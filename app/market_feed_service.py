from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Set

from .service_bootstrap import configure_logging, init_store, load_user_ids
from .service_queues import MARKET_SUBSCRIPTION_QUEUE
from .stock_sector import STOCK_INDEX_MAPPING

configure_logging("market_feed_service")
log = logging.getLogger("market_feed_service")

REFRESH_SEC = float(os.getenv("MARKET_FEED_REFRESH_SEC", "60") or "60")
SUBSCRIPTION_DRAIN_LIMIT = int(os.getenv("MARKET_SUBSCRIPTION_DRAIN_LIMIT", "200") or "200")


async def _ensure_user_feed_started(main_app: Any, store: Any, started_users: Set[int], user_id: int) -> None:
    user_id = int(user_id)
    if user_id in started_users:
        return
    engine = await main_app.ensure_engine(user_id)
    await engine.configure_broker()
    await main_app.subscribe_symbols_for_user(user_id, list(STOCK_INDEX_MAPPING.keys()))
    if await store.load_broker(user_id) == "DHAN":
        await main_app.subscribe_dhan_sector_indices_for_user(user_id)
    await main_app.restart_selected_feed(user_id)
    started_users.add(user_id)
    log.info("MARKET_FEED_STARTED | user=%s broker=%s", user_id, await store.load_broker(user_id))


async def _drain_subscription_requests(main_app: Any, store: Any, started_users: Set[int]) -> None:
    for _ in range(max(1, SUBSCRIPTION_DRAIN_LIMIT)):
        raw = await store.redis.lpop(MARKET_SUBSCRIPTION_QUEUE)
        if not raw:
            return
        try:
            job: Dict[str, Any] = json.loads(raw)
        except Exception:
            log.warning("Bad market subscription job: %r", raw)
            continue
        user_id = int(job.get("user_id") or 1)
        symbols = [str(symbol) for symbol in (job.get("symbols") or []) if symbol]
        if not symbols:
            continue
        try:
            await _ensure_user_feed_started(main_app, store, started_users, user_id)
            await main_app.subscribe_symbols_for_user(user_id, symbols)
            log.info("MARKET_SUBSCRIBED_ALERT_SYMBOLS | user=%s symbols=%s", user_id, symbols)
        except Exception:
            log.exception("Market subscription request failed | user=%s symbols=%s", user_id, symbols)


async def main() -> None:
    # Reuse existing feed wiring in app.main so Dhan/Kite websocket packet
    # handling remains exactly the same as the dashboard process.
    from . import main as main_app

    store = await init_store()
    loop = asyncio.get_running_loop()
    main_app.store = store
    main_app.APP_LOOP = loop
    main_app.ws_mgr.set_loop(loop)
    try:
        log.info("Market feed service started")
        started_users = set()
        last_user_refresh = 0.0
        while True:
            now = time.time()
            if now - last_user_refresh >= max(5.0, REFRESH_SEC):
                last_user_refresh = now
                for user_id in await load_user_ids(store):
                    try:
                        await _ensure_user_feed_started(main_app, store, started_users, int(user_id))
                    except Exception:
                        log.exception("Market feed start failed | user=%s", user_id)
            await _drain_subscription_requests(main_app, store, started_users)
            await asyncio.sleep(1.0)
    finally:
        await main_app._stop_dhan_feed()
        await main_app._stop_kite_ticker()
        for engine in list(main_app.ENGINE.values()):
            await engine.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
