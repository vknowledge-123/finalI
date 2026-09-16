import asyncio
import json
import logging
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import execution_service as execution
from app import market_feed_service as market
from app.log_safety import AccessQueryFilter
from app.memory_store import InMemoryStore
from app.order_locks import OrderLockLeases, OrderLockLost
from app.redis_store import RedisStore, k_alerts
from app.service_queues import ALERT_QUEUE, ALERT_PROCESSING_QUEUE, ALERT_DEAD_QUEUE


def job(symbol="SBIN", **kwargs):
    return dict(user_id=1, alert_name="classic", symbols=[symbol],
                timestamp="2026-09-10T10:00:00", queued_ts=time.time(), **kwargs)


class WorkerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dhan_login_restores_only_sector_indices_and_active_positions(self):
        from app import main
        rows = [{"symbol": "VENUSREM", "status": "OPEN"}, {"symbol": "SBIN", "status": "CLOSED"}]
        with patch.object(main, "store", SimpleNamespace(list_positions=AsyncMock(return_value=rows))):
            symbols = await main._dhan_monitor_symbols(1)
        self.assertEqual(set(symbols), set(main.SECTOR_INDEX_INSTRUMENTS) | {"VENUSREM"})

    async def test_cancelled_order_job_does_not_cancel_worker_or_replay(self):
        store = InMemoryStore()
        engine = SimpleNamespace(on_chartink_alert=AsyncMock(side_effect=asyncio.CancelledError()))
        registry = SimpleNamespace(get=AsyncMock(return_value=engine))
        await execution._run_isolated_job(store, registry, job())
        self.assertTrue(await store.is_kill(1))
        rows = await store.get_recent_alerts(1)
        self.assertEqual(rows[0]["result"][0]["reason"], "ORDER_LOCK_LOST_RECONCILE_REQUIRED")
        engine.on_chartink_alert.assert_awaited_once()
        # The service coroutine is still usable after an individual cancellation.
        engine.on_chartink_alert.side_effect = None
        engine.on_chartink_alert.return_value = [{"symbol": "HEG", "status": "SKIPPED", "reason": "KILL_SWITCH"}]
        await execution._run_isolated_job(store, registry, job("HEG"))
        self.assertEqual(engine.on_chartink_alert.await_count, 2)
        await store.close()

    async def test_worker_shutdown_propagates_cancellation(self):
        entered = asyncio.Event()

        async def slow(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        store = SimpleNamespace(save_alert=AsyncMock(), set_kill=AsyncMock())
        registry = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(on_chartink_alert=slow)))
        task = asyncio.create_task(execution._run_isolated_job(store, registry, job()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        store.save_alert.assert_not_awaited()

    async def test_pre_submission_lease_check_fails_closed(self):
        leases = OrderLockLeases()
        on_loss = AsyncMock()
        leases.track("order", "owner", 30000, AsyncMock(return_value=False), AsyncMock(), on_loss)
        try:
            with self.assertRaises(OrderLockLost):
                await leases.verify_current()
            on_loss.assert_awaited_once()
        finally:
            await leases.close()

    async def test_only_missing_symbols_requeued_with_backoff(self):
        request = json.dumps({"user_id": 1, "symbols": ["SBIN", "DELISTED"]})
        redis = SimpleNamespace(llen=AsyncMock(return_value=1), lpop=AsyncMock(return_value=request), rpush=AsyncMock())
        main = SimpleNamespace(subscribe_symbols_for_user=AsyncMock(return_value={"sent": True, "missing": ["DELISTED"]}))
        with patch.object(market, "_ensure_user_feed_started", AsyncMock()), \
             patch.object(market, "_confirm_dhan_alert_symbol_ticks", AsyncMock()) as confirm:
            await market._drain_subscription_requests(main, SimpleNamespace(redis=redis), {1})
        retry = json.loads(redis.rpush.await_args.args[1])
        self.assertEqual(retry["symbols"], ["DELISTED"])
        self.assertEqual(retry["retry_count"], 1)
        self.assertGreater(retry["not_before"], time.time())
        self.assertEqual(confirm.await_args.args[4], ["SBIN"])
        redis.lpop.assert_awaited_once()

    async def test_bad_retry_metadata_is_skipped_and_max_retries_bounded(self):
        values = [dict(not_before="bad"), dict(not_before=float("nan")), dict(retry_count="bad"), dict(retry_count=5)]
        redis = SimpleNamespace(llen=AsyncMock(return_value=4), lpop=AsyncMock(side_effect=[
            json.dumps(dict(user_id=1, symbols=["MISSING"], **v)) for v in values
        ]), rpush=AsyncMock())
        main = SimpleNamespace(subscribe_symbols_for_user=AsyncMock(return_value={"sent": True, "missing": ["MISSING"]}))
        with patch.object(market, "_ensure_user_feed_started", AsyncMock()):
            await market._drain_subscription_requests(main, SimpleNamespace(redis=redis), {1})
        main.subscribe_symbols_for_user.assert_awaited_once()
        redis.rpush.assert_not_awaited()

    def test_access_log_drops_auth_query_but_keeps_route_and_status(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %s',
                                   ("127.0.0.1", "POST", "/webhook/chartink?secret=private&user_id=1", "1.1", 200), None)
        self.assertTrue(AccessQueryFilter().filter(record))
        self.assertNotIn("private", record.getMessage())
        self.assertIn("/webhook/chartink HTTP/1.1", record.getMessage())


class RedisQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fake = pytest.importorskip("fakeredis.aioredis")
        self.store = RedisStore.__new__(RedisStore)
        self.store.redis = fake.FakeRedis(decode_responses=True)
        self.store._lock_leases = OrderLockLeases()
        self.store.encryption = None
        self.store._sha_lock = self.store._sha_limit = None
        self.engine = SimpleNamespace(on_chartink_alert=AsyncMock(return_value=[{"symbol": "SBIN", "status": "SKIPPED"}]))
        self.registry = SimpleNamespace(get=AsyncMock(return_value=self.engine))

    async def asyncTearDown(self):
        await self.store._lock_leases.close()
        await self.store.redis.aclose()

    async def enqueue(self, value):
        raw = json.dumps(value)
        await self.store.redis.rpush(ALERT_QUEUE, raw)
        return raw

    async def test_fifo_claim_ack_after_save(self):
        await self.enqueue(job("SBIN"))
        await self.enqueue(job("HEG"))
        await execution._consume_once(self.store, self.registry)
        self.assertEqual(self.engine.on_chartink_alert.await_args.args[1], ["SBIN"])
        self.assertEqual(await self.store.redis.llen(ALERT_PROCESSING_QUEUE), 0)
        self.assertEqual(await self.store.redis.llen(ALERT_QUEUE), 1)
        self.assertTrue(await self.store.get_recent_alerts(1))

    async def test_terminal_position_lua_rejects_stale_reopen_and_preserves_json(self):
        closed = {"trade_id": "T1", "status": "CLOSED", "qty": 0, "fills": [], "checks": {}}
        await self.store.upsert_position(1, "SBIN", closed)
        with self.assertRaisesRegex(RuntimeError, "STALE_POSITION_REOPEN"):
            await self.store.upsert_position(1, "SBIN", dict(closed, status="OPEN", qty=2))
        stored = await self.store.get_position(1, "SBIN")
        self.assertEqual(stored, dict(closed, symbol="SBIN"))
        new_trade = dict(closed, trade_id="T2", status="OPEN", qty=3)
        await self.store.upsert_position(1, "SBIN", new_trade)
        self.assertEqual(await self.store.get_position(1, "SBIN"), dict(new_trade, symbol="SBIN"))

    async def test_webhook_queue_execution_updates_same_dashboard_row(self):
        import httpx
        from app import alert_service

        transport = httpx.ASGITransport(app=alert_service.app)
        with patch.object(alert_service, "store", self.store), patch.object(alert_service, "WEBHOOK_SECRET", "test-only"):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                denied = await client.post("/webhook/chartink", json={"alert_name": "classic", "stocks": "SBIN"})
                self.assertEqual(denied.status_code, 401)
                response = await client.post("/webhook/chartink?secret=test-only", json={"alert_name": "classic", "stocks": "SBIN,SBIN"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["symbols"], ["SBIN"])
        queued = (await self.store.get_recent_alerts(1))[0]
        self.assertEqual(queued["result"][0]["status"], "QUEUED")
        await execution._consume_once(self.store, self.registry)
        rows = await self.store.get_recent_alerts(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["time"], queued["time"])
        self.assertEqual(rows[0]["result"][0]["status"], "SKIPPED")

    async def test_restart_recovery_never_replays_uncertain_order(self):
        raw = await self.enqueue(job())
        await self.store.redis.blmove(ALERT_QUEUE, ALERT_PROCESSING_QUEUE, 1, "LEFT", "RIGHT")
        await execution._recover_interrupted(self.store)
        self.engine.on_chartink_alert.assert_not_awaited()
        self.assertTrue(await self.store.is_kill(1))
        rows = await self.store.get_recent_alerts(1)
        self.assertEqual(rows[0]["result"][0]["reason"], "EXECUTION_INTERRUPTED_RECONCILE_REQUIRED")
        self.assertNotIn(raw, await self.store.redis.lrange(ALERT_PROCESSING_QUEUE, 0, -1))

    async def test_persistence_failure_retains_processing_record(self):
        raw = await self.enqueue(job())
        with patch.object(self.store, "save_alert", AsyncMock(side_effect=OSError("store down"))):
            with self.assertRaises(OSError):
                await execution._consume_once(self.store, self.registry)
        self.assertEqual(await self.store.redis.lrange(ALERT_PROCESSING_QUEUE, 0, -1), [raw])

    async def test_expired_alert_is_not_executed(self):
        value = job()
        value["queued_ts"] -= execution.MAX_ALERT_AGE_SEC + 1
        await self.enqueue(value)
        await execution._consume_once(self.store, self.registry)
        self.engine.on_chartink_alert.assert_not_awaited()
        self.assertEqual((await self.store.get_recent_alerts(1))[0]["result"][0]["reason"], "ALERT_EXPIRED_IN_QUEUE")

    async def test_malformed_job_quarantined(self):
        await self.store.redis.rpush(ALERT_QUEUE, "[]")
        await execution._consume_once(self.store, self.registry)
        self.assertEqual(await self.store.redis.lrange(ALERT_DEAD_QUEUE, 0, -1), ["[]"])
        self.assertEqual(await self.store.redis.llen(ALERT_PROCESSING_QUEUE), 0)
        self.registry.get.assert_not_awaited()

    async def test_history_concurrent_writers_keep_other_rows_and_fields(self):
        await self.store.save_alert(1, {"alert_name": "A", "time": "T", "symbols": ["SBIN"]})
        await asyncio.gather(
            self.store.save_alert(1, {"alert_name": "A", "time": "T", "result": []}),
            self.store.save_alert(1, {"alert_name": "B", "time": "T", "symbols": ["HEG"]}),
        )
        rows = {r["alert_name"]: r for r in await self.store.get_recent_alerts(1)}
        self.assertEqual(set(rows), {"A", "B"})
        self.assertEqual(rows["A"]["symbols"], ["SBIN"])
        self.assertEqual(rows["A"]["result"], [])
        self.assertGreater(await self.store.redis.ttl(k_alerts(1)), 0)

    async def test_lock_renew_and_release_cannot_change_another_owner(self):
        await self.store.redis.set("test-lock", "other", px=30000)
        self.assertFalse(await self.store._renew_order_lock("test-lock", "mine", 30000))
        await self.store._release_order_lock("test-lock", "mine")
        self.assertEqual(await self.store.redis.get("test-lock"), "other")
