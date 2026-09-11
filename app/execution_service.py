from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List

from .service_bootstrap import EngineRegistry, configure_logging, init_store
from .service_queues import ALERT_QUEUE, ALERT_PROCESSING_QUEUE, ALERT_DEAD_QUEUE
from .dhan_broker import compact_broker_error

configure_logging("execution_service")
log = logging.getLogger("execution_service")
MAX_ALERT_AGE_SEC = float(os.getenv("MAX_ALERT_AGE_SEC", "60"))
WORKER_LEASE = "execution:worker:lease"


async def _process_job(store, registry: EngineRegistry, job: Dict[str, Any]) -> None:
    user_id = int(job.get("user_id") or 1)
    alert_name = str(job.get("alert_name") or "UNKNOWN")
    symbols: List[str] = [str(symbol) for symbol in (job.get("symbols") or []) if symbol]
    ts = str(job.get("timestamp") or "")
    if not symbols:
        return
    try:
        engine = await registry.get(user_id)
        result = await engine.on_chartink_alert(alert_name, symbols, ts=ts)
    except Exception as exc:
        log.exception("Alert execution failed | user=%s alert=%s", user_id, alert_name)
        await store.set_kill(user_id, True)
        result = [
            {"symbol": symbol, "status": "ERROR", "reason": f"CRITICAL_FAIL:{compact_broker_error(exc)}"}
            for symbol in symbols
        ]

    await store.save_alert(user_id, {"alert_name": alert_name, "time": ts, "result": result})
    log.info("EXECUTED_ALERT | user=%s alert=%s symbols=%s result=%s", user_id, alert_name, symbols, result)


async def _record_interrupted(store, job, reason):
    uid = int(job.get("user_id") or 1)
    await store.set_kill(uid, True)
    await store.save_alert(uid, {
        "alert_name": job.get("alert_name", "UNKNOWN"), "time": job.get("timestamp", ""),
        "result": [{"symbol": s, "status": "ERROR", "reason": reason} for s in job.get("symbols", [])],
    })


async def _run_isolated_job(store, registry, job):
    task = asyncio.create_task(_process_job(store, registry, job), name="execution_alert_job")
    started = time.perf_counter()
    try:
        await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            # Service shutdown: leave the claimed job for conservative recovery.
            raise
        await _record_interrupted(store, job, "ORDER_LOCK_LOST_RECONCILE_REQUIRED")
        log.error("EXECUTION_JOB_CANCELLED | user=%s alert=%s", job.get("user_id"), job.get("alert_name"))
    finally:
        log.info("EXECUTION_JOB_DURATION | user=%s duration_ms=%.1f", job.get("user_id"),
                 (time.perf_counter() - started) * 1000)


def _decode_job(raw):
    job = json.loads(raw)
    if (not isinstance(job, dict) or int(job.get("user_id") or 1) <= 0
            or not isinstance(job.get("symbols"), list)
            or not all(isinstance(s, str) and s.strip() for s in job["symbols"])):
        raise ValueError("INVALID_EXECUTION_JOB")
    return job


async def _recover_interrupted(store):
    # Called only while holding the single execution-worker lease. Never replay:
    # a crash may have happened after the broker accepted an order.
    for raw in await store.redis.lrange(ALERT_PROCESSING_QUEUE, 0, -1):
        try:
            job = _decode_job(raw)
        except (ValueError, TypeError):
            await store.redis.rpush(ALERT_DEAD_QUEUE, raw)
        else:
            await _record_interrupted(store, job, "EXECUTION_INTERRUPTED_RECONCILE_REQUIRED")
        await store.redis.lrem(ALERT_PROCESSING_QUEUE, 1, raw)


async def _consume_once(store, registry):
    # Atomic claim preserves FIFO and retains the job across process crashes.
    raw = await store.redis.blmove(ALERT_QUEUE, ALERT_PROCESSING_QUEUE, 5, src="LEFT", dest="RIGHT")
    if raw is None:
        return
    try:
        job = _decode_job(raw)
    except (ValueError, TypeError):
        await store.redis.rpush(ALERT_DEAD_QUEUE, raw)
        await store.redis.lrem(ALERT_PROCESSING_QUEUE, 1, raw)
        log.error("INVALID_EXECUTION_JOB_QUARANTINED")
        return
    queued = job.get("queued_ts")
    if not isinstance(queued, (float, int)) or not 0 <= time.time() - queued <= MAX_ALERT_AGE_SEC:
        await store.save_alert(int(job.get("user_id") or 1), {
            "alert_name": job.get("alert_name", "UNKNOWN"), "time": job.get("timestamp", ""),
            "result": [{"symbol": s, "status": "SKIPPED", "reason": "ALERT_EXPIRED_IN_QUEUE"} for s in job["symbols"]],
        })
    else:
        await _run_isolated_job(store, registry, job)
    # Acknowledge only after the final result is persisted successfully.
    await store.redis.lrem(ALERT_PROCESSING_QUEUE, 1, raw)


async def main() -> None:
    store = await init_store()
    registry = EngineRegistry(store)
    try:
        token = uuid.uuid4().hex
        if not await store.redis.set(WORKER_LEASE, token, nx=True, px=30000):
            raise RuntimeError("EXECUTION_WORKER_ALREADY_RUNNING_OR_RECOVERING")
        async def lease_lost():
            log.error("EXECUTION_WORKER_LEASE_LOST")
        store._lock_leases.track(WORKER_LEASE, token, 30000, store._renew_order_lock,
                                 store._release_order_lock, lease_lost)
        await _recover_interrupted(store)
        log.info("Execution service waiting on Redis queue %s", ALERT_QUEUE)
        while True:
            try:
                await _consume_once(store, registry)
            except Exception:
                # Keep processing records intact and stop; restart recovery will
                # mark uncertainty before any further entries can be processed.
                log.exception("EXECUTION_PERSISTENCE_OR_PROCESSING_FAILED")
                raise
    finally:
        await registry.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
