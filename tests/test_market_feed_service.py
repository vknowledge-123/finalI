import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app import market_feed_service as mfs


class _FakeStore:
    def __init__(self, latest_tick=None) -> None:
        self.latest_tick = latest_tick or {}
        self.health_updates = []
        self.feed_health = {"connected": True, "detail": "tick"}

    async def load_broker(self, _user_id: int) -> str:
        return "DHAN"

    async def load_latest_tick(self, _user_id: int, _symbol: str):
        return dict(self.latest_tick)

    async def save_broker_feed_health(self, user_id: int, broker: str, connected: bool, ttl_sec: int, detail: str):
        self.health_updates.append(
            {
                "user_id": user_id,
                "broker": broker,
                "connected": connected,
                "ttl_sec": ttl_sec,
                "detail": detail,
            }
        )

    async def load_broker_feed_health(self, _user_id: int, _broker: str):
        return dict(self.feed_health)


class _FakeMain:
    def __init__(self) -> None:
        self.restart_selected_feed = AsyncMock()
        self.DHAN_CONNECTED = True
        self.DHAN_USER_ID = 1


class MarketFeedServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        mfs._DHAN_NO_TICK_RESTART.clear()

    async def asyncTearDown(self) -> None:
        mfs._DHAN_NO_TICK_RESTART.clear()

    async def test_dhan_alert_symbol_without_tick_keeps_connected_feed_running(self) -> None:
        store = _FakeStore()
        main_app = _FakeMain()
        started_users = {1}

        with patch.object(mfs, "DHAN_SUBSCRIBE_TICK_CONFIRM_SEC", 0.01), patch.object(
            mfs,
            "DHAN_SUBSCRIBE_RESTART_COOLDOWN_SEC",
            0.0,
        ), patch.object(mfs, "_feed_started_with_current_credentials", AsyncMock(return_value=True)):
            await mfs._confirm_dhan_alert_symbol_ticks(
                main_app,
                store,
                started_users,
                1,
                ["VENUSREM"],
                "chartink_alert",
            )

        main_app.restart_selected_feed.assert_not_awaited()
        self.assertTrue(any("ws_tick_pending:VENUSREM" == item["detail"] for item in store.health_updates))

    async def test_dhan_alert_symbol_without_tick_restarts_disconnected_feed_once(self) -> None:
        store = _FakeStore()
        store.feed_health = {"connected": False, "detail": "websocket_state"}
        main_app = _FakeMain()
        main_app.DHAN_CONNECTED = False
        started_users = {1}

        with patch.object(mfs, "DHAN_SUBSCRIBE_TICK_CONFIRM_SEC", 0.01), patch.object(
            mfs,
            "DHAN_SUBSCRIBE_RESTART_COOLDOWN_SEC",
            0.0,
        ), patch.object(mfs, "_feed_started_with_current_credentials", AsyncMock(return_value=True)):
            await mfs._confirm_dhan_alert_symbol_ticks(
                main_app,
                store,
                started_users,
                1,
                ["VENUSREM"],
                "chartink_alert",
            )

        main_app.restart_selected_feed.assert_awaited_once_with(1)
        self.assertTrue(any("ws_tick_missing:VENUSREM" == item["detail"] for item in store.health_updates))
        self.assertFalse(any(item["connected"] for item in store.health_updates))

    def test_only_dhan_ws_counts_as_first_tick_including_zero_age(self) -> None:
        for source in ("REST_EXIT_FALLBACK", "REST_ENTRY_FALLBACK", "", "ZERODHA_WS"):
            self.assertFalse(mfs._real_ws_tick_seen({"ltp": 100, "age_sec": 0, "source": source}))
        self.assertTrue(mfs._real_ws_tick_seen({"ltp": 100, "age_sec": 0, "source": "DHAN_WS"}))

    async def test_health_refresh_continues_during_slow_subscription_confirmation(self) -> None:
        store = _FakeStore()
        store.redis = type("Redis", (), {})()
        store.redis.lpop = AsyncMock(side_effect=[json.dumps({
            "user_id": 1, "symbols": ["SBIN"], "source": "chartink_alert",
        }), None])
        main_app = _FakeMain()
        main_app.subscribe_symbols_for_user = AsyncMock(return_value={"sent": True})
        blocked = asyncio.Event()
        release = asyncio.Event()

        async def confirm(*args):
            blocked.set()
            await release.wait()

        with patch.object(mfs, "_ensure_user_feed_started", AsyncMock()), \
             patch.object(mfs, "_confirm_dhan_alert_symbol_ticks", side_effect=confirm), \
             patch.object(mfs, "FEED_HEALTH_REFRESH_SEC", 1):
            drain = asyncio.create_task(mfs._drain_subscription_requests(main_app, store, {1}))
            health = asyncio.create_task(mfs._health_loop(main_app, store, {1}))
            try:
                await asyncio.wait_for(blocked.wait(), 1)
                await asyncio.sleep(1.1)
                self.assertFalse(drain.done())
                self.assertGreaterEqual(len(store.health_updates), 2)
                self.assertTrue(all(item["connected"] for item in store.health_updates))
            finally:
                release.set()
                drain.cancel()
                health.cancel()
                await asyncio.gather(drain, health, return_exceptions=True)

    async def test_failed_subscription_job_is_retained_for_retry(self) -> None:
        store = _FakeStore()
        raw = json.dumps({"user_id": 1, "symbols": ["SBIN"]})
        store.redis = type("Redis", (), {})()
        store.redis.lpop = AsyncMock(return_value=raw)
        store.redis.rpush = AsyncMock()
        main_app = _FakeMain()
        main_app.subscribe_symbols_for_user = AsyncMock(side_effect=OSError("send failed"))
        with patch.object(mfs, "_ensure_user_feed_started", AsyncMock()):
            await mfs._drain_subscription_requests(main_app, store, {1})
        store.redis.rpush.assert_awaited_once_with(mfs.MARKET_SUBSCRIPTION_QUEUE, raw)
        store.redis.lpop.assert_awaited_once()

    async def test_dhan_alert_symbol_with_fresh_ws_tick_does_not_restart_feed(self) -> None:
        store = _FakeStore({"ltp": 1708.4, "age_sec": 0.2, "source": "DHAN_WS"})
        main_app = _FakeMain()

        with patch.object(mfs, "DHAN_SUBSCRIBE_TICK_CONFIRM_SEC", 0.01):
            await mfs._confirm_dhan_alert_symbol_ticks(
                main_app,
                store,
                {1},
                1,
                ["VENUSREM"],
                "chartink_alert",
            )

        main_app.restart_selected_feed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
