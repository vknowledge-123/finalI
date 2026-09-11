from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Set, Tuple

from .redis_store import norm_symbol
from .service_bootstrap import configure_logging, init_store, load_user_ids
from .service_queues import MARKET_SUBSCRIPTION_QUEUE
from .dhan_feed_policy import equity_session_open

configure_logging("market_feed_service")
log = logging.getLogger("market_feed_service")

REFRESH_SEC = float(os.getenv("MARKET_FEED_REFRESH_SEC", "10") or "10")
SUBSCRIPTION_DRAIN_LIMIT = int(os.getenv("MARKET_SUBSCRIPTION_DRAIN_LIMIT", "200") or "200")
FEED_HEALTH_REFRESH_SEC = float(os.getenv("FEED_HEALTH_REFRESH_SEC", "2") or "2")
FEED_RESTART_AFTER_SEC = float(os.getenv("FEED_RESTART_AFTER_SEC", "30") or "30")
FEED_RESTART_COOLDOWN_SEC = float(os.getenv("FEED_RESTART_COOLDOWN_SEC", "30") or "30")
DHAN_SUBSCRIBE_TICK_CONFIRM_SEC = float(os.getenv("DHAN_SUBSCRIBE_TICK_CONFIRM_SEC", "2") or "2")
DHAN_SUBSCRIBE_RESTART_COOLDOWN_SEC = float(os.getenv("DHAN_SUBSCRIBE_RESTART_COOLDOWN_SEC", "60") or "60")
DHAN_SUBSCRIBE_RESTART_ON_NO_TICK = str(os.getenv("DHAN_SUBSCRIBE_RESTART_ON_NO_TICK", "0")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
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
        age = float(tick.get("age_sec", 999999.0))
    except Exception:
        return False
    source = str(tick.get("source") or "").strip().upper()
    return source == "DHAN_WS" and ltp > 0 and 0 <= age <= max(0.5, max_age_sec)


def _dhan_connected(main_app: Any, user_id: int) -> bool:
    return bool(getattr(main_app, "DHAN_CONNECTED", False) and getattr(main_app, "DHAN_USER_ID", None) == user_id)


def _dhan_manages_reconnect(main_app: Any, user_id: int) -> bool:
    return bool(getattr(main_app, "DHAN_USER_ID", None) == user_id
                and getattr(getattr(main_app, "DHAN_FEED", None), "reconnect_managed", False))


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
    if not equity_session_open():
        log.info("MARKET_SUBSCRIBE_SESSION_CLOSED | user=%s", user_id)
        return

    missing = await _wait_for_fresh_ws_ticks(store, user_id, symbols, DHAN_SUBSCRIBE_TICK_CONFIRM_SEC)
    if not missing:
        log.info("MARKET_SUBSCRIBE_TICK_OK | user=%s symbols=%s", user_id, symbols)
        return

    try:
        health = await store.load_broker_feed_health(int(user_id), "DHAN")
    except Exception:
        health = {}
    connected = bool(health.get("connected")) if isinstance(health, dict) else False

    if not connected and _dhan_manages_reconnect(main_app, user_id):
        log.info("MARKET_SUBSCRIBE_RECOVERY_PENDING | user=%s", user_id)
        return

    if not connected:
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

        log.warning("MARKET_SUBSCRIBE_NO_TICK_RESTART | user=%s symbols=%s detail=feed_disconnected", user_id, restart_symbols)
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
            await store.save_broker_feed_health(int(user_id), "DHAN", _dhan_connected(main_app, user_id), ttl_sec=15, detail=detail)
            log.warning("MARKET_SUBSCRIBE_STILL_NO_TICK | user=%s symbols=%s", user_id, still_missing)
        else:
            log.info("MARKET_SUBSCRIBE_TICK_OK_AFTER_RESTART | user=%s symbols=%s", user_id, restart_symbols)
        return

    if not DHAN_SUBSCRIBE_RESTART_ON_NO_TICK:
        detail = "ws_tick_pending:" + ",".join(missing[:5])
        await store.save_broker_feed_health(int(user_id), "DHAN", _dhan_connected(main_app, user_id), ttl_sec=15, detail=detail)
        log.warning("MARKET_SUBSCRIBE_NO_TICK_WAITING | user=%s symbols=%s detail=event_based_feed_rest_fallback_active", user_id, missing)
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

    log.warning("MARKET_SUBSCRIBE_NO_TICK_RESTART | user=%s symbols=%s detail=no_tick_override_enabled", user_id, restart_symbols)
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
        await store.save_broker_feed_health(int(user_id), "DHAN", _dhan_connected(main_app, user_id), ttl_sec=15, detail=detail)
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
            and getattr(main_app.DHAN_FEED, "client_id", "") == str(creds.get("client_id") or "").strip()
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
    if broker == "DHAN":
        await main_app.subscribe_dhan_sector_indices_for_user(user_id)
    await main_app.restart_selected_feed(user_id)
    positions = await store.list_positions(user_id)
    active = [row["symbol"] for row in positions if row.get("symbol")
              and row.get("status") in {"OPEN", "EXITING", "EXIT_CONDITIONS_MET"}]
    if active:
        await main_app.subscribe_symbols_for_user(user_id, active)
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
    # Each queued request is visited at most once per drain, including delayed jobs.
    count = min(max(1, SUBSCRIPTION_DRAIN_LIMIT), await store.redis.llen(MARKET_SUBSCRIPTION_QUEUE))
    for _ in range(count):
        raw = await store.redis.lpop(MARKET_SUBSCRIPTION_QUEUE)
        if not raw:
            return
        try:
            job: Dict[str, Any] = json.loads(raw)
            if not isinstance(job, dict) or not isinstance(job.get("symbols"), list):
                raise ValueError("Invalid subscription job")
            user_id = int(job.get("user_id") or 1)
            if user_id <= 0:
                raise ValueError("Invalid subscription user")
            symbols = [norm_symbol(symbol) for symbol in job["symbols"] if isinstance(symbol, str) and symbol.strip()]
            source = str(job.get("source") or "").strip()
            not_before = float(job.get("not_before") or 0)
            retry_count = int(job.get("retry_count") or 0)
            if not math.isfinite(not_before) or not 0 <= retry_count <= 5:
                raise ValueError("Invalid subscription retry")
        except Exception:
            log.warning("INVALID_MARKET_SUBSCRIPTION_JOB")
            continue
        if not symbols:
            continue
        if not_before > time.time():
            await store.redis.rpush(MARKET_SUBSCRIPTION_QUEUE, raw)
            continue
        try:
            await _ensure_user_feed_started(main_app, store, started_users, user_id)
            result = await main_app.subscribe_symbols_for_user(user_id, symbols)
            missing = result.get("missing", []) if isinstance(result, dict) else []
            sent = not isinstance(result, dict) or result.get("sent")
            if missing or not sent:
                retries = retry_count + 1
                retry_symbols = missing if sent else symbols
                if retries <= 5:
                    job.update(symbols=retry_symbols, retry_count=retries,
                               not_before=time.time() + min(300, 10 * 2 ** (retries - 1)))
                    await store.redis.rpush(MARKET_SUBSCRIPTION_QUEUE, json.dumps(job))
                log.warning("MARKET_SUBSCRIPTION_%s | user=%s count=%s sample=%s attempt=%s",
                            "DEFERRED" if retries <= 5 else "UNRESOLVED", user_id,
                            len(retry_symbols), retry_symbols[:5], retries)
            if sent:
                resolved = [s for s in symbols if s not in missing]
                if resolved:
                    log.info("MARKET_SUBSCRIPTION_SENT | user=%s count=%s sample=%s", user_id, len(resolved), resolved[:5])
                    await _confirm_dhan_alert_symbol_ticks(main_app, store, started_users, user_id, resolved, source)
        except Exception:
            log.exception("Market subscription request failed | user=%s count=%s", user_id, len(symbols))
            retries = retry_count + 1
            if retries <= 5:
                job.update(retry_count=retries, not_before=time.time() + min(300, 10 * 2 ** (retries - 1)))
                await store.redis.rpush(MARKET_SUBSCRIPTION_QUEUE, json.dumps(job))


async def _publish_feed_health(main_app: Any, store: Any, started_users: Set[int]) -> None:
    now = time.time()
    for user_id in list(started_users):
        try:
            broker = str(await store.load_broker(int(user_id)) or "ZERODHA").strip().upper()
            if broker == "DHAN":
                connected = bool(main_app.DHAN_CONNECTED and main_app.DHAN_USER_ID == int(user_id))
            else:
                connected = bool(main_app.KT_CONNECTED and main_app.KT_USER_ID == int(user_id))
            detail = "market_feed_service"
            if broker == "DHAN":
                service = getattr(main_app, "DHAN_FEED", None)
                detail = getattr(getattr(service, "feed", None), "health_detail", None) or getattr(service, "health_detail", detail)
            await store.save_broker_feed_health(
                int(user_id),
                "DHAN" if broker == "DHAN" else "ZERODHA",
                connected,
                ttl_sec=15,
                detail=detail,
            )
            if connected:
                _DISCONNECTED_SINCE.pop(int(user_id), None)
                continue
            # Dhan's receiver owns recovery/backoff. A second watchdog must not
            # recreate it and reset its cooldown or expired-token block.
            if broker == "DHAN" and (_dhan_manages_reconnect(main_app, user_id) or not equity_session_open(connection_window=True)):
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


async def _health_loop(main_app: Any, store: Any, started_users: Set[int]) -> None:
    while True:
        await _publish_feed_health(main_app, store, started_users)
        await asyncio.sleep(max(1.0, FEED_HEALTH_REFRESH_SEC))


async def main() -> None:
    # Reuse existing feed wiring in app.main so Dhan/Kite websocket packet
    # handling remains exactly the same as the dashboard process.
    from . import main as main_app

    store = await init_store()
    loop = asyncio.get_running_loop()
    main_app.store = store
    main_app.APP_LOOP = loop
    main_app.ws_mgr.set_loop(loop)
    started_users: Set[int] = set()
    health_task = asyncio.create_task(_health_loop(main_app, store, started_users), name="feed_health")
    try:
        log.info("Market feed service started")
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
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        await main_app._stop_dhan_feed()
        await main_app._stop_kite_ticker()
        for engine in list(main_app.ENGINE.values()):
            await engine.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
