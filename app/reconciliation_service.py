from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Tuple

from .service_bootstrap import EngineRegistry, configure_logging, init_store, load_user_ids
from .redis_store import norm_symbol
from .trade_engine import Position

configure_logging("reconciliation_service")
log = logging.getLogger("reconciliation_service")

INTERVAL_SEC = float(os.getenv("RECONCILIATION_INTERVAL_SEC", "15") or "15")
EXIT_REST_FALLBACK_ENABLED = (os.getenv("EXIT_REST_FALLBACK_ENABLED", "1") or "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
EXIT_REST_FALLBACK_INTERVAL_SEC = float(os.getenv("EXIT_REST_FALLBACK_INTERVAL_SEC", "2") or "2")
EXIT_FRESH_TICK_MAX_AGE_SEC = float(os.getenv("EXIT_FRESH_TICK_MAX_AGE_SEC", "5") or "5")
_REST_FALLBACK_LAST: Dict[Tuple[int, str], float] = {}


async def _reconcile_position(store, registry: EngineRegistry, user_id: int, row: Dict[str, Any]) -> None:
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        return
    local_qty = int(float(row.get("qty") or 0))
    if local_qty <= 0:
        return
    engine = await registry.get(user_id)
    broker_qty_signed = await engine._fetch_broker_symbol_qty(symbol)
    if broker_qty_signed is None:
        return
    broker_qty = abs(int(broker_qty_signed))
    if broker_qty == local_qty:
        return
    if broker_qty <= 0:
        await store.delete_position(user_id, symbol)
        await store.clear_open(user_id, symbol)
        engine.positions.pop(symbol, None)
        log.warning("RECON_POSITION_CLOSED | user=%s symbol=%s local_qty=%s broker_qty=0", user_id, symbol, local_qty)
        return
    row["qty"] = broker_qty
    row["pending_reason"] = f"BROKER_QTY_RECONCILED:{local_qty}->{broker_qty}"
    row["updated_ts"] = time.time()
    await store.upsert_position(user_id, symbol, row)
    if symbol in engine.positions:
        engine.positions[symbol].qty = broker_qty
        engine.positions[symbol].pending_reason = row["pending_reason"]
        engine.positions[symbol].updated_ts = row["updated_ts"]
    log.warning(
        "RECON_QTY_MISMATCH_FIXED | user=%s symbol=%s local_qty=%s broker_qty=%s",
        user_id,
        symbol,
        local_qty,
        broker_qty,
    )


async def _rest_exit_fallback_position(store, registry: EngineRegistry, user_id: int, row: Dict[str, Any]) -> None:
    if not EXIT_REST_FALLBACK_ENABLED:
        return
    status = str(row.get("status") or "").upper()
    if status not in {"OPEN", "EXIT_CONDITIONS_MET"}:
        return
    symbol = norm_symbol(str(row.get("symbol") or ""))
    if not symbol:
        return
    qty = int(float(row.get("qty") or 0))
    if qty <= 0:
        return

    try:
        latest = await store.load_latest_tick(int(user_id), symbol)
    except Exception:
        latest = {}
    age = float(latest.get("age_sec", 999999.0) or 999999.0)
    ltp = float(latest.get("ltp") or latest.get("last_price") or 0.0)
    latest_source = str(latest.get("source") or "").strip().upper()
    if ltp > 0 and latest_source != "REST_EXIT_FALLBACK" and age <= max(0.5, EXIT_FRESH_TICK_MAX_AGE_SEC):
        return

    now = time.time()
    key = (int(user_id), symbol)
    last = float(_REST_FALLBACK_LAST.get(key) or 0.0)
    if now - last < max(0.5, EXIT_REST_FALLBACK_INTERVAL_SEC):
        return
    _REST_FALLBACK_LAST[key] = now

    engine = await registry.get(user_id)
    if getattr(engine, "_exit_inflight", {}).get(symbol):
        return
    try:
        rest_ltp = float(await engine._fetch_ltp(symbol, prefer_cache=False) or 0.0)
    except Exception as exc:
        log.warning("REST_EXIT_LTP_FAIL | user=%s symbol=%s err=%s", user_id, symbol, exc)
        return
    if rest_ltp <= 0:
        log.warning("REST_EXIT_LTP_EMPTY | user=%s symbol=%s stale_tick_age=%.1fs", user_id, symbol, age)
        return

    try:
        await store.save_latest_tick(
            int(user_id),
            symbol,
            {"ltp": rest_ltp, "close": 0.0, "high": rest_ltp, "low": rest_ltp, "source": "REST_EXIT_FALLBACK"},
            ttl_sec=max(2, int(EXIT_FRESH_TICK_MAX_AGE_SEC)),
        )
    except Exception:
        pass

    log.info(
        "REST_EXIT_FALLBACK_TICK | user=%s symbol=%s ltp=%.2f stale_tick_age=%.1fs",
        user_id,
        symbol,
        rest_ltp,
        age,
    )
    if symbol not in getattr(engine, "positions", {}):
        try:
            data = {}
            for key, field_info in Position.__dataclass_fields__.items():  # type: ignore[attr-defined]
                data[key] = row.get(key, field_info.default)
            data["user_id"] = int(user_id)
            data["symbol"] = symbol
            engine.positions[symbol] = Position(**data)
        except Exception as exc:
            log.warning("REST_EXIT_POSITION_HYDRATE_FAIL | user=%s symbol=%s err=%s", user_id, symbol, exc)
            return
    pos = await engine.on_tick(symbol, rest_ltp, 0.0, rest_ltp, rest_ltp, 0.0, 0.0)
    if pos and str(pos.status).upper() == "OPEN":
        current_reason = str(pos.pending_reason or "")
        if not current_reason or current_reason.startswith("WS_TICK_MISSING"):
            pos.pending_reason = "WS_TICK_MISSING_REST_FALLBACK"
            pos.updated_ts = time.time()
            try:
                await store.upsert_position(int(user_id), symbol, pos.to_public())
            except Exception:
                pass


async def main() -> None:
    store = await init_store()
    registry = EngineRegistry(store)
    try:
        log.info(
            "Reconciliation service started | interval=%ss rest_exit_fallback=%s fallback_interval=%ss",
            INTERVAL_SEC,
            EXIT_REST_FALLBACK_ENABLED,
            EXIT_REST_FALLBACK_INTERVAL_SEC,
        )
        last_reconcile = 0.0
        while True:
            now = time.time()
            for user_id in await load_user_ids(store):
                try:
                    positions = await store.list_positions(user_id)
                    for row in positions:
                        row_dict = dict(row)
                        if now - last_reconcile >= max(1.0, INTERVAL_SEC):
                            await _reconcile_position(store, registry, int(user_id), row_dict)
                        await _rest_exit_fallback_position(store, registry, int(user_id), row_dict)
                except Exception:
                    log.exception("Reconciliation pass failed | user=%s", user_id)
            if now - last_reconcile >= max(1.0, INTERVAL_SEC):
                last_reconcile = now
            await asyncio.sleep(max(0.5, min(1.0, EXIT_REST_FALLBACK_INTERVAL_SEC)))
    finally:
        await registry.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
