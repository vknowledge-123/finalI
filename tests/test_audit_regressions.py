import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.memory_store import InMemoryStore
from app.reconciliation_service import _reconcile_position
from app.trade_engine import OrderExecution, Position, TradeEngine


class AuditRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryStore()
        self.engine = TradeEngine(1, self.store)
        self.pos = Position(
            trade_id="audit", user_id=1, symbol="SBIN", alert_name="classic",
            side="BUY", product="MIS", qty=5, initial_qty=7, entry_price=100,
            ltp=110, realized_pnl=6, pnl=56, status="OPEN",
        )
        self.engine.positions["SBIN"] = self.pos
        await self.store.upsert_position(1, "SBIN", self.pos.to_public())
        await self.store.mark_open(1, "SBIN", "audit")

    async def asyncTearDown(self):
        await self.engine.close()
        await self.store.close()

    def fill(self, qty, price):
        self.engine._place_order_with_execution = AsyncMock(return_value=OrderExecution(
            order_id="exit", symbol="SBIN", side="SELL", qty=qty,
            filled_qty=qty, avg_price=price, status="COMPLETE",
        ))

    async def test_live_exit_uses_fill_price_and_preserves_earlier_realized_profit(self):
        self.fill(5, 109)
        await self.engine._exit_position("SBIN", "TARGET")
        row = await self.store.get_position(1, "SBIN")
        self.assertEqual(row["realized_pnl"], 51)
        self.assertEqual(row["pnl"], 51)
        self.assertEqual(row["qty"], 0)

    async def test_partial_exit_counts_remaining_unrealized_once(self):
        for paper in (False, True):
            with self.subTest(paper=paper):
                self.pos.qty, self.pos.realized_pnl, self.pos.pnl = 5, 6, 56
                self.pos.paper_trading = paper
                self.pos.status = "OPEN"
                await self.store.upsert_position(1, "SBIN", self.pos.to_public())
                self.fill(2, 109)
                await self.engine._exit_position("SBIN", "TARGET")
                row = await self.store.get_position(1, "SBIN")
                self.assertEqual(row["qty"], 3)
                self.assertEqual(row["realized_pnl"], 24)
                self.assertEqual(row["pnl"], 54)

    async def test_short_exit_fill_accounting(self):
        self.pos.side = "SELL"
        await self.store.upsert_position(1, "SBIN", self.pos.to_public())
        self.fill(5, 95)
        await self.engine._exit_position("SBIN", "TARGET")
        self.assertEqual((await self.store.get_position(1, "SBIN"))["pnl"], 31)

    async def test_custom_partial_close_updates_pnl_without_waiting_for_next_tick(self):
        self.fill(5, 109)
        self.assertTrue(await self.engine._book_partial_profit(self.pos, "TP3", 5))
        row = await self.store.get_position(1, "SBIN")
        self.assertEqual(row["pnl"], 51)
        self.assertEqual(row["realized_pnl"], 51)

    async def test_reconciliation_does_not_overwrite_newer_closed_snapshot(self):
        async def broker_qty(*args, **kwargs):
            await self.store.upsert_position(1, "SBIN", dict(
                self.pos.to_public(), status="CLOSED", qty=0, pnl=51,
                realized_pnl=51, updated_ts=123,
            ))
            return 3
        self.engine._fetch_broker_symbol_qty = broker_qty
        registry = SimpleNamespace(get=AsyncMock(return_value=self.engine))
        await _reconcile_position(self.store, registry, 1, self.pos.to_public())
        row = await self.store.get_position(1, "SBIN")
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["qty"], 0)
        self.assertEqual(row["pnl"], 51)

    async def test_reconciliation_does_not_adopt_unrelated_extra_shares(self):
        self.engine._fetch_broker_symbol_qty = AsyncMock(return_value=15)
        registry = SimpleNamespace(get=AsyncMock(return_value=self.engine))
        await _reconcile_position(self.store, registry, 1, self.pos.to_public())
        row = await self.store.get_position(1, "SBIN")
        self.assertEqual(row["qty"], 5)
        self.assertIn("BROKER_QTY_EXCESS", row["pending_reason"])

    async def test_late_entry_updates_do_not_overwrite_confirmed_position(self):
        self.pos.entry_order_id = "entry"
        for status in ("COMPLETE", "CANCELLED", "PARTIAL"):
            await self.engine.on_order_update({
                "order_id": "entry", "tradingsymbol": "SBIN", "status": status,
                "filled_quantity": 2, "quantity": 3, "average_price": 90,
            })
            self.assertEqual(self.pos.entry_price, 100)
            self.assertEqual(self.pos.status, "OPEN")
            self.assertEqual(self.pos.qty, 5)
        self.assertIn("entry", self.engine._order_updates_by_id)

    async def test_late_exit_updates_do_not_discard_remaining_shares(self):
        self.pos.exit_order_id = "earlier-partial"
        await self.engine.on_order_update({
            "order_id": "earlier-partial", "tradingsymbol": "SBIN", "status": "COMPLETE",
            "filled_quantity": 2, "quantity": 2, "average_price": 110,
        })
        self.assertEqual(self.pos.qty, 5)
        self.assertEqual(self.pos.realized_pnl, 6)

    async def test_exit_recovery_requires_nonzero_fill_and_uses_actual_price(self):
        self.pos.status, self.pos.exit_order_id = "EXITING", "recovery"
        await self.store.upsert_position(1, "SBIN", self.pos.to_public())
        packet = dict(order_id="recovery", tradingsymbol="SBIN", status="COMPLETE",
                      filled_quantity=0, quantity=5, average_price=109)
        await self.engine.on_order_update(packet)
        self.assertEqual(self.pos.qty, 5)
        packet["filled_quantity"] = 5
        await self.engine.on_order_update(packet)
        await self.engine.on_order_update(packet)
        row = await self.store.get_position(1, "SBIN")
        self.assertEqual(row["pnl"], 51)
        self.assertEqual(row["realized_pnl"], 51)
        self.assertEqual(row["status"], "CLOSED")

    async def test_reconciliation_defers_while_exit_owns_lock(self):
        self.engine._fetch_broker_symbol_qty = AsyncMock(return_value=0)
        registry = SimpleNamespace(get=AsyncMock(return_value=self.engine))
        await self.store.acquire_lock(1, "SBIN", "exit")
        try:
            await _reconcile_position(self.store, registry, 1, self.pos.to_public())
            self.engine._fetch_broker_symbol_qty.assert_not_awaited()
        finally:
            await self.store.release_lock(1, "SBIN", "exit")

    async def test_only_one_process_can_pyramid_same_position(self):
        self.pos.pyramid_enabled = True
        self.pos.pyramid_base_qty = 1
        self.pos.pyramid_max_adds = 3
        self.pos.pyramid_step_pct = 1
        await self.store.set_auto_sq_off_enabled(1, False)
        await self.store.upsert_position(1, "SBIN", self.pos.to_public())
        other = TradeEngine(1, self.store)
        other_pos = Position(**self.pos.to_public())
        other.positions["SBIN"] = other_pos
        entered, release = asyncio.Event(), asyncio.Event()

        async def fill(*args, **kwargs):
            entered.set()
            await release.wait()
            return OrderExecution(order_id="add", symbol="SBIN", side="BUY",
                                  qty=1, filled_qty=1, avg_price=102, status="COMPLETE")
        self.engine._place_order_with_execution = AsyncMock(side_effect=fill)
        other._place_order_with_execution = AsyncMock()
        task = asyncio.create_task(self.engine._maybe_pyramid_position(self.pos, 102))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertFalse(await other._maybe_pyramid_position(other_pos, 102))
            release.set()
            self.assertTrue(await task)
            # Even after lock release, the stale engine must reload before adding.
            self.assertFalse(await other._maybe_pyramid_position(other_pos, 102))
            other._place_order_with_execution.assert_not_awaited()
        finally:
            release.set()
            await task
            await other.close()

    async def test_custom_profit_target_is_not_booked_again_by_stale_engine(self):
        await self.store.upsert_position(1, "SBIN", dict(
            self.pos.to_public(), qty=3, realized_pnl=24, tp1_booked=True,
        ))
        self.fill(2, 109)
        self.assertTrue(await self.engine._book_partial_profit(self.pos, "TP1", 2))
        self.engine._place_order_with_execution.assert_not_awaited()
        self.assertEqual(self.pos.qty, 3)

    async def test_final_exit_preserves_profit_booked_by_other_service(self):
        await self.store.upsert_position(1, "SBIN", dict(
            self.pos.to_public(), qty=3, realized_pnl=24, exit_filled_qty=4,
        ))
        self.fill(3, 109)
        await self.engine._exit_position("SBIN", "TARGET")
        row = await self.store.get_position(1, "SBIN")
        self.assertEqual(row["pnl"], 51)
        self.assertEqual(row["exit_filled_qty"], 7)
