import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.dhan_broker import normalize_dhan_positions, normalize_dhan_holdings
from app.memory_store import InMemoryStore
from app.trade_engine import TradeEngine, Position, OrderExecution
from app.reconciliation_service import _reconcile_position, _rest_exit_fallback_position
from app import market_feed_service as mfs


class ServiceRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_zero_exit_fill_does_not_close_position(self):
        store = InMemoryStore()
        engine = TradeEngine(1, store)
        pos = Position(trade_id="zero-fill", user_id=1, symbol="SBIN", alert_name="classic",
                       side="BUY", product="MIS", qty=5, initial_qty=5, entry_price=100,
                       target_price=110, sl_price=95, status="OPEN")
        engine.positions["SBIN"] = pos
        await store.upsert_position(1, "SBIN", pos.to_public())
        await store.mark_open(1, "SBIN", "zero-fill")
        engine._place_order_with_execution = AsyncMock(return_value=OrderExecution(
            order_id="O1", symbol="SBIN", side="SELL", qty=5, status="PENDING", filled_qty=0))
        await engine._exit_position("SBIN", "TARGET")
        self.assertEqual(pos.qty, 5)
        self.assertNotEqual(pos.status, "CLOSED")
        self.assertEqual(await store.get_open(1, "SBIN"), "zero-fill")

    async def test_net_and_day_are_not_added_and_products_stay_separate(self):
        engine = TradeEngine(1, InMemoryStore())
        net = [{"tradingsymbol": "SBIN", "quantity": 5, "product": "MIS"},
               {"tradingsymbol": "SBIN", "quantity": 8, "product": "CNC"}]
        engine._broker_positions = AsyncMock(return_value={"net": net, "day": net})
        self.assertEqual(await engine._fetch_broker_symbol_qty("SBIN"), 13)
        self.assertEqual(await engine._fetch_broker_symbol_qty("SBIN", product="MIS"), 5)
        engine._broker_positions.return_value = {"net": [], "day": net}
        self.assertEqual(await engine._fetch_broker_symbol_qty("SBIN"), 0)

    def test_failed_broker_response_is_not_an_empty_portfolio(self):
        for normalize in (normalize_dhan_positions, normalize_dhan_holdings):
            with self.assertRaises(RuntimeError):
                normalize({"status": "failure", "remarks": "token expired", "data": []})
        with self.assertRaises(ValueError):
            normalize_dhan_positions({"unexpected": "response"})
        self.assertEqual(normalize_dhan_positions({"data": [{"tradingSymbol": "SBIN", "netQty": 0, "quantity": 10}]})["net"][0]["quantity"], 0)

    async def test_closed_broker_position_stays_closed_and_keeps_history(self):
        store = InMemoryStore()
        pos = Position(trade_id="T1", user_id=1, symbol="SBIN", alert_name="classic",
                       side="BUY", product="MIS", qty=5, initial_qty=5, entry_price=100,
                       target_price=110, sl_price=95, status="OPEN")
        row = pos.to_public()
        await store.upsert_position(1, "SBIN", row)
        await store.mark_open(1, "SBIN", "T1")
        engine = TradeEngine(1, store)
        engine.positions["SBIN"] = pos
        engine._fetch_broker_symbol_qty = AsyncMock(return_value=0)
        engine._fetch_ltp = AsyncMock(return_value=120)
        registry = SimpleNamespace(get=AsyncMock(return_value=engine))
        await _reconcile_position(store, registry, 1, row)
        await _rest_exit_fallback_position(store, registry, 1, row)
        engine._fetch_ltp.assert_not_awaited()
        self.assertNotIn("SBIN", engine.positions)
        saved = (await store.list_positions(1))[0]
        self.assertEqual(saved["status"], "CLOSED")
        self.assertEqual(saved["exit_reason"], "BROKER_POSITION_CLOSED")
        self.assertEqual(await store.get_open(1, "SBIN"), "")

    async def test_intraday_reconciliation_does_not_delete_cnc_holdings(self):
        store = InMemoryStore()
        row = {"symbol": "SBIN", "qty": 10, "product": "CNC", "status": "OPEN"}
        await store.upsert_position(1, "SBIN", row)
        registry = SimpleNamespace(get=AsyncMock())
        await _reconcile_position(store, registry, 1, row)
        registry.get.assert_not_awaited()
        self.assertEqual((await store.list_positions(1))[0]["qty"], 10)

    def execution_engine(self, snapshots):
        store = InMemoryStore()
        engine = TradeEngine(1, store)
        engine.broker = "DHAN"
        engine._fetch_broker_symbol_qty = AsyncMock(return_value=0)
        engine._place_order = AsyncMock(side_effect=["O1", "O2"])
        engine._wait_for_order_execution = AsyncMock(side_effect=snapshots)
        engine._cancel_order_if_pending = AsyncMock(return_value=True)

        async def reconcile(oid, symbol, side, qty, before, snapshot):
            return snapshot, None
        engine._reconcile_dhan_execution_snapshot = reconcile
        return store, engine

    async def test_unconfirmed_cancellation_never_places_replacement(self):
        store, engine = self.execution_engine([{"status": "PENDING"}, {"status": "PENDING"}])
        engine._cancel_order_if_pending.return_value = False
        with self.assertRaisesRegex(RuntimeError, "ORDER_CANCEL_UNCONFIRMED"):
            await engine._place_order_with_execution("SBIN", "BUY", 10, "MIS")
        self.assertEqual(engine._place_order.await_count, 1)
        self.assertTrue(await store.is_kill(1))

    async def test_fill_during_cancel_reduces_replacement_quantity(self):
        store, engine = self.execution_engine([
            {"status": "PENDING", "filled_quantity": 0},
            {"status": "CANCELLED", "filled_quantity": 2, "average_price": 100},
            {"status": "COMPLETE", "filled_quantity": 8, "average_price": 101},
        ])
        execution = await engine._place_order_with_execution("SBIN", "BUY", 10, "MIS")
        self.assertEqual([c.args[2] for c in engine._place_order.await_args_list], [10, 8])
        self.assertEqual(execution.filled_qty, 10)
        self.assertAlmostEqual(execution.avg_price, 100.8)
        self.assertFalse(await store.is_kill(1))

    async def test_bad_subscription_job_does_not_stop_service(self):
        redis = SimpleNamespace(llen=AsyncMock(return_value=4), lpop=AsyncMock(side_effect=[
            "[]", json.dumps({"user_id": "bad", "symbols": ["SBIN"]}),
            json.dumps({"user_id": 1, "symbols": "SBIN"}),
            json.dumps({"user_id": 1, "symbols": ["SBIN"]}), None,
        ]))
        main = SimpleNamespace(subscribe_symbols_for_user=AsyncMock(return_value={"sent": True}))
        with patch.object(mfs, "_ensure_user_feed_started", AsyncMock()), \
             patch.object(mfs, "_confirm_dhan_alert_symbol_ticks", AsyncMock()):
            await mfs._drain_subscription_requests(main, SimpleNamespace(redis=redis), {1})
        main.subscribe_symbols_for_user.assert_awaited_once_with(1, ["SBIN"])
