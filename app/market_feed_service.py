from __future__ import annotations

import asyncio
import logging
import os

from .service_bootstrap import configure_logging, init_store, load_user_ids
from .stock_sector import STOCK_INDEX_MAPPING

configure_logging("market_feed_service")
log = logging.getLogger("market_feed_service")

REFRESH_SEC = float(os.getenv("MARKET_FEED_REFRESH_SEC", "60") or "60")


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
        while True:
            for user_id in await load_user_ids(store):
                try:
                    if user_id in started_users:
                        continue
                    engine = await main_app.ensure_engine(int(user_id))
                    await engine.configure_broker()
                    await main_app.subscribe_symbols_for_user(int(user_id), list(STOCK_INDEX_MAPPING.keys()))
                    if await store.load_broker(int(user_id)) == "DHAN":
                        await main_app.subscribe_dhan_sector_indices_for_user(int(user_id))
                    await main_app.restart_selected_feed(int(user_id))
                    started_users.add(user_id)
                    log.info("MARKET_FEED_STARTED | user=%s broker=%s", user_id, await store.load_broker(int(user_id)))
                except Exception:
                    log.exception("Market feed start failed | user=%s", user_id)
            await asyncio.sleep(max(5.0, REFRESH_SEC))
    finally:
        await main_app._stop_dhan_feed()
        await main_app._stop_kite_ticker()
        for engine in list(main_app.ENGINE.values()):
            await engine.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())

