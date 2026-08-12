from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List

from .service_bootstrap import EngineRegistry, configure_logging, init_store
from .service_queues import ALERT_QUEUE

configure_logging("execution_service")
log = logging.getLogger("execution_service")


async def _process_job(store, registry: EngineRegistry, job: Dict[str, Any]) -> None:
    user_id = int(job.get("user_id") or 1)
    alert_name = str(job.get("alert_name") or "UNKNOWN")
    symbols: List[str] = [str(symbol) for symbol in (job.get("symbols") or []) if symbol]
    ts = str(job.get("timestamp") or "")
    if not symbols:
        return
    engine = await registry.get(user_id)
    try:
        result = await engine.on_chartink_alert(alert_name, symbols, ts=ts)
    except Exception as exc:
        log.exception("Alert execution failed | user=%s alert=%s", user_id, alert_name)
        await store.set_kill(user_id, True)
        result = [{"symbol": symbol, "status": "ERROR", "reason": f"CRITICAL_FAIL:{exc}"} for symbol in symbols]

    await store.save_alert(user_id, {"alert_name": alert_name, "time": ts, "result": result})
    log.info("EXECUTED_ALERT | user=%s alert=%s symbols=%s result=%s", user_id, alert_name, symbols, result)


async def main() -> None:
    store = await init_store()
    registry = EngineRegistry(store)
    try:
        log.info("Execution service waiting on Redis queue %s", ALERT_QUEUE)
        while True:
            item = await store.redis.blpop([ALERT_QUEUE], timeout=5)
            if not item:
                continue
            _queue_name, raw = item
            try:
                job = json.loads(raw)
                if isinstance(job, dict):
                    await _process_job(store, registry, job)
            except Exception:
                log.exception("Bad execution job: %r", raw)
    finally:
        await registry.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())

