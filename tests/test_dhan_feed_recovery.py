import asyncio
import base64
import io
import json
import struct
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app import market_feed_service as mfs
from app.dhan_broker import DhanFeedService, _QueuedDhanMarketFeed, _SafeDhanOrderUpdate, _DhanFeedDisconnect
from app.dhan_feed_policy import FeedBackoff, equity_session_open, safe_feed_error, token_expired


def jwt(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.test-signature"


class FeedRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def service(self, token="test-token"):
        return DhanFeedService(1, "client", token, AsyncMock(), AsyncMock())

    def test_expiry_metadata_is_only_used_to_block_expired_tokens(self):
        self.assertTrue(token_expired(jwt(100), now=101))
        self.assertFalse(token_expired(jwt(200), now=101))
        for value in ("opaque-token", "bad.@@@@.token", "a.W10.b", ""):
            self.assertFalse(token_expired(value))

    async def test_expired_token_starts_neither_socket(self):
        service = self.service(jwt(100))
        with patch("app.dhan_broker._QueuedDhanMarketFeed") as market, patch("app.dhan_broker._SafeDhanOrderUpdate") as orders:
            await service.start(["14"])
        market.assert_not_called()
        orders.assert_not_called()
        self.assertEqual(service.health_detail, "token_expired_reauthenticate")
        self.assertTrue(service.reconnect_managed)
        await service.stop()

    def test_session_hours_weekend_and_holiday_override(self):
        ist = ZoneInfo("Asia/Kolkata")
        sunday = datetime(2026, 9, 6, 10, tzinfo=ist)
        monday = datetime(2026, 9, 7, 10, tzinfo=ist)
        self.assertFalse(equity_session_open(sunday))
        self.assertTrue(equity_session_open(monday))
        self.assertFalse(equity_session_open(monday.replace(hour=23)))
        with patch.dict("os.environ", {"DHAN_MARKET_EXTRA_SESSIONS": "2026-09-06"}):
            self.assertTrue(equity_session_open(sunday))
        with patch.dict("os.environ", {"DHAN_MARKET_HOLIDAYS": "2026-09-07"}):
            self.assertFalse(equity_session_open(monday))

    def test_rate_limit_retry_after_and_exponential_backoff(self):
        error = RuntimeError("sensitive-url?token=do-not-log")
        error.response = SimpleNamespace(status_code=429, headers={"Retry-After": "120"})
        retry = FeedBackoff()
        with patch("app.dhan_feed_policy.random.uniform", return_value=0):
            self.assertEqual(retry.delay(error, session_open=True), 120)
            self.assertEqual(retry.delay(error, session_open=False), 300)
            retry.reset()
            self.assertEqual([retry.delay(OSError(), session_open=True) for _ in range(4)], [5, 10, 20, 40])
        self.assertEqual(safe_feed_error(error), "HTTP_429")
        self.assertNotIn("do-not-log", safe_feed_error(OSError("do-not-log")))

    async def test_order_login_has_documented_payload_without_stdout_secret(self):
        service = self.service()
        receiver = _SafeDhanOrderUpdate(service.context)
        receiver.on_update = AsyncMock()
        packet = {"Type": "order_alert", "Data": {"OrderNo": "O1", "Status": "TRADED"}}
        ws = SimpleNamespace(send=AsyncMock(), recv=AsyncMock(side_effect=[json.dumps(packet), asyncio.CancelledError()]))
        conn = AsyncMock()
        conn.__aenter__.return_value = ws
        output = io.StringIO()
        with patch("app.dhan_broker.websockets.connect", return_value=conn), redirect_stdout(output):
            with self.assertRaises(asyncio.CancelledError):
                await receiver.connect_order_update()
        receiver.on_update.assert_awaited_once_with(packet)
        auth = json.loads(ws.send.await_args.args[0])
        self.assertEqual(auth, {"LoginReq": {"MsgCode": 42, "ClientId": "client", "Token": "test-token"}, "UserType": "SELF"})
        self.assertEqual(output.getvalue(), "")
        conn.__aexit__.assert_awaited_once()

    async def test_malformed_order_frames_do_not_dispatch_fabricated_orders(self):
        for raw in ('1\n429 too many requests', '{"Type":"order_alert"}\n{}', '[]', '{"Type":"order_alert","Data":[]}'):
            receiver = _SafeDhanOrderUpdate(self.service().context)
            receiver.on_update = AsyncMock()
            ws = SimpleNamespace(send=AsyncMock(), recv=AsyncMock(return_value=raw))
            conn = AsyncMock()
            conn.__aenter__.return_value = ws
            with patch("app.dhan_broker.websockets.connect", return_value=conn):
                with self.assertRaises(ValueError):
                    await receiver.connect_order_update()
            receiver.on_update.assert_not_awaited()
            conn.__aexit__.assert_awaited_once()

    async def test_order_error_waits_and_logs_no_payload(self):
        service = self.service()
        service.order_update = SimpleNamespace(connect_order_update=AsyncMock(side_effect=ValueError("test-token")))
        with patch("app.dhan_broker.FeedBackoff.delay", return_value=60), \
             patch("app.dhan_broker.asyncio.sleep", side_effect=asyncio.CancelledError()) as sleep, \
             self.assertLogs("dhan_broker", "WARNING") as logs:
            with self.assertRaises(asyncio.CancelledError):
                await service._run_order_updates()
        sleep.assert_awaited_once_with(60)
        self.assertNotIn("test-token", " ".join(logs.output))
        service.order_update.connect_order_update.assert_awaited_once()

    async def test_market_rate_limit_waits_before_another_connection(self):
        feed = _QueuedDhanMarketFeed(self.service().context, [], "v2")
        feed._running = True
        error = RuntimeError("test-token")
        error.response = SimpleNamespace(status_code=429, headers={})
        feed.connect = AsyncMock(side_effect=error)
        try:
            with patch("app.dhan_broker.FeedBackoff.delay", return_value=60), \
                 patch("app.dhan_broker.asyncio.sleep", side_effect=asyncio.CancelledError()) as sleep:
                with self.assertRaises(asyncio.CancelledError):
                    await feed._run_async()
            feed.connect.assert_awaited_once()
            sleep.assert_awaited_once_with(60)
            self.assertEqual(feed.health_detail, "HTTP_429")
        finally:
            feed.loop.close()

    async def test_auth_failure_stops_retries_until_credentials_change(self):
        feed = _QueuedDhanMarketFeed(self.service().context, [], "v2")
        feed._running = True
        feed.connect = AsyncMock(side_effect=_DhanFeedDisconnect(807))
        try:
            await feed._run_async()
            feed.connect.assert_awaited_once()
            self.assertEqual(feed.health_detail, "authentication_rejected_reauthenticate")
            with self.assertRaises(_DhanFeedDisconnect):
                feed.process_data(struct.pack("<BHBIH", 50, 10, 0, 0, 807))
        finally:
            feed.loop.close()

    async def test_idle_market_socket_is_not_reconnected(self):
        feed = _QueuedDhanMarketFeed(self.service().context, [], "v2")
        feed._running = True
        feed.connect = AsyncMock()
        feed.get_instrument_data = AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.CancelledError()])
        try:
            with self.assertRaises(asyncio.CancelledError):
                await feed._run_async()
            feed.connect.assert_awaited_once()
            self.assertEqual(feed.get_instrument_data.await_count, 2)
        finally:
            feed.loop.close()

    async def test_watchdog_does_not_reset_managed_backoff(self):
        main = SimpleNamespace(DHAN_FEED=SimpleNamespace(reconnect_managed=True, health_detail="HTTP_429"),
                               DHAN_USER_ID=1, DHAN_CONNECTED=False, restart_selected_feed=AsyncMock())
        store = SimpleNamespace(load_broker=AsyncMock(return_value="DHAN"), save_broker_feed_health=AsyncMock())
        with patch.object(mfs, "FEED_RESTART_AFTER_SEC", 0), patch.object(mfs, "FEED_RESTART_COOLDOWN_SEC", 0), \
             patch.object(mfs, "equity_session_open", return_value=True):
            await mfs._publish_feed_health(main, store, {1})
            await mfs._publish_feed_health(main, store, {1})
        main.restart_selected_feed.assert_not_awaited()
        self.assertEqual(store.save_broker_feed_health.await_args.kwargs["detail"], "HTTP_429")

    async def test_sunday_no_tick_does_not_restart_or_wait_for_ticks(self):
        main = SimpleNamespace(restart_selected_feed=AsyncMock())
        store = SimpleNamespace(load_broker=AsyncMock(return_value="DHAN"))
        with patch.object(mfs, "equity_session_open", return_value=False), \
             patch.object(mfs, "_wait_for_fresh_ws_ticks", AsyncMock()) as wait:
            await mfs._confirm_dhan_alert_symbol_ticks(main, store, {1}, 1, ["SBIN"], "chartink_alert")
        wait.assert_not_awaited()
        main.restart_selected_feed.assert_not_awaited()

    async def test_new_credentials_replace_blocked_feed_without_service_restart(self):
        from app import main
        from app.memory_store import InMemoryStore

        store = InMemoryStore()
        await store.save_dhan_credentials(1, "client", "replacement-token")
        old = self.service(jwt(100))
        old.stop = AsyncMock()
        replacement = SimpleNamespace(client_id="client", start=AsyncMock())
        with patch.object(main, "store", store), patch.object(main, "_is_test_mode", return_value=False), \
             patch.object(main, "_stop_kite_ticker", AsyncMock()), \
             patch.object(main, "DHAN_FEED", old), patch.object(main, "DHAN_USER_ID", 1), \
             patch.object(main, "DHAN_ACCESS_TOKEN", old.access_token), patch.object(main, "DHAN_CONNECTED", False), \
             patch.object(main, "DHAN_SUBSCRIPTIONS", {(0, "14")}), patch.object(main, "_save_feed_health_nowait"), \
             patch.object(main, "DhanFeedService", return_value=replacement) as factory:
            self.assertFalse(await mfs._feed_started_with_current_credentials(main, store, 1, "DHAN"))
            await main.start_dhan_feed(1)
            old.stop.assert_awaited_once()
            replacement.start.assert_awaited_once_with({(0, "14")})
            self.assertEqual(factory.call_args.kwargs["access_token"], "replacement-token")
            self.assertTrue(await mfs._feed_started_with_current_credentials(main, store, 1, "DHAN"))
        await store.close()

    async def test_v2_disconnect_sends_only_json_and_closes_socket(self):
        feed = _QueuedDhanMarketFeed(self.service().context, [], "v2")
        ws = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
        feed.ws = ws
        try:
            await feed.disconnect()
            ws.send.assert_awaited_once()
            self.assertEqual(json.loads(ws.send.await_args.args[0]), {"RequestCode": 12})
            ws.close.assert_awaited_once()
            self.assertIsNone(feed.ws)
        finally:
            feed.loop.close()
