import asyncio
import unittest
from unittest.mock import AsyncMock

from app.memory_store import InMemoryStore
from app.reconciliation_service import _REST_FALLBACK_LAST, _rest_exit_fallback_position
from app.trade_engine import Position, TradeEngine


class _Registry:
    def __init__(self, engine: TradeEngine) -> None:
        self.engine = engine

    async def get(self, _user_id: int) -> TradeEngine:
        return self.engine


class ReconciliationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _REST_FALLBACK_LAST.clear()

    async def asyncTearDown(self) -> None:
        _REST_FALLBACK_LAST.clear()

    async def test_rest_exit_fallback_feeds_stale_position_into_exit_logic(self) -> None:
        store = InMemoryStore()
        position = Position(
            trade_id="rest-exit",
            user_id=1,
            symbol="SBIN",
            alert_name="classic",
            side="BUY",
            product="MIS",
            qty=1,
            initial_qty=1,
            entry_price=100.0,
            target_price=110.0,
            sl_price=95.0,
            tsl_pct=0.0,
            status="OPEN",
        )
        await store.upsert_position(1, "SBIN", position.to_public())
        await store.mark_open(1, "SBIN", "rest-exit")

        engine = TradeEngine(1, store)
        engine.broker = "DHAN"
        engine._fetch_ltp = AsyncMock(return_value=110.5)
        engine._exit_position = AsyncMock()

        await _rest_exit_fallback_position(store, _Registry(engine), 1, position.to_public())
        await asyncio.sleep(0)

        engine._fetch_ltp.assert_awaited_once_with("SBIN", prefer_cache=False)
        engine._exit_position.assert_awaited_once_with("SBIN", "TARGET")
        self.assertEqual(engine.positions["SBIN"].status, "EXITING")

    async def test_rest_exit_fallback_skips_when_websocket_tick_is_fresh(self) -> None:
        store = InMemoryStore()
        position = Position(
            trade_id="fresh-ws",
            user_id=1,
            symbol="SBIN",
            alert_name="classic",
            side="BUY",
            product="MIS",
            qty=1,
            initial_qty=1,
            entry_price=100.0,
            target_price=110.0,
            sl_price=95.0,
            status="OPEN",
        )
        await store.save_latest_tick(1, "SBIN", {"ltp": 105.0, "source": "DHAN_WS"}, ttl_sec=30)
        engine = TradeEngine(1, store)
        engine._fetch_ltp = AsyncMock(return_value=110.5)

        await _rest_exit_fallback_position(store, _Registry(engine), 1, position.to_public())

        engine._fetch_ltp.assert_not_awaited()

    async def test_rest_exit_fallback_marks_open_position_with_ws_warning(self) -> None:
        store = InMemoryStore()
        position = Position(
            trade_id="rest-monitor",
            user_id=1,
            symbol="SBIN",
            alert_name="classic",
            side="BUY",
            product="MIS",
            qty=1,
            initial_qty=1,
            entry_price=100.0,
            target_price=120.0,
            sl_price=95.0,
            status="OPEN",
        )
        await store.upsert_position(1, "SBIN", position.to_public())
        engine = TradeEngine(1, store)
        engine.broker = "DHAN"
        engine._fetch_ltp = AsyncMock(return_value=105.0)

        await _rest_exit_fallback_position(store, _Registry(engine), 1, position.to_public())

        stored = [row for row in await store.list_positions(1) if row.get("symbol") == "SBIN"][0]
        self.assertEqual(stored["pending_reason"], "WS_TICK_MISSING_REST_FALLBACK")
        self.assertEqual(stored["ltp"], 105.0)
        self.assertEqual(stored["pnl"], 5.0)


if __name__ == "__main__":
    unittest.main()
