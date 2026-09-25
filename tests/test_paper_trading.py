import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.memory_store import InMemoryStore
from app.reconciliation_service import _reconcile_position
from app.trade_engine import TradeEngine


class PaperTradingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryStore()
        self.engine = TradeEngine(1, self.store)
        self.engine.broker = "DHAN"
        self.guard = patch("app.trade_engine._is_test_mode", return_value=True)
        self.guard.start()
        self.engine._wait_for_entry_feed_ready = AsyncMock(return_value=(True, "", 100.0))
        self.engine._fetch_ltp = AsyncMock(return_value=100.0)
        self.engine._place_order = AsyncMock(side_effect=AssertionError("REAL_ORDER_FORBIDDEN"))
        self.engine._broker_positions = AsyncMock(side_effect=AssertionError("BROKER_POSITIONS_FORBIDDEN"))
        self.engine._broker_holdings = AsyncMock(side_effect=AssertionError("BROKER_HOLDINGS_FORBIDDEN"))
        self.config = {"alert_name": "PAPER", "enabled": True, "paper_trading": True,
                       "entry_start_time": "00:00", "entry_end_time": "23:59", "qty_mode": "QTY",
                       "qty": 2, "direction": "LONG", "target_pct": 2, "stop_loss_pct": 1,
                       "trailing_sl_enabled": False, "product": "MIS", "trade_limit_per_day": 10}

    async def asyncTearDown(self):
        self.engine._place_order.assert_not_awaited()
        self.engine._broker_positions.assert_not_awaited()
        self.engine._broker_holdings.assert_not_awaited()
        await self.engine.close()
        await self.store.close()
        self.guard.stop()

    async def enter(self, **changes):
        self.config.update(changes)
        await self.store.save_alert_config(1, self.config)
        result = await self.engine.on_chartink_alert("PAPER", ["TEST"])
        self.assertEqual(result[0]["reason"], "PAPER_ORDER_EXECUTED", result)
        pos = self.engine.positions["TEST"]
        self.assertTrue(pos.paper_trading)
        self.assertTrue(pos.entry_order_id.startswith("PAPER-"))
        return pos

    async def tick(self, price):
        await self.store.save_latest_tick(1, "TEST", {"ltp": price, "source": "DHAN_WS"})
        await self.engine.on_tick("TEST", price, 100, price, price, 0, 0)
        for _ in range(30):
            if not self.engine._exit_inflight.get("TEST"):
                break
            await asyncio.sleep(0.01)
        return await self.store.get_position(1, "TEST")

    async def test_target_closes_paper_and_records_realized_pnl(self):
        await self.enter()
        row = await self.tick(103)
        self.assertEqual(row["status"], "CLOSED", row)
        self.assertEqual(row["pnl"], 6)
        self.assertEqual(row["realized_pnl"], 6)
        self.assertEqual(row["qty"], 0)
        self.assertFalse(await self.store.get_open(1, "TEST"))

    async def test_short_stop_closes_with_loss(self):
        await self.enter(direction="SHORT")
        row = await self.tick(102)
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["pnl"], -4)

    async def test_short_target(self):
        await self.enter(direction="SHORT")
        row = await self.tick(97)
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["pnl"], 6)

    async def test_toggle_change_cannot_convert_existing_position_to_live(self):
        await self.enter()
        await self.store.save_alert_config(1, dict(self.config, paper_trading=False))
        row = await self.tick(103)
        self.assertTrue(row["paper_trading"])
        self.assertTrue(row["exit_order_id"].startswith("PAPER-"))

    async def test_pyramid_stays_paper_and_updates_average(self):
        await self.enter(target_pct=10, pyramid_enabled=True, pyramid_step_pct=1, pyramid_max_adds=2)
        row = await self.tick(102)
        self.assertEqual(row["qty"], 4)
        self.assertEqual(row["entry_price"], 101)
        self.assertTrue(row["pyramid_last_order_id"].startswith("PAPER-"))

    async def test_trailing_stop_ratchets_then_exits(self):
        await self.enter(target_pct=10, trailing_sl_enabled=True, trailing_sl_pct=1)
        raised = await self.tick(103)
        line = raised["trail_price"]
        self.assertGreater(line, 100)
        lower = await self.tick(102.5)
        self.assertGreaterEqual(lower["trail_price"], line)
        closed = await self.tick(101)
        self.assertEqual(closed["status"], "CLOSED")

    async def test_cost_sl_exits_at_cost_after_trigger(self):
        await self.enter(target_pct=10, cost_sl_enabled=True, cost_sl_rr=2)
        row = await self.tick(103)
        self.assertEqual(row["sl_price"], 100)
        row = await self.tick(100)
        self.assertEqual(row["status"], "CLOSED")

    async def test_partial_profit_is_simulated(self):
        pos = await self.enter(target_pct=10)
        await self.store.save_latest_tick(1, "TEST", {"ltp": 103})
        self.assertTrue(await self.engine._book_partial_profit(pos, "TP1", 1))
        self.assertEqual(pos.qty, 1)
        self.assertEqual(pos.realized_pnl, 3)

    async def test_alert_exit_uses_owning_strategy(self):
        await self.enter(exit_alert_enabled=True, exit_alert_name="PAPER EXIT")
        await self.store.save_latest_tick(1, "TEST", {"ltp": 104})
        result = await self.engine.on_chartink_alert("PAPER EXIT", ["TEST", "OTHER"])
        self.assertEqual(result[0]["status"], "EXITED")
        self.assertEqual(result[1]["reason"], "NO_OPEN_POSITION")

    async def test_restart_restores_paper_mode_and_manual_exit(self):
        await self.enter()
        self.engine.positions.clear()
        await self.engine.rehydrate_open_positions()
        await self.store.save_latest_tick(1, "TEST", {"ltp": 101})
        result = await self.engine.manual_squareoff_zerodha("TEST")
        self.assertEqual(result["status"], "EXIT_TRIGGERED")
        row = await self.store.get_position(1, "TEST")
        self.assertEqual(row["pnl"], 2)
        self.assertEqual((await self.engine.manual_squareoff_zerodha("TEST"))["reason"], "NO_ACTIVE_PAPER_POSITION")

    async def test_restart_recovers_interrupted_paper_exit(self):
        pos = await self.enter()
        pos.status = "EXITING"
        await self.engine._persist_position_state(pos)
        self.engine.positions.clear()
        await self.engine.rehydrate_open_positions()
        self.assertEqual(self.engine.positions["TEST"].status, "OPEN")
        self.assertEqual((await self.store.get_position(1, "TEST"))["status"], "OPEN")
        self.assertEqual((await self.tick(103))["status"], "CLOSED")

    async def test_cnc_carry_restores_without_holdings_api(self):
        await self.enter(product="CNC")
        self.engine.positions.clear()
        self.store._positions.clear()
        restored = await self.engine.rehydrate_cnc_carry_positions()
        self.assertEqual(restored, ["TEST"])
        self.assertTrue(self.engine.positions["TEST"].paper_trading)
        await self.tick(103)
        self.assertEqual(await self.store.list_cnc_carry_positions(1), [])

    async def test_reconciliation_does_not_compare_paper_with_demat(self):
        pos = await self.enter()
        registry = AsyncMock()
        await _reconcile_position(self.store, registry, 1, pos.to_public())
        registry.get.assert_not_awaited()

    async def test_broker_order_update_does_not_change_paper(self):
        pos = await self.enter()
        await self.engine.on_order_update({"tradingsymbol": "TEST", "order_id": "live123",
                                          "status": "COMPLETE", "filled_quantity": 999})
        self.assertEqual(pos.qty, 2)

    async def test_duplicate_guard_applies_across_modes(self):
        await self.enter()
        await self.store.save_alert_config(1, dict(self.config, paper_trading=False))
        result = await self.engine.on_chartink_alert("PAPER", ["TEST"])
        self.assertEqual(result[0]["reason"], "ALREADY_OPEN")

    async def test_last_line_broker_guard(self):
        await self.enter()
        with self.assertRaisesRegex(RuntimeError, "PAPER_ORDER_BROKER_SUBMISSION_BLOCKED"):
            await TradeEngine._place_order(self.engine, "TEST", "BUY", 1, "MIS")
        self.engine.positions.clear()
        with self.assertRaisesRegex(RuntimeError, "PAPER_ORDER_BROKER_SUBMISSION_BLOCKED"):
            await TradeEngine._place_order(self.engine, "test", "SELL", 1, "MIS")

    async def test_missing_paper_quote_never_falls_through_to_broker(self):
        self.engine._fetch_ltp.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "PAPER_FRESH_PRICE_REQUIRED"):
            await self.engine._place_order_with_execution("TEST", "BUY", 2, "MIS", {"paper_trading": True})

    async def test_turnover_missing_skips_before_price_fetch(self):
        await self.store.save_alert_config(1, dict(self.config, turnover_filter_on=True, turnover_top_n=10))
        result = await self.engine.on_chartink_alert("PAPER", ["TEST"])
        self.assertEqual(result[0]["reason"], "TURNOVER_RANK_STALE")
        self.engine._wait_for_entry_feed_ready.assert_not_awaited()

    async def test_paper_price_failure_does_not_activate_live_kill_switch(self):
        await self.enter()
        self.engine._fetch_ltp.return_value = 0
        await self.engine._exit_position("TEST", "MANUAL")
        self.assertFalse(await self.store.is_kill(1))
        self.assertEqual(self.engine.positions["TEST"].status, "OPEN")
        self.assertEqual(self.engine.positions["TEST"].pending_reason, "PAPER_EXIT_PRICE_UNAVAILABLE")

    async def test_live_order_path_remains_live(self):
        self.engine._place_order.side_effect = None
        self.engine._place_order.return_value = "REAL123"
        self.engine._wait_for_order_execution = AsyncMock(return_value={
            "status": "COMPLETE", "filled_quantity": 2, "remaining_quantity": 0, "average_price": 100})
        self.engine._reconcile_dhan_execution_snapshot = AsyncMock(return_value=({
            "status": "COMPLETE", "filled_quantity": 2, "remaining_quantity": 0, "average_price": 100}, 2))
        self.engine._fetch_broker_symbol_qty = AsyncMock(return_value=0)
        await self.store.save_alert_config(1, dict(self.config, paper_trading=False))
        result = await self.engine.on_chartink_alert("PAPER", ["TEST"])
        self.assertEqual(result[0]["reason"], "ORDER_EXECUTED", result)
        self.assertFalse(self.engine.positions["TEST"].paper_trading)
        self.engine._place_order.assert_awaited_once()
        self.engine._place_order.reset_mock()

    async def test_ready_turnover_allows_paper_order_and_short_uses_same_rank(self):
        from app.turnover import TurnoverBook
        from app.alert_features import IST
        from datetime import datetime
        now = datetime(2026, 9, 24, 10, 0, tzinfo=IST).timestamp()
        book = TurnoverBook({"TEST": "101"}, now)
        book.connected = True
        book.update("101", 100, 1000, now, trade_at=now, now=now)
        await self.store.save_turnover_snapshot(1, book.snapshot(now))
        with patch("app.turnover.time.time", return_value=now):
            pos = await self.enter(turnover_filter_on=True, turnover_top_n=1, direction="SHORT")
        self.assertEqual(pos.side, "SELL")

    async def test_turnover_rechecked_immediately_before_order(self):
        from app.turnover import TurnoverBook
        from app.alert_features import IST
        from datetime import datetime
        now = datetime(2026, 9, 24, 10, 0, tzinfo=IST).timestamp()
        book = TurnoverBook({"TEST": "101"}, now)
        book.connected = True
        book.update("101", 100, 1000, now, trade_at=now, now=now)
        snapshot = book.snapshot(now)
        self.store.load_turnover_snapshot = AsyncMock(side_effect=[snapshot, {}])
        await self.store.save_alert_config(1, dict(self.config, turnover_filter_on=True, turnover_top_n=1))
        with patch("app.turnover.time.time", return_value=now):
            result = await self.engine.on_chartink_alert("PAPER", ["TEST"])
        self.assertIn("TURNOVER_RANK_STALE", result[0]["reason"])
        self.assertNotIn("TEST", self.engine.positions)
