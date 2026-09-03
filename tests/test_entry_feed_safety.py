import os
import unittest
from unittest.mock import AsyncMock

from app.memory_store import InMemoryStore
from app.trade_engine import OrderExecution, TradeEngine


class EntryFeedSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._old_env = {
            "APP_TESTING": os.environ.get("APP_TESTING"),
            "APP_ENV": os.environ.get("APP_ENV"),
            "REQUIRE_BROKER_FEED_FOR_ENTRY": os.environ.get("REQUIRE_BROKER_FEED_FOR_ENTRY"),
            "ENTRY_FEED_READY_TIMEOUT_SEC": os.environ.get("ENTRY_FEED_READY_TIMEOUT_SEC"),
            "ENTRY_FRESH_TICK_MAX_AGE_SEC": os.environ.get("ENTRY_FRESH_TICK_MAX_AGE_SEC"),
        }
        os.environ.pop("APP_TESTING", None)
        os.environ["APP_ENV"] = "production"
        os.environ["REQUIRE_BROKER_FEED_FOR_ENTRY"] = "1"
        os.environ["ENTRY_FEED_READY_TIMEOUT_SEC"] = "0"
        os.environ["ENTRY_FRESH_TICK_MAX_AGE_SEC"] = "5"

    async def asyncTearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    async def _engine_with_config(self) -> tuple[InMemoryStore, TradeEngine]:
        store = InMemoryStore()
        await store.save_alert_config(
            1,
            {
                "alert_name": "FEED_TEST",
                "enabled": True,
                "entry_start_time": "00:00",
                "entry_end_time": "23:59",
                "direction": "LONG",
                "product": "MIS",
                "qty_mode": "QTY",
                "qty": 1,
            },
        )
        await store.save_broker(1, "DHAN")
        engine = TradeEngine(user_id=1, store=store)
        engine.broker = "DHAN"
        return store, engine

    async def test_entry_is_blocked_when_broker_feed_health_is_missing(self) -> None:
        _store, engine = await self._engine_with_config()
        engine._fetch_ltp = AsyncMock(return_value=100.0)
        engine._place_order_with_execution = AsyncMock()

        result = await engine.on_chartink_alert("FEED_TEST", ["SBIN"])

        self.assertEqual(result[0]["status"], "SKIPPED")
        self.assertTrue(result[0]["reason"].startswith("BROKER_FEED_NOT_CONNECTED"))
        engine._fetch_ltp.assert_not_awaited()
        engine._place_order_with_execution.assert_not_awaited()

    async def test_entry_uses_fresh_websocket_tick_before_order(self) -> None:
        store, engine = await self._engine_with_config()
        await store.save_broker_feed_health(1, "DHAN", True, ttl_sec=15, detail="test")
        await store.save_latest_tick(1, "SBIN", {"ltp": 100.0}, ttl_sec=30)
        engine._fetch_ltp = AsyncMock(return_value=0.0)
        engine._place_order_with_execution = AsyncMock(
            return_value=OrderExecution(
                order_id="OID1",
                symbol="SBIN",
                side="BUY",
                qty=1,
                status="COMPLETE",
                avg_price=100.0,
                filled_qty=1,
            )
        )

        result = await engine.on_chartink_alert("FEED_TEST", ["SBIN"])

        self.assertEqual(result[0]["status"], "ENTERED")
        self.assertIn(result[0]["reason"], {"ORDER_OK", "ORDER_EXECUTED"})
        engine._fetch_ltp.assert_not_awaited()
        engine._place_order_with_execution.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
