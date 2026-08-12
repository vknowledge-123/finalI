from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict

from .service_bootstrap import EngineRegistry, configure_logging, init_store, load_user_ids

configure_logging("reconciliation_service")
log = logging.getLogger("reconciliation_service")

INTERVAL_SEC = float(os.getenv("RECONCILIATION_INTERVAL_SEC", "15") or "15")


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


async def main() -> None:
    store = await init_store()
    registry = EngineRegistry(store)
    try:
        log.info("Reconciliation service started | interval=%ss", INTERVAL_SEC)
        while True:
            for user_id in await load_user_ids(store):
                try:
                    positions = await store.list_positions(user_id)
                    for row in positions:
                        await _reconcile_position(store, registry, int(user_id), dict(row))
                except Exception:
                    log.exception("Reconciliation pass failed | user=%s", user_id)
            await asyncio.sleep(max(1.0, INTERVAL_SEC))
    finally:
        await registry.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())

