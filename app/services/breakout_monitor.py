"""Non-blocking TTL watches. Only the owning execution path may submit orders."""

import asyncio
import logging
import math
import time
from decimal import Decimal

from ..alert_features import enabled
from ..breakout_state import config_signature

log = logging.getLogger(__name__)


class BreakoutMonitor:
    def __init__(self, store_provider, ensure_engine, owner="api", subscribe_symbols=None):
        self.store_provider = store_provider
        self.ensure_engine = ensure_engine
        self.owner = owner
        self.subscribe_symbols = subscribe_symbols
        self._slots = asyncio.Semaphore(8)

    async def finish(self, record, result):
        updated = dict(record, phase="DONE", result=result)
        return await self.store_provider().transition_breakout_watch(record["id"], record["phase"], updated)

    async def poll_once(self):
        records = await self.store_provider().list_breakout_watches()
        await asyncio.gather(*(self._guarded(record) for record in records if record.get("owner") == self.owner))

    async def _guarded(self, record):
        async with self._slots:
            try:
                await asyncio.create_task(self.check(record), name="breakout_candidate")
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                await self.store_provider().set_kill(record["user_id"], True)
                fresh = await self.store_provider().get_breakout_watch(record["id"])
                if fresh and fresh["phase"] != "DONE":
                    await self.finish(fresh, {"symbol": record["symbol"], "status": "ERROR",
                                              "reason": "BREAKOUT_LOCK_LOST_RECONCILE_REQUIRED"})
            except Exception:
                log.exception("BREAKOUT_MONITOR_FAILED | user=%s symbol=%s", record["user_id"], record["symbol"])

    async def check(self, record):
        store = self.store_provider()
        uid, symbol = record["user_id"], record["symbol"]
        if record["phase"] == "DONE":
            if time.time() > record["expires_at"] + 86400:
                await store.delete_breakout_watch(record["id"])
            return
        if record["phase"] == "EXECUTING":
            # Never replay an order after an ambiguous interruption.
            if time.time() > record.get("executing_at", record["expires_at"]) + 90:
                await store.set_kill(uid, True)
                await self.finish(record, {"symbol": symbol, "status": "ERROR", "reason": "BREAKOUT_EXECUTION_UNCERTAIN_RECONCILE_REQUIRED"})
            return
        reason = ""
        config = await store.get_alert_config(uid, record["alert_name"])
        if time.time() >= record["expires_at"]:
            reason = "BREAKOUT_TTL_EXPIRED"
        elif await store.is_kill(uid):
            reason = "KILL_SWITCH"
        elif not config or not enabled(config.get("enabled", True)) or not enabled(config.get("high_break_enabled")):
            reason = "BREAKOUT_DISABLED"
        elif config_signature(config) != record["config_signature"]:
            reason = "BREAKOUT_CONFIG_CHANGED"
        else:
            from ..trade_engine import _is_within_entry_window
            if not _is_within_entry_window(config.get("entry_start_time", "09:15"), config.get("entry_end_time", "15:15")):
                reason = "ENTRY_WINDOW"
        if reason:
            await self.finish(record, {"symbol": symbol, "status": "SKIPPED", "reason": reason})
            return
        if record.get("level") is None:
            if time.time() < record.get("candle_retry_at", 0):
                return
        else:
            tick = await store.load_latest_tick(uid, symbol)
            try:
                price = float(tick.get("ltp") or 0)
                age = float(tick.get("age_sec", 999999))
                if not math.isfinite(price) or price <= 0 or not 0 <= age <= 5:
                    return
                level = Decimal(record["level"])
                if not (Decimal(str(price)) > level if record["side"] == "BUY" else Decimal(str(price)) < level):
                    return
            except (TypeError, ValueError):
                return
        engine = await self.ensure_engine(uid)
        results = await engine.on_chartink_alert(record["alert_name"], [symbol], ts=record["alert_time"],
                                                breakout_watch_id=record["id"], monitor_owner=self.owner)
        if not results:
            return
        result = results[0]
        fresh = await store.get_breakout_watch(record["id"])
        if not fresh or fresh["phase"] in {"DONE", "EXECUTING"}:
            return
        transient = str(result.get("reason", ""))
        if fresh["phase"] == "WAITING" and (result.get("status") == "WAITING_FOR_BREAKOUT" or transient in {"BUSY", "NO_LTP"}
                                            or transient.startswith(("NO_FRESH_LIVE_TICK", "BROKER_FEED_NOT_CONNECTED"))):
            return
        await self.finish(fresh, result)

    async def recover(self):
        # Startup may follow a crash after submission. Block entries until broker
        # reconciliation instead of re-arming EXECUTING records.
        for record in await self.store_provider().list_breakout_watches():
            if record.get("owner") == self.owner and record.get("phase") == "EXECUTING":
                await self.store_provider().set_kill(record["user_id"], True)
                await self.finish(record, {"symbol": record["symbol"], "status": "ERROR",
                                           "reason": "BREAKOUT_EXECUTION_INTERRUPTED_RECONCILE_REQUIRED"})
            elif (self.subscribe_symbols and record.get("owner") == self.owner
                  and record.get("phase") == "WAITING" and record["expires_at"] > time.time()):
                await self.subscribe_symbols(record["user_id"], [record["symbol"]])

    async def run(self):
        running = {}
        try:
            while True:
                try:
                    for key, task in list(running.items()):
                        if task.done():
                            del running[key]
                            if not task.cancelled() and task.exception():
                                log.error("BREAKOUT_CANDIDATE_TASK_FAILED | id=%s type=%s", key, type(task.exception()).__name__)
                    records = await self.store_provider().list_breakout_watches()
                    for record in records:
                        key = record["id"]
                        if record.get("owner") == self.owner and (key not in running or running[key].done()):
                            running[key] = asyncio.create_task(self._guarded(record))
                except Exception:
                    log.exception("BREAKOUT_MONITOR_PASS_FAILED")
                # A slow broker request for one stock must not stop other watches.
                await asyncio.sleep(0.25)
        finally:
            for task in running.values():
                task.cancel()
            await asyncio.gather(*running.values(), return_exceptions=True)
