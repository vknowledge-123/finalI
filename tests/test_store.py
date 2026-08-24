import unittest
from unittest.mock import AsyncMock

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
