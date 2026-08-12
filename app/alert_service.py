from __future__ import annotations

import json
import time
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .chartink_client import normalize_alert_name, normalize_symbol, normalize_symbols, parse_chartink_payload
from .service_bootstrap import configure_logging, init_store
from .service_queues import ALERT_QUEUE

configure_logging("alert_service")

app = FastAPI(title="AlgoEdge Alert Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = None


async def _read_payload(request: Request) -> Dict[str, Any]:
    if request.method == "GET":
        payload: Dict[str, Any] = {}
        for key in request.query_params.keys():
            values = request.query_params.getlist(key)
            payload[key] = values if len(values) > 1 else (values[0] if values else "")
        return payload
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            return await request.json()
        except Exception:
            return {}
    try:
        form = await request.form()
        if form:
            payload: Dict[str, Any] = {}
            for key, value in form.multi_items():
                if key in payload:
                    if not isinstance(payload[key], list):
                        payload[key] = [payload[key]]
                    payload[key].append(value)
                else:
                    payload[key] = value
            return payload
    except Exception:
        pass
    try:
        raw = (await request.body() or b"").decode("utf-8", errors="ignore").strip()
        if raw.startswith("{") and raw.endswith("}"):
            return json.loads(raw)
    except Exception:
        pass
    return {}


@app.on_event("startup")
async def startup() -> None:
    global store
    store = await init_store()


@app.on_event("shutdown")
async def shutdown() -> None:
    if store is not None:
        await store.close()


@app.get("/health")
async def health() -> Dict[str, Any]:
    depth = await store.redis.llen(ALERT_QUEUE) if store is not None else -1
    return {"ok": True, "service": "alert", "queue": ALERT_QUEUE, "queue_depth": depth}


@app.api_route("/webhook/chartink", methods=["POST", "GET"])
async def chartink_webhook(request: Request, user_id: int = 1) -> Dict[str, Any]:
    started = time.perf_counter()
    payload = await _read_payload(request)
    alert_name_raw, symbols_raw, ts = parse_chartink_payload(payload)
    alert_name = normalize_alert_name(alert_name_raw)
    symbols = []
    seen = set()
    for raw in normalize_symbols(symbols_raw):
        symbol = normalize_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)

    initial_result = [{"symbol": symbol, "status": "QUEUED"} for symbol in symbols]
    if not symbols:
        initial_result = [{"symbol": "", "status": "ERROR", "reason": "NO_SYMBOLS_PARSED"}]

    job = {
        "user_id": int(user_id),
        "alert_name": alert_name,
        "symbols": symbols,
        "timestamp": str(ts or ""),
        "payload_keys": sorted(str(key) for key in payload.keys())[:30],
        "queued_ts": time.time(),
    }
    await store.save_alert(
        int(user_id),
        {
            "alert_name": alert_name,
            "time": str(ts or ""),
            "symbols": symbols,
            "result": initial_result,
        },
    )
    if symbols:
        await store.redis.rpush(ALERT_QUEUE, json.dumps(job, separators=(",", ":")))

    return {
        "ok": True,
        "service": "alert",
        "queued": bool(symbols),
        "alert": alert_name,
        "symbols": symbols,
        "result": initial_result,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
    }

