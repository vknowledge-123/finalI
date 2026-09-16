import asyncio
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from redis.exceptions import (
    AuthenticationError, AuthorizationError, BusyLoadingError, ConnectionError,
    NoPermissionError, TimeoutError as RedisTimeoutError,
)

from app.memory_store import InMemoryStore
from app.redis_store import RedisStore, k_alert_cfg, k_alert_cfg_legacy


class FakeRedis:
    def __init__(self) -> None:
        self.deleted = []
        self.hdeleted = []
        self.scan_patterns = []

    async def hdel(self, key, field):
        self.hdeleted.append((key, field))
        return 1

    async def delete(self, *keys):
        self.deleted.extend(keys)
        return len(keys)

    async def scan_iter(self, match):
        self.scan_patterns.append(match)
        if match.startswith("trade:open:"):
            yield "trade:open:1:SBIN"
        if match.startswith("trade:count:"):
            yield "trade:count:1:20260614:test"

    async def close(self):
        return None


class RedisStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_errors_are_specific_and_do_not_leak_credentials(self):
        secret = "redis://default:test-private-password@127.0.0.1:6379/0"
        cases = [
            (AuthenticationError, "REDIS_AUTH_FAILED"),
            (AuthorizationError, "REDIS_PERMISSION_DENIED"),
            (NoPermissionError, "REDIS_PERMISSION_DENIED"),
            (BusyLoadingError, "REDIS_LOADING"),
            (RedisTimeoutError, "REDIS_TIMEOUT"),
            (TimeoutError, "REDIS_TIMEOUT"),
            (ConnectionError, "REDIS_CONNECTION_FAILED"),
            (ValueError, "REDIS_CHECK_FAILED"),
        ]
        for error_type, code in cases:
            with self.subTest(error_type=error_type):
                store = RedisStore.__new__(RedisStore)
                store.redis = SimpleNamespace(ping=AsyncMock(side_effect=error_type(secret)))
                try:
                    await store.require_connection()
                except RuntimeError as exc:
                    self.assertIn(code, str(exc))
                    rendered = "".join(traceback.format_exception(exc))
                    self.assertNotIn(secret, rendered)
                    self.assertNotIn("test-private-password", rendered)
                else:
                    self.fail("Startup must fail closed when Redis fails")

    async def test_startup_ping_accepts_success_and_rejects_false(self):
        store = RedisStore.__new__(RedisStore)
        store.redis = SimpleNamespace(ping=AsyncMock(return_value=True))
        await store.require_connection()
        store.redis.ping.return_value = False
        with self.assertRaisesRegex(RuntimeError, "REDIS_PING_FAILED"):
            await store.require_connection()

    async def test_startup_ping_cancellation_propagates(self):
        store = RedisStore.__new__(RedisStore)
        store.redis = SimpleNamespace(ping=AsyncMock(side_effect=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await store.require_connection()

    async def test_delete_alert_config_removes_new_and_legacy_entries(self) -> None:
        store = RedisStore("redis://unused")
        fake = FakeRedis()
        store.redis = fake

        deleted = await store.delete_alert_config(1, "My_Strategy")

        self.assertTrue(deleted)
        self.assertEqual(
            fake.hdeleted,
            [
                (k_alert_cfg(1), "my strategy"),
                (k_alert_cfg_legacy(1), "my strategy"),
            ],
        )

    async def test_daily_state_cleanup_removes_guards_counters_and_snapshots(self) -> None:
        store = RedisStore("redis://unused")
        fake = FakeRedis()
        store.redis = fake

        result = await store.clear_daily_trading_state(1)

        self.assertEqual(result["scanned_keys"], 2)
        self.assertIn("positions:1", fake.deleted)
        self.assertIn("alerts:1", fake.deleted)
        self.assertIn("kill:1", fake.deleted)
        self.assertNotIn("positions:cnc_carry:1", fake.deleted)
        self.assertIn("trade:open:1:SBIN", fake.deleted)
        self.assertIn("trade:count:1:20260614:test", fake.deleted)
        self.assertIn("lock:1:*", fake.scan_patterns)

    async def test_memory_daily_cleanup_clears_dashboard_state_preserves_configs(self) -> None:
        store = InMemoryStore()
        await store.save_alert(1, {"alert_name": "a", "symbols": ["SBIN"], "result": []})
        await store.upsert_position(1, "SBIN", {"symbol": "SBIN", "status": "OPEN", "qty": 1})
        await store.mark_open(1, "SBIN", "trade-1")
        await store.save_cnc_carry_position(1, "SBIN", {"symbol": "SBIN", "product": "CNC", "qty": 1})
        await store.set_kill(1, True)
        await store.set_auto_sq_off_enabled(1, True)
        await store.mark_auto_sq_off_run(1)

        await store.clear_daily_trading_state(1)

        self.assertEqual(await store.get_recent_alerts(1), [])
        self.assertEqual(await store.list_positions(1), [])
        self.assertEqual(await store.get_open(1, "SBIN"), "")
        self.assertEqual((await store.list_cnc_carry_positions(1))[0]["symbol"], "SBIN")
        self.assertFalse(await store.is_kill(1))
        self.assertTrue(await store.is_auto_sq_off_enabled(1))
        self.assertFalse(await store.has_auto_sq_off_run(1))


if __name__ == "__main__":
    unittest.main()
