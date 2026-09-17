import asyncio
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.alert_features import IST
from app.memory_store import InMemoryStore
from app.services.breakout_monitor import BreakoutMonitor
from app.trade_engine import OrderExecution, TradeEngine


class BreakoutMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryStore()
        self.cfg = {"alert_name": "ttl test", "strategy_mode": "CLASSIC", "enabled": True,
                    "direction": "LONG", "qty_mode": "QTY", "qty": 1, "product": "MIS",
                    "entry_start_time": "00:00", "entry_end_time": "23:59", "trade_limit_per_day": 5,
                    "high_break_enabled": True, "high_break_timeframe_minutes": 1,
                    "high_break_ttl_minutes": 2, "high_break_buffer_enabled": True, "high_break_buffer": .1}
        await self.store.save_alert_config(1, self.cfg)
        self.engine = TradeEngine(1, self.store)
        self.at = datetime.now(IST).replace(hour=10, minute=30, second=3, microsecond=0)
        self.engine._fetch_historical_candles = AsyncMock(return_value=[{
            "date": self.at.replace(second=0) - timedelta(minutes=1), "high": 100, "low": 99,
        }])
        self.engine._wait_for_entry_feed_ready = AsyncMock(return_value=(True, "", 100))
        self.engine._place_order_with_execution = AsyncMock(return_value=OrderExecution(
            order_id="SIMULATED", symbol="SBIN", side="BUY", qty=1, filled_qty=1,
            status="COMPLETE", avg_price=100.2,
        ))
        self.monitor = BreakoutMonitor(lambda: self.store, AsyncMock(return_value=self.engine))

    async def asyncTearDown(self):
        await self.engine.close()

    async def watch(self, **kwargs):
        result = await self.engine.on_chartink_alert("ttl test", ["SBIN"], self.at.isoformat(), **kwargs)
        self.assertEqual(result[0]["status"], "WAITING_FOR_BREAKOUT")
        await self.store.save_alert(1, {"alert_name": "ttl test", "time": self.at.isoformat(), "result": result})
        return await self.store.get_breakout_watch(result[0]["breakout_watch_id"])

    async def cross(self, price=100.2):
        await self.store.save_latest_tick(1, "SBIN", {"ltp": price, "source": "DHAN_WS"})
        self.engine._wait_for_entry_feed_ready.return_value = (True, "", price)

    async def test_wait_cross_fixed_candle_history_and_no_duplicate_order(self):
        watch = await self.watch()
        self.assertEqual(watch["expires_at"] - watch["created_at"], 120)
        self.engine._place_order_with_execution.assert_not_awaited()
        duplicate = await self.watch()
        self.assertEqual(duplicate, watch)
        await self.cross()
        await asyncio.gather(self.monitor.poll_once(), self.monitor.poll_once())
        await self.monitor.poll_once()
        self.engine._fetch_historical_candles.assert_awaited_once()
        self.engine._place_order_with_execution.assert_awaited_once()
        stored = await self.store.get_breakout_watch(watch["id"])
        self.assertEqual(stored["phase"], "DONE")
        self.assertEqual(stored["result"]["status"], "ENTERED")
        rows = await self.store.get_recent_alerts(1)
        self.assertTrue(all(r["result"][0]["status"] == "ENTERED" for r in rows))

    async def test_ttl_expiry_does_not_submit(self):
        watch = await self.watch()
        await self.store.save_breakout_watch(dict(watch, expires_at=time.time() - 1))
        await self.cross()
        await self.monitor.poll_once()
        row = (await self.store.get_recent_alerts(1))[0]["result"][0]
        self.assertEqual(row["reason"], "BREAKOUT_TTL_EXPIRED")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_short_strict_cross_below_low_minus_buffer(self):
        self.cfg["direction"] = "SHORT"
        await self.store.save_alert_config(1, self.cfg)
        watch = await self.watch()
        self.assertEqual(watch["level"], "98.9")
        await self.cross(98.9)
        await self.monitor.poll_once()
        self.engine._place_order_with_execution.assert_not_awaited()
        await self.cross(98.89)
        await self.monitor.poll_once()
        self.assertEqual(self.engine._place_order_with_execution.await_args.args[1], "SELL")

    async def test_missing_and_stale_tick_wait_without_order(self):
        watch = await self.watch()
        await self.monitor.poll_once()
        await self.store.save_latest_tick(1, "SBIN", {"ltp": 105, "received_at": time.time() - 10})
        await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["phase"], "WAITING")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_price_falls_back_before_execution_keeps_original_watch(self):
        watch = await self.watch()
        await self.cross()
        self.engine._wait_for_entry_feed_ready.return_value = (True, "", 99)
        await self.monitor.poll_once()
        self.assertEqual(await self.store.get_breakout_watch(watch["id"]), watch)
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_kill_switch_cancels_watch(self):
        watch = await self.watch()
        await self.store.set_kill(1, True)
        await self.cross()
        await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"], "KILL_SWITCH")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_config_change_cancels_watch(self):
        watch = await self.watch()
        await self.store.save_alert_config(1, dict(self.cfg, qty=3))
        await self.cross()
        await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"], "BREAKOUT_CONFIG_CHANGED")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_disabled_toggle_cancels_watch(self):
        watch = await self.watch()
        await self.store.save_alert_config(1, dict(self.cfg, high_break_enabled=False))
        await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"], "BREAKOUT_DISABLED")

    async def test_entry_end_wins_over_ttl(self):
        watch = await self.watch()
        await self.cross()
        with patch("app.trade_engine._is_within_entry_window", return_value=False):
            await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"], "ENTRY_WINDOW")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_restart_waiting_resumes_and_owner_isolation(self):
        watch = await self.watch(monitor_owner="execution")
        await self.cross()
        await self.monitor.poll_once()
        self.engine._place_order_with_execution.assert_not_awaited()
        restarted = BreakoutMonitor(lambda: self.store, AsyncMock(return_value=self.engine), owner="execution")
        await restarted.recover()
        await restarted.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["phase"], "DONE")
        self.engine._place_order_with_execution.assert_awaited_once()

    async def test_restart_never_replays_uncertain_submission(self):
        watch = await self.watch()
        await self.store.transition_breakout_watch(watch["id"], "WAITING", dict(watch, phase="EXECUTING"))
        await self.monitor.recover()
        await self.cross()
        await self.monitor.poll_once()
        self.assertTrue(await self.store.is_kill(1))
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"],
                         "BREAKOUT_EXECUTION_INTERRUPTED_RECONCILE_REQUIRED")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_duplicate_open_guard_rechecked_before_submission(self):
        watch = await self.watch()
        await self.store.mark_open(1, "SBIN", "other-strategy")
        await self.cross()
        await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"], "ALREADY_OPEN")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_waiting_does_not_consume_trade_limit_and_trigger_rechecks_it(self):
        self.store.allow_trade = AsyncMock(return_value=False)
        watch = await self.watch()
        self.store.allow_trade.assert_not_awaited()
        await self.cross()
        await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"], "TRADE_LIMIT")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_expiry_during_slow_validation_stops_submission(self):
        watch = await self.watch()
        await self.cross()
        clock = [time.time()]

        async def slow_validation(*args):
            clock[0] = watch["expires_at"] + 1
            return True

        self.store.allow_trade = AsyncMock(side_effect=slow_validation)
        with patch("app.trade_engine.time.time", side_effect=lambda: clock[0]):
            await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"], "BREAKOUT_TTL_EXPIRED")
        self.engine._place_order_with_execution.assert_not_awaited()

    async def test_lost_entry_lease_does_not_kill_monitor_loop_or_replay(self):
        watch = await self.watch()
        await self.cross()
        self.engine._place_order_with_execution.side_effect = asyncio.CancelledError
        await self.monitor.poll_once()
        self.assertTrue(await self.store.is_kill(1))
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["reason"],
                         "BREAKOUT_LOCK_LOST_RECONCILE_REQUIRED")
        await self.monitor.poll_once()
        self.engine._place_order_with_execution.assert_awaited_once()

    async def test_order_failure_finishes_watch_without_monitor_retry(self):
        watch = await self.watch()
        await self.cross()
        self.engine._place_order_with_execution.side_effect = RuntimeError("simulated rejection")
        await self.monitor.poll_once()
        await self.monitor.poll_once()
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["result"]["status"], "ERROR")
        self.engine._place_order_with_execution.assert_awaited_once()

    async def test_watch_state_compare_and_set_cannot_overwrite_submission(self):
        watch = await self.watch()
        self.assertTrue(await self.store.transition_breakout_watch(watch["id"], "WAITING", dict(watch, phase="EXECUTING")))
        self.assertFalse(await self.monitor.finish(watch, {"status": "SKIPPED"}))
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["phase"], "EXECUTING")

    async def test_busy_result_cannot_overwrite_another_inflight_submission(self):
        watch = await self.watch()
        await self.cross()

        async def busy(*args, **kwargs):
            await self.store.transition_breakout_watch(watch["id"], "WAITING", dict(watch, phase="EXECUTING"))
            return [{"symbol": "SBIN", "status": "SKIPPED", "reason": "BUSY"}]

        with patch.object(self.engine, "on_chartink_alert", AsyncMock(side_effect=busy)):
            await self.monitor.check(watch)
        self.assertEqual((await self.store.get_breakout_watch(watch["id"]))["phase"], "EXECUTING")

    async def test_background_worker_keeps_checking_while_one_order_waits(self):
        watch = await self.watch()
        await self.store.save_breakout_watch(dict(watch, id="another", symbol="TCS"))
        blocked = asyncio.Event()
        release = asyncio.Event()
        checked = asyncio.Event()
        count = 0

        async def check(record):
            nonlocal count
            if record["symbol"] == "SBIN":
                blocked.set()
                await release.wait()
            else:
                count += 1
                if count >= 2:
                    checked.set()

        self.monitor.check = check
        task = asyncio.create_task(self.monitor.run())
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            await asyncio.wait_for(checked.wait(), 2)
            self.assertFalse(release.is_set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_recovery_resubscribes_only_unexpired_owned_watches(self):
        watch = await self.watch()
        subscribe = AsyncMock()
        monitor = BreakoutMonitor(lambda: self.store, AsyncMock(return_value=self.engine), subscribe_symbols=subscribe)
        await self.store.save_breakout_watch(dict(watch, id="expired", expires_at=time.time() - 1))
        await self.store.save_breakout_watch(dict(watch, id="other", owner="execution"))
        await monitor.recover()
        subscribe.assert_awaited_once_with(1, ["SBIN"])


class RedisWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_cas_history_projection_and_user_isolation(self):
        import fakeredis.aioredis
        from app.redis_store import RedisStore
        from app.breakout_state import waiting_result

        store = RedisStore("redis://localhost:6379/0")
        await store.redis.aclose()
        store.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        watch = {"id": "test", "user_id": 1, "symbol": "SBIN", "side": "BUY", "level": "100.10",
                 "phase": "WAITING", "expires_at": time.time() + 120}
        try:
            await store.save_breakout_watch(watch)
            self.assertGreater(await store.redis.ttl("breakout:watches"), 0)
            self.assertEqual(await store.list_breakout_watches(), [watch])
            submitting = dict(watch, phase="EXECUTING")
            claimed = await asyncio.gather(*(store.transition_breakout_watch("test", "WAITING", submitting) for _ in range(2)))
            self.assertEqual(sum(claimed), 1)
            final = dict(watch, phase="DONE", result={"symbol": "SBIN", "status": "ENTERED", "qty": 1})
            self.assertTrue(await store.transition_breakout_watch("test", "EXECUTING", final))
            # Persist the initial alert AFTER completion to reproduce the UI race.
            alert = {"alert_name": "test", "time": datetime.now(IST).isoformat(), "result": [waiting_result(watch)]}
            await store.save_alert(1, alert)
            await store.save_alert(2, alert)
            self.assertEqual((await store.get_recent_alerts(1))[0]["result"][0]["status"], "ENTERED")
            self.assertEqual((await store.get_recent_alerts(2))[0]["result"][0]["status"], "WAITING_FOR_BREAKOUT")
            await store.delete_breakout_watch("test")
            self.assertEqual(await store.list_breakout_watches(), [])
        finally:
            await store.close()
