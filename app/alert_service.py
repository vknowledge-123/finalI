from __future__ import annotations

import json
import logging
import hmac
import os
import time
import uuid
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .chartink_client import normalize_alert_name, normalize_symbol, normalize_symbols, parse_chartink_payload
from .service_bootstrap import configure_logging, init_store
from .service_queues import ALERT_QUEUE, MARKET_SUBSCRIPTION_QUEUE

configure_logging("alert_service")
log = logging.getLogger("alert_service")

app = FastAPI(title="AlgoEdge Alert Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = None
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()


def _json_error_response(
    request: Request,
    status_code: int,
    error: str,
    detail: Any = "",
    *,
    request_id: str = "",
) -> JSONResponse:
    rid = request_id or request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    return JSONResponse(
        status_code=int(status_code),
        content={
            "ok": False,
            "error": str(error or "ERROR"),
            "detail": detail if isinstance(detail, (str, int, float, bool, list, dict)) else str(detail),
            "request_id": rid,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if exc.detail is not None else "HTTP_ERROR"
    return _json_error_response(request, exc.status_code, str(detail), detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _json_error_response(request, 422, "REQUEST_VALIDATION_ERROR", exc.errors())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    log.exception(
        "UNHANDLED_ALERT_EXCEPTION | request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
    )
    return _json_error_response(
        request,
        500,
        "INTERNAL_SERVER_ERROR",
        "Unexpected server error. Check alert-service logs with request_id.",
        request_id=request_id,
    )


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


def _webhook_secret_valid(request: Request) -> bool:
    if not WEBHOOK_SECRET:
        return True
    provided = str(request.query_params.get("secret") or request.headers.get("X-Webhook-Secret") or "").strip()
    return bool(provided) and hmac.compare_digest(provided, WEBHOOK_SECRET)


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
    redis_client = getattr(store, "redis", None) if store is not None else None
    depth = await redis_client.llen(ALERT_QUEUE) if redis_client is not None else -1
    sub_depth = await redis_client.llen(MARKET_SUBSCRIPTION_QUEUE) if redis_client is not None else -1
    return {
        "ok": True,
        "service": "alert",
        "queue": ALERT_QUEUE,
        "queue_depth": depth,
        "market_subscription_queue": MARKET_SUBSCRIPTION_QUEUE,
        "market_subscription_queue_depth": sub_depth,
    }


@app.api_route("/webhook/chartink", methods=["POST", "GET"])
async def chartink_webhook(request: Request, user_id: int = 1) -> Dict[str, Any]:
    if not _webhook_secret_valid(request):
        raise HTTPException(status_code=401, detail="WEBHOOK_SECRET_INVALID")
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
        await store.redis.rpush(
            MARKET_SUBSCRIPTION_QUEUE,
            json.dumps(
                {
                    "user_id": int(user_id),
                    "symbols": symbols,
                    "source": "chartink_alert",
                    "timestamp": time.time(),
                },
                separators=(",", ":"),
            ),
        )
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
