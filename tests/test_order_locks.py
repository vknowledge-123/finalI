import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.memory_store import InMemoryStore


class OrderLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryStore()

    async def asyncTearDown(self):
        await self.store.close()

    async def test_immediate_release_before_watcher_starts(self):
        self.assertEqual(await self.store.acquire_lock(1, "SBIN", "exit"), 1)
        await self.store.release_lock(1, "SBIN", "exit")
        self.assertFalse(self.store._locks)
        self.assertFalse(self.store._lock_leases.active)
        self.assertEqual(await self.store.acquire_lock(1, "SBIN", "exit"), 1)

    async def test_slow_operation_renews_past_original_expiry(self):
        clock = 100.0
        renewed = asyncio.Event()
        renew = self.store._renew_order_lock

        async def advance_and_renew(key, token, ttl_ms):
            nonlocal clock
            clock += 0.04
            ok = await renew(key, token, ttl_ms)
            if clock >= 100.35:
                renewed.set()
            return ok

        # Advance lease time per renewal, independently of OS scheduling delays.
        with patch("app.memory_store.time", SimpleNamespace(time=lambda: clock)), patch.object(
            self.store, "_renew_order_lock", advance_and_renew
        ):
            self.assertEqual(await self.store.acquire_lock(1, "SBIN", "exit", ttl_ms=120), 1)
            try:
                await asyncio.wait_for(renewed.wait(), timeout=5)
                self.assertGreater(clock, 100.12)
                self.assertEqual(await self.store.acquire_lock(1, "SBIN", "exit"), 0)
                self.assertFalse(await self.store.is_kill(1))
            finally:
                await self.store.release_lock(1, "SBIN", "exit")

    async def test_another_task_cannot_release_owners_lock(self):
        await self.store.acquire_lock(1, "SBIN", "exit")
        await asyncio.create_task(self.store.release_lock(1, "SBIN", "exit"))
        self.assertEqual(await self.store.acquire_lock(1, "SBIN", "exit"), 0)

    async def test_old_token_cannot_release_new_owner(self):
        await self.store.acquire_lock(1, "SBIN", "exit")
        key = self.store._guard_key(1, "SBIN", "exit")
        self.store._lock_tokens[key] = "new-owner-token"
        await self.store.release_lock(1, "SBIN", "exit")
        self.assertEqual(self.store._lock_tokens[key], "new-owner-token")
        self.assertIn(key, self.store._locks)

    async def test_owner_completion_cleans_up_unreleased_lock(self):
        async def operation():
            await self.store.acquire_lock(1, "SBIN", "exit", ttl_ms=120)
        await asyncio.create_task(operation())
        for _ in range(20):
            if not self.store._locks:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(self.store._locks)

    async def test_lost_lease_sets_kill_and_cancels_owner(self):
        ready = asyncio.Event()

        async def operation():
            await self.store.acquire_lock(1, "SBIN", "entry", ttl_ms=120)
            ready.set()
            await asyncio.sleep(5)

        task = asyncio.create_task(operation())
        await ready.wait()
        key = self.store._guard_key(1, "SBIN", "entry")
        self.store._lock_tokens[key] = "another-owner"
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        self.assertTrue(await self.store.is_kill(1))
        self.assertEqual(self.store._lock_tokens[key], "another-owner")

    async def test_store_close_releases_even_unstarted_watchers(self):
        await self.store.acquire_lock(1, "SBIN", "exit")
        await self.store.close()
        self.assertFalse(self.store._locks)
        self.assertFalse(self.store._lock_leases.active)
