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
