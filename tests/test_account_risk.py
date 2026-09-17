import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.memory_store import InMemoryStore
from app.trade_engine import Position, TradeEngine


def position(symbol, product):
    return Position(trade_id=f"risk-{symbol}", user_id=1, symbol=symbol,
                    alert_name="risk-test", side="BUY", product=product,
                    qty=2, entry_price=100, status="OPEN")


class AccountRiskTests(unittest.IsolatedAsyncioTestCase):
    async def run_monitor_once(self, engine):
        with patch("app.trade_engine.asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):
                await engine._pnl_exit_monitor()

    async def test_pnl_breach_blocks_entries_before_exiting_mis_and_cnc(self):
        for mtm in [250, -250]:
            with self.subTest(mtm=mtm):
                store = InMemoryStore()
                await store.set_pnl_exit_config(1, {"enabled": True, "max_profit": 200, "max_loss": 200})
                engine = TradeEngine(1, store)
                engine.positions = {"MISSTOCK": position("MISSTOCK", "MIS"),
                                    "CNCSTOCK": position("CNCSTOCK", "CNC")}
                engine._ensure_broker_ready = AsyncMock(return_value=True)
                engine._broker_positions = AsyncMock(return_value={"net": [{"pnl": mtm}]})
                exited = []

                async def exit_position(symbol, reason):
                    self.assertTrue(await store.is_kill(1))
                    self.assertIn("PNL_EXIT:", reason)
                    exited.append(symbol)

                engine._exit_position = AsyncMock(side_effect=exit_position)
                await self.run_monitor_once(engine)
                self.assertEqual(set(exited), {"MISSTOCK", "CNCSTOCK"})

    async def test_pnl_disabled_zero_limits_and_unbreached_do_not_exit(self):
        for enabled, profit, loss in [(False, 10, 10), (True, 0, 0), (True, 200, 200)]:
            store = InMemoryStore()
            await store.set_pnl_exit_config(1, {"enabled": enabled, "max_profit": profit, "max_loss": loss})
            engine = TradeEngine(1, store)
            engine._ensure_broker_ready = AsyncMock(return_value=True)
            engine._broker_positions = AsyncMock(return_value={"net": [{"pnl": 100}]})
            engine.trigger_kill_switch = AsyncMock()
            await self.run_monitor_once(engine)
            engine.trigger_kill_switch.assert_not_awaited()

    async def test_squareoff_restores_managed_cnc_missing_from_broker_day_positions(self):
        store = InMemoryStore()
        saved = position("CARRYSTOCK", "CNC")
        await store.upsert_position(1, saved.symbol, saved.to_public())
        await store.mark_open(1, saved.symbol, saved.trade_id)
        engine = TradeEngine(1, store)
        engine._ensure_broker_ready = AsyncMock(return_value=True)
        engine._broker_positions = AsyncMock(return_value={"net": [], "day": []})
        engine._exit_position = AsyncMock()
        result = await engine.squareoff_all_positions(reason="PNL_EXIT", products={"MIS", "CNC"})
        self.assertEqual(result["count"], 1)
        engine._exit_position.assert_awaited_once_with("CARRYSTOCK", "PNL_EXIT")
        self.assertEqual(engine.positions["CARRYSTOCK"].qty, 2)

    async def test_squareoff_failure_keeps_local_kill_enabled(self):
        store = InMemoryStore()
        engine = TradeEngine(1, store)
        engine.squareoff_all_positions = AsyncMock(side_effect=RuntimeError("broker unavailable"))
        result = await engine.trigger_kill_switch("PNL_EXIT", products={"MIS", "CNC"})
        self.assertTrue(await store.is_kill(1))
        self.assertFalse(result["squareoff"]["ok"])

    async def test_kill_switch_blocks_pyramid_adds(self):
        store = InMemoryStore()
        await store.set_kill(1, True)
        engine = TradeEngine(1, store)
        pos = position("CNCSTOCK", "CNC")
        pos.pyramid_enabled = True
        pos.pyramid_step_pct = 1
        pos.pyramid_base_qty = 2
        pos.pyramid_max_adds = 3
        engine._place_order_with_execution = AsyncMock()
        self.assertFalse(await engine._maybe_pyramid_position(pos, 102))
        engine._place_order_with_execution.assert_not_awaited()
