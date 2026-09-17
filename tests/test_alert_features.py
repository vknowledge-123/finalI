import json
import logging
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import main
from app.alert_features import IST, breakout_level, prepare_features
from app.crypto import EncryptionManager
from app.dhan_broker import normalize_dhan_positions
from app.memory_store import InMemoryStore
from app.redis_store import RedisStore
from app.services.telegram_service import TelegramService, TelegramURLFilter
from app.trade_engine import TradeEngine, OrderExecution

TOKEN = "123456789:" + "a" * 35
NOW = datetime(2026, 9, 17, 10, 30, 3, tzinfo=IST)


def store_with_cipher():
    store = InMemoryStore()
    store.encryption = EncryptionManager(Fernet.generate_key().decode())
    return store


def config(store, **extra):
    payload = {"alert_name": "feature test", "strategy_mode": "CLASSIC", "enabled": True,
               "direction": "LONG", "qty_mode": "QTY", "qty": 2, "product": "MIS",
               "entry_start_time": "00:00", "entry_end_time": "23:59",
               "telegram_enabled": True, "telegram_bot_token": TOKEN, "telegram_chat_id": "-1001234567",
               **extra}
    payload.update(prepare_features(payload, {}, store))
    payload.pop("telegram_bot_token", None)
    return payload


class FeatureApiTests(unittest.TestCase):
    def test_save_reload_encrypts_token_and_preserves_existing_secret(self):
        with TestClient(main.app) as client:
            main.store.encryption = EncryptionManager(Fernet.generate_key().decode())
            payload = {"user_id": 1, "alert_name": "TELEGRAM API", "telegram_enabled": True,
                       "telegram_bot_token": TOKEN, "telegram_chat_id": "-1001234567",
                       "high_break_enabled": True, "high_break_timeframe_minutes": 5,
                       "high_break_ttl_minutes": 2,
                       "high_break_buffer_enabled": True, "high_break_buffer": 0.10}
            result = client.post("/api/alert-config", json=payload).json()
            self.assertEqual(result["status"], "saved")
            self.assertNotIn(TOKEN, json.dumps(result))
            self.assertNotIn("telegram_bot_token_encrypted", result["config"])
            self.assertTrue(result["config"]["telegram_token_set"])
            self.assertEqual(result["config"]["high_break_ttl_minutes"], 2)
            saved = main.store._alert_configs[1]["telegram api"]
            encrypted = saved["telegram_bot_token_encrypted"]
            self.assertNotIn(TOKEN, json.dumps(saved))
            self.assertEqual(main.store.encryption.cipher.decrypt(encrypted.encode()).decode(), TOKEN)
            payload.update(telegram_bot_token="", high_break_buffer=0)
            self.assertEqual(client.post("/api/alert-config", json=payload).json()["status"], "saved")
            saved = main.store._alert_configs[1]["telegram api"]
            self.assertEqual(saved["telegram_bot_token_encrypted"], encrypted)
            self.assertEqual(saved["high_break_buffer"], 0)
            listed = client.get("/api/alert-config?user_id=1").json()
            self.assertNotIn(TOKEN, json.dumps(listed))
            self.assertNotIn(encrypted, json.dumps(listed))

    def test_invalid_feature_settings_fail_without_saving(self):
        with TestClient(main.app) as client:
            cases = [({"telegram_enabled": True}, "TELEGRAM_CREDENTIALS_REQUIRED"),
                     ({"telegram_chat_id": "https://evil.invalid"}, "TELEGRAM_CHAT_ID_INVALID"),
                     ({"telegram_bot_token": TOKEN}, "TELEGRAM_ENCRYPTION_REQUIRED"),
                     ({"high_break_timeframe_minutes": 3}, "HIGH_BREAK_SETTINGS_INVALID"),
                     ({"high_break_ttl_minutes": 0}, "HIGH_BREAK_SETTINGS_INVALID"),
                     ({"high_break_ttl_minutes": 1.5}, "HIGH_BREAK_SETTINGS_INVALID"),
                     ({"high_break_ttl_minutes": 61}, "HIGH_BREAK_SETTINGS_INVALID"),
                     ({"high_break_buffer": "nan"}, "HIGH_BREAK_SETTINGS_INVALID"),
                     ({"high_break_buffer": -1}, "HIGH_BREAK_SETTINGS_INVALID")]
            for settings, expected in cases:
                with self.subTest(settings=settings):
                    result = client.post("/api/alert-config", json={"alert_name": "BAD FEATURES", **settings}).json()
                    self.assertEqual(result["error"], expected)
            self.assertNotIn("bad features", main.store._alert_configs.get(1, {}))


class CandleBreakoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_zerodha_one_minute_interval_contract(self):
        for interval in ("minute", "1minute", "5minute"):
            engine = TradeEngine(1, InMemoryStore(), token_resolver=lambda symbol: 123)
            engine.kite = SimpleNamespace(historical_data=lambda **kwargs: [])
            engine._ensure_kite_ready = AsyncMock(return_value=True)
            now = datetime.now(IST).replace(second=0, microsecond=0)
            candle = {"date": now - timedelta(minutes=2), "high": 100, "low": 99}
            engine.market_data_worker.submit = AsyncMock(return_value=[candle])
            result = await engine._fetch_historical_candles("SBIN", interval, 1)
            self.assertEqual(engine.market_data_worker.submit.await_args.kwargs["interval"],
                             "5minute" if interval == "5minute" else "minute")
            self.assertEqual(result, [] if interval == "5minute" else [candle])
            await engine.close()

    def test_exact_previous_bar_and_timezone_for_one_and_five_minutes(self):
        for minutes in (1, 5):
            start = NOW.replace(second=0) - timedelta(minutes=minutes)
            candles = [{"date": start.isoformat(), "high": 100, "low": 99},
                       {"date": NOW.replace(second=0), "high": 999, "low": 1}]
            self.assertEqual(breakout_level(candles, NOW, minutes, "BUY", .1), Decimal("100.1"))
            self.assertEqual(breakout_level(candles, NOW, minutes, "SELL", .1), Decimal("98.9"))
            self.assertEqual(breakout_level(candles, NOW.astimezone(IST), minutes, "BUY", 0), 100)

    def test_missing_previous_candle_and_opening_bar_fail_closed(self):
        for candles, stamp in [([], NOW),
                               ([{"date": NOW - timedelta(minutes=3), "high": 100, "low": 99}], NOW),
                               ([], NOW.replace(hour=9, minute=15))]:
            with self.assertRaisesRegex(ValueError, "HIGH_BREAK_CANDLE_NOT_READY"):
                breakout_level(candles, stamp, 1, "BUY")

    async def test_entry_breakout_both_directions_and_toggle_bypass(self):
        for direction, ltp, high_on, buffer_on, expected in [
            ("LONG", 100.1, True, True, "WAITING_FOR_BREAKOUT"),
            ("LONG", 100.11, True, True, "ENTERED"),
            ("SHORT", 98.9, True, True, "WAITING_FOR_BREAKOUT"),
            ("SHORT", 98.89, True, True, "ENTERED"),
            ("LONG", 100.05, True, False, "ENTERED"),
            ("LONG", 90, False, True, "ENTERED"),
        ]:
            with self.subTest(direction=direction, ltp=ltp, high_on=high_on, buffer_on=buffer_on):
                store = store_with_cipher()
                cfg = config(store, direction=direction, high_break_enabled=high_on,
                             high_break_buffer_enabled=buffer_on, high_break_buffer=.1)
                await store.save_alert_config(1, cfg)
                engine = TradeEngine(1, store)
                engine._fetch_historical_candles = AsyncMock(return_value=[{
                    "date": NOW.replace(minute=29, second=0), "high": 100, "low": 99,
                }])
                engine._wait_for_entry_feed_ready = AsyncMock(return_value=(True, "", ltp))
                engine._place_order_with_execution = AsyncMock(return_value=OrderExecution(
                    order_id="TEST", symbol="SBIN", side="BUY" if direction == "LONG" else "SELL",
                    qty=1, filled_qty=1, status="PARTIAL", remaining_qty=1, avg_price=ltp,
                ))
                result = await engine.on_chartink_alert("feature test", ["SBIN"], NOW.isoformat())
                self.assertEqual(result[0]["status"], expected)
                events = await store.list_telegram_entries(1)
                self.assertEqual(len(events), int(expected == "ENTERED"))
                if events:
                    self.assertEqual(events[0]["qty"], 1)
                    self.assertEqual(events[0]["entry"], ltp)
                else:
                    engine._place_order_with_execution.assert_not_awaited()
                if not high_on:
                    engine._fetch_historical_candles.assert_not_awaited()
                await engine.close()

    async def test_candle_fetch_failure_skips_order(self):
        store = store_with_cipher()
        await store.save_alert_config(1, config(store, high_break_enabled=True))
        engine = TradeEngine(1, store)
        engine._fetch_historical_candles = AsyncMock(side_effect=RuntimeError("broker unavailable"))
        engine._place_order_with_execution = AsyncMock()
        result = await engine.on_chartink_alert("feature test", ["SBIN"], NOW.isoformat())
        self.assertEqual(result[0]["reason"], "HIGH_BREAK_DATA_UNAVAILABLE")
        engine._place_order_with_execution.assert_not_awaited()
        await engine.close()


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = store_with_cipher()
        self.cfg = config(self.store)
        await self.store.save_alert_config(1, self.cfg)
        self.engine = SimpleNamespace(broker="DHAN", _broker_positions=AsyncMock(return_value={"net": [{"pnl": 125}, {"pnl": -25}]}))
        self.ensure = AsyncMock(return_value=self.engine)
        self.service = TelegramService(lambda: self.store, self.ensure)
        self.service.send = AsyncMock(return_value=True)

    async def test_welcome_daily_ist_and_dedup_shared_group_and_restart(self):
        await self.store.save_alert_config(1, dict(self.cfg, alert_name="second strategy"))
        at = NOW.replace(hour=9, minute=5)
        await self.service.poll_user(1, at - timedelta(minutes=1))
        self.service.send.assert_not_awaited()
        await self.service.poll_user(1, at)
        await self.service.poll_user(1, at)
        self.service.send.assert_awaited_once()
        self.assertIn("Welcome dear traders", self.service.send.await_args.args[2])
        restarted = TelegramService(lambda: self.store, self.ensure)
        restarted.send = AsyncMock(return_value=True)
        await restarted.poll_user(1, at)
        restarted.send.assert_not_awaited()
        await self.service.poll_user(1, at + timedelta(days=1))
        self.assertEqual(self.service.send.await_count, 2)
        self.ensure.assert_not_awaited()

    async def test_pnl_five_minute_slots_single_broker_fetch_per_group(self):
        await self.store.save_alert_config(1, dict(self.cfg, alert_name="second strategy"))
        await self.service.poll_user(1, NOW)
        await self.service.poll_user(1, NOW + timedelta(minutes=2))
        self.service.send.assert_awaited_once()
        self.assertIn("+100.00", self.service.send.await_args.args[2])
        self.engine._broker_positions.assert_awaited_once()
        await self.service.poll_user(1, NOW + timedelta(minutes=5))
        self.assertEqual(self.service.send.await_count, 2)

    async def test_off_and_custom_configs_do_not_send_or_fetch_mtm(self):
        for cfg in [dict(self.cfg, telegram_enabled=False), dict(self.cfg, strategy_mode="GMMA_OBV")]:
            await self.store.save_alert_config(1, cfg)
            await self.service.poll_user(1, NOW)
        self.service.send.assert_not_awaited()
        self.ensure.assert_not_awaited()

    async def test_entry_only_once_and_disabled_pending_entry_is_dropped(self):
        event = {"trade_id": "T1", "created_at": NOW.timestamp(), "alert_name": "feature test",
                 "symbol": "SBIN", "side": "BUY", "product": "CNC", "qty": 2,
                 "entry": 100, "target": 102, "stop_loss": 99}
        await self.store.queue_telegram_entry(1, event)
        # Entry delivery also works outside scheduled P&L hours.
        at = NOW.replace(hour=8)
        event["created_at"] = at.timestamp()
        await self.store.queue_telegram_entry(1, event)
        await self.service.poll_user(1, at)
        await self.service.poll_user(1, at)
        self.service.send.assert_awaited_once()
        self.assertIn("Entry: INR 100.00", self.service.send.await_args.args[2])
        self.assertEqual(await self.store.list_telegram_entries(1), [])
        await self.store.save_alert_config(1, dict(self.cfg, telegram_enabled=False))
        await self.store.queue_telegram_entry(1, dict(event, trade_id="T2"))
        await self.service.poll_user(1, at)
        self.assertEqual(await self.store.list_telegram_entries(1), [])
        self.service.send.assert_awaited_once()

    async def test_failed_delivery_keeps_event_and_does_not_hot_loop(self):
        self.service.send.return_value = False
        at = NOW.replace(hour=8)
        event = {"trade_id": "RETRY", "created_at": at.timestamp(), "alert_name": "feature test",
                 "symbol": "SBIN", "side": "BUY", "product": "MIS", "qty": 1,
                 "entry": 100, "target": 102, "stop_loss": 99}
        await self.store.queue_telegram_entry(1, event)
        await self.service.poll_user(1, at)
        await self.service.poll_user(1, at)
        self.service.send.assert_awaited_once()
        self.assertEqual(len(await self.store.list_telegram_entries(1)), 1)

    async def test_pnl_not_fabricated_on_broker_failure_or_invalid_number(self):
        for value in [RuntimeError("not connected"), {"net": [{"pnl": float("nan")}]}]:
            self.store._telegram_sent.clear()
            self.engine._broker_positions.side_effect = value if isinstance(value, Exception) else None
            self.engine._broker_positions.return_value = value
            with self.assertRaises((RuntimeError, ValueError)):
                await self.service.poll_user(1, NOW)
        self.service.send.assert_not_awaited()

    async def test_send_message_contract_rate_limit_and_secret_redaction(self):
        service = TelegramService(lambda: self.store, self.ensure)
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(json.loads(request.content)["chat_id"], "-1001234567")
            return httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 120}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service.client = client
            self.assertFalse(await service.send(TOKEN, "-1001234567", "test"))
            self.assertFalse(await service.send(TOKEN, "-1001234567", "test"))
        self.assertEqual(len(requests), 1)
        record = logging.LogRecord("httpx", logging.INFO, "", 0, "POST %s", (f"https://api.telegram.org/bot{TOKEN}/sendMessage",), None)
        TelegramURLFilter().filter(record)
        self.assertNotIn(TOKEN, record.getMessage())

    def test_dhan_mtm_uses_realized_and_unrealized_once(self):
        result = normalize_dhan_positions({"status": "success", "data": [
            {"tradingSymbol": "SBIN", "netQty": 1, "realizedProfit": 50, "unrealizedProfit": -20},
            {"tradingSymbol": "TCS", "netQty": 0, "realizedProfit": 100, "unrealizedProfit": 0},
        ]})
        self.assertEqual(sum(row["pnl"] for row in result["net"]), 130)


class TelegramRedisTests(unittest.IsolatedAsyncioTestCase):
    async def test_outbox_claims_persist_and_are_isolated_by_user(self):
        import fakeredis.aioredis
        store = RedisStore("redis://localhost:6379/0")
        await store.redis.aclose()
        store.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            event = {"trade_id": "T1", "alert_name": "test", "created_at": NOW.timestamp()}
            await store.queue_telegram_entry(1, event)
            self.assertEqual(await store.list_telegram_entries(1), [event])
            self.assertEqual(await store.list_telegram_entries(2), [])
            self.assertGreater(await store.redis.ttl("telegram:entries:1"), 0)
            self.assertTrue(await store.claim_telegram_message(1, "same"))
            self.assertFalse(await store.claim_telegram_message(1, "same"))
            self.assertTrue(await store.claim_telegram_message(2, "same"))
            await store.mark_telegram_sent(1, "same")
            self.assertGreater(await store.redis.ttl("telegram:sent:1:same"), 86400)
            await store.delete_telegram_entry(1, "T1")
            self.assertEqual(await store.list_telegram_entries(1), [])
            await store.save_alert_config(5, {"alert_name": "config only"})
            self.assertIn(5, await store.list_all_user_ids())
        finally:
            await store.close()
