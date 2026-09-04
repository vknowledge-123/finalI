from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Set, Tuple

from .redis_store import norm_symbol
from .service_bootstrap import configure_logging, init_store, load_user_ids
from .service_queues import MARKET_SUBSCRIPTION_QUEUE
from .stock_sector import STOCK_INDEX_MAPPING

configure_logging("market_feed_service")
log = logging.getLogger("market_feed_service")

REFRESH_SEC = float(os.getenv("MARKET_FEED_REFRESH_SEC", "10") or "10")
SUBSCRIPTION_DRAIN_LIMIT = int(os.getenv("MARKET_SUBSCRIPTION_DRAIN_LIMIT", "200") or "200")
FEED_HEALTH_REFRESH_SEC = float(os.getenv("FEED_HEALTH_REFRESH_SEC", "2") or "2")
FEED_RESTART_AFTER_SEC = float(os.getenv("FEED_RESTART_AFTER_SEC", "30") or "30")
FEED_RESTART_COOLDOWN_SEC = float(os.getenv("FEED_RESTART_COOLDOWN_SEC", "30") or "30")
DHAN_SUBSCRIBE_TICK_CONFIRM_SEC = float(os.getenv("DHAN_SUBSCRIBE_TICK_CONFIRM_SEC", "2") or "2")
DHAN_SUBSCRIBE_RESTART_COOLDOWN_SEC = float(os.getenv("DHAN_SUBSCRIBE_RESTART_COOLDOWN_SEC", "60") or "60")
DHAN_SUBSCRIBE_CONFIRM_SOURCES = {
    "chartink_alert",
    "api_signal_intake",
    "dashboard_subscribe",
    "manual_subscribe",
}
_DISCONNECTED_SINCE: Dict[int, float] = {}
_LAST_RESTART: Dict[int, float] = {}
_DHAN_NO_TICK_RESTART: Dict[Tuple[int, str], float] = {}


def _real_ws_tick_seen(tick: Dict[str, Any], max_age_sec: float = 5.0) -> bool:
    if not tick:
        return False
    try:
        ltp = float(tick.get("ltp") or tick.get("last_price") or 0.0)
        age = float(tick.get("age_sec", 999999.0) or 999999.0)
    except Exception:
        return False
    source = str(tick.get("source") or "").strip().upper()
    if source == "REST_EXIT_FALLBACK":
        return False
    return ltp > 0 and age <= max(0.5, max_age_sec)


async def _wait_for_fresh_ws_ticks(store: Any, user_id: int, symbols: List[str], timeout_sec: float) -> List[str]:
    watched = [norm_symbol(symbol) for symbol in symbols]
    watched = [symbol for symbol in watched if symbol]
    if not watched:
        return []
    deadline = time.time() + max(0.0, timeout_sec)
    missing = list(watched)
    while True:
        still_missing: List[str] = []
        for symbol in watched:
            try:
                latest = await store.load_latest_tick(int(user_id), symbol)
            except Exception:
                latest = {}
            if not _real_ws_tick_seen(latest):
                still_missing.append(symbol)
        missing = still_missing
        if not missing or time.time() >= deadline:
            return missing
        await asyncio.sleep(0.25)


async def _confirm_dhan_alert_symbol_ticks(
    main_app: Any,
    store: Any,
    started_users: Set[int],
    user_id: int,
    symbols: List[str],
    source: str,
) -> None:
    broker = str(await store.load_broker(int(user_id)) or "ZERODHA").strip().upper()
    if broker != "DHAN":
        return
    if str(source or "").strip() not in DHAN_SUBSCRIBE_CONFIRM_SOURCES:
        return
    if DHAN_SUBSCRIBE_TICK_CONFIRM_SEC <= 0:
        return

    missing = await _wait_for_fresh_ws_ticks(store, user_id, symbols, DHAN_SUBSCRIBE_TICK_CONFIRM_SEC)
    if not missing:
        log.info("MARKET_SUBSCRIBE_TICK_OK | user=%s symbols=%s", user_id, symbols)
        return

    now = time.time()
    restart_symbols = [
        symbol
        for symbol in missing
        if now - float(_DHAN_NO_TICK_RESTART.get((int(user_id), symbol), 0.0)) >= DHAN_SUBSCRIBE_RESTART_COOLDOWN_SEC
    ]
    if not restart_symbols:
        log.warning("MARKET_SUBSCRIBE_NO_TICK_COOLDOWN | user=%s symbols=%s", user_id, missing)
        return
    for symbol in restart_symbols:
        _DHAN_NO_TICK_RESTART[(int(user_id), symbol)] = now

    log.warning("MARKET_SUBSCRIBE_NO_TICK_RESTART | user=%s symbols=%s", user_id, restart_symbols)
    await main_app.restart_selected_feed(int(user_id))
    if await _feed_started_with_current_credentials(main_app, store, int(user_id), "DHAN"):
        started_users.add(int(user_id))
    else:
        started_users.discard(int(user_id))
        await store.save_broker_feed_health(
            int(user_id),
            "DHAN",
            False,
            ttl_sec=15,
            detail="restart_after_subscribe_no_tick_failed",
        )
        return

    still_missing = await _wait_for_fresh_ws_ticks(store, user_id, restart_symbols, DHAN_SUBSCRIBE_TICK_CONFIRM_SEC)
    if still_missing:
        detail = "ws_tick_missing:" + ",".join(still_missing[:5])
        await store.save_broker_feed_health(int(user_id), "DHAN", True, ttl_sec=15, detail=detail)
        log.warning("MARKET_SUBSCRIBE_STILL_NO_TICK | user=%s symbols=%s", user_id, still_missing)
    else:
        log.info("MARKET_SUBSCRIBE_TICK_OK_AFTER_RESTART | user=%s symbols=%s", user_id, restart_symbols)


async def _feed_started_with_current_credentials(main_app: Any, store: Any, user_id: int, broker: str) -> bool:
    broker = str(broker or "").strip().upper()
    if broker == "DHAN":
        creds = await store.load_dhan_credentials(int(user_id))
        access_token = str(creds.get("access_token") or "").strip()
        return bool(
            access_token
            and main_app.DHAN_FEED is not None
            and main_app.DHAN_USER_ID == int(user_id)
            and getattr(main_app, "DHAN_ACCESS_TOKEN", "") == access_token
        )

    access_token = str(await store.load_access_token(int(user_id)) or "").strip()
    return bool(
        access_token
        and main_app.KT is not None
        and main_app.KT_USER_ID == int(user_id)
        and main_app.KT_ACCESS_TOKEN == access_token
    )


async def _ensure_user_feed_started(main_app: Any, store: Any, started_users: Set[int], user_id: int) -> None:
    user_id = int(user_id)
    broker = str(await store.load_broker(user_id) or "ZERODHA").strip().upper()
    if user_id in started_users and await _feed_started_with_current_credentials(main_app, store, user_id, broker):
        return
    started_users.discard(user_id)
    engine = await main_app.ensure_engine(user_id)
    await engine.configure_broker()
    await main_app.subscribe_symbols_for_user(user_id, list(STOCK_INDEX_MAPPING.keys()))
    if broker == "DHAN":
        await main_app.subscribe_dhan_sector_indices_for_user(user_id)
    await main_app.restart_selected_feed(user_id)
    if not await _feed_started_with_current_credentials(main_app, store, user_id, broker):
        await store.save_broker_feed_health(
            user_id,
            "DHAN" if broker == "DHAN" else "ZERODHA",
            False,
            ttl_sec=15,
            detail="credentials_missing_or_feed_not_started",
        )
        log.warning("MARKET_FEED_NOT_STARTED | user=%s broker=%s", user_id, broker)
        return
    started_users.add(user_id)
    log.info("MARKET_FEED_STARTED | user=%s broker=%s", user_id, broker)


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
        source = str(job.get("source") or "").strip()
        if not symbols:
            continue
        try:
            await _ensure_user_feed_started(main_app, store, started_users, user_id)
            await main_app.subscribe_symbols_for_user(user_id, symbols)
            log.info("MARKET_SUBSCRIBED_ALERT_SYMBOLS | user=%s symbols=%s", user_id, symbols)
            await _confirm_dhan_alert_symbol_ticks(main_app, store, started_users, user_id, symbols, source)
        except Exception:
            log.exception("Market subscription request failed | user=%s symbols=%s", user_id, symbols)


async def _publish_feed_health(main_app: Any, store: Any, started_users: Set[int]) -> None:
    now = time.time()
    for user_id in list(started_users):
        try:
            broker = str(await store.load_broker(int(user_id)) or "ZERODHA").strip().upper()
            if broker == "DHAN":
                connected = bool(main_app.DHAN_CONNECTED and main_app.DHAN_USER_ID == int(user_id))
            else:
                connected = bool(main_app.KT_CONNECTED and main_app.KT_USER_ID == int(user_id))
            await store.save_broker_feed_health(
                int(user_id),
                "DHAN" if broker == "DHAN" else "ZERODHA",
                connected,
                ttl_sec=15,
                detail="market_feed_service",
            )
            if connected:
                _DISCONNECTED_SINCE.pop(int(user_id), None)
                continue

            since = _DISCONNECTED_SINCE.setdefault(int(user_id), now)
            last_restart = _LAST_RESTART.get(int(user_id), 0.0)
            if now - since >= FEED_RESTART_AFTER_SEC and now - last_restart >= FEED_RESTART_COOLDOWN_SEC:
                _LAST_RESTART[int(user_id)] = now
                log.warning("MARKET_FEED_WATCHDOG_RESTART | user=%s broker=%s", user_id, broker)
                await main_app.restart_selected_feed(int(user_id))
                if await _feed_started_with_current_credentials(main_app, store, int(user_id), broker):
                    started_users.add(int(user_id))
                else:
                    started_users.discard(int(user_id))
                _DISCONNECTED_SINCE.pop(int(user_id), None)
        except Exception:
            log.exception("Market feed health publish failed | user=%s", user_id)


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
        last_health_refresh = 0.0
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
            if now - last_health_refresh >= max(1.0, FEED_HEALTH_REFRESH_SEC):
                last_health_refresh = now
                await _publish_feed_health(main_app, store, started_users)
            await asyncio.sleep(1.0)
    finally:
        await main_app._stop_dhan_feed()
        await main_app._stop_kite_ticker()
        for engine in list(main_app.ENGINE.values()):
            await engine.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
