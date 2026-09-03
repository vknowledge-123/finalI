from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List

from fastapi import Request

from ..chartink_client import normalize_alert_name, normalize_symbol, normalize_symbols, parse_chartink_payload
from ..dhan_broker import compact_broker_error
from .contracts import ChartinkSignalJob
from .job_queue import AsyncJobQueue
from .notification_service import NotificationService
from .trade_decision import TradeDecisionService

log = logging.getLogger("trade_engine")


class SignalIntakeService:
    """Chartink intake + queued strategy/trade dispatch."""

    def __init__(
        self,
        *,
        subscribe_symbols: Callable[[int, List[str]], Awaitable[None]],
        notification_service: NotificationService,
        trade_decision_service: TradeDecisionService,
        workers: int = 4,
    ) -> None:
        self.subscribe_symbols = subscribe_symbols
        self.notification = notification_service
        self.trade_decision = trade_decision_service
        self.queue: AsyncJobQueue[ChartinkSignalJob, Dict[str, Any]] = AsyncJobQueue(
            "signal_intake",
            self._process_job,
            workers=workers,
            maxsize=1000,
        )

    async def start(self) -> None:
        await self.queue.start()

    async def stop(self) -> None:
        await self.queue.stop()

    async def submit_request(self, request: Request, user_id: int) -> Dict[str, Any]:
        started = time.perf_counter()
        payload, content_type = await self._read_payload(request)
        alert_name_raw, symbols_raw, ts = parse_chartink_payload(payload)
        alert_name = normalize_alert_name(alert_name_raw)
        symbols = self._clean_symbols(symbols_raw)
        payload_keys = sorted([str(k) for k in (payload or {}).keys()])[:30]

        log.info(
            "WEBHOOK_RECEIVED | user=%s method=%s ct=%s alert=%s symbols=%s keys=%s",
            user_id,
            request.method,
            content_type,
            alert_name,
            len(symbols),
            payload_keys,
        )

        job = ChartinkSignalJob(
            user_id=int(user_id),
            alert_name=alert_name,
            symbols=symbols,
            timestamp=str(ts or ""),
            content_type=content_type,
            method=request.method,
            payload_keys=payload_keys,
        )
        result = await self.queue.submit(job)
        result["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        return result

    async def _read_payload(self, request: Request) -> tuple[Dict[str, Any], str]:
        payload: Dict[str, Any] = {}
        content_type = (request.headers.get("content-type") or "").lower()

        if request.method == "GET":
            try:
                for key in request.query_params.keys():
                    values = request.query_params.getlist(key)
                    payload[key] = values if len(values) > 1 else (values[0] if values else "")
            except Exception:
                payload = {}
            return payload, content_type

        if "application/json" in content_type:
            try:
                return await request.json(), content_type
            except Exception:
                return {}, content_type

        try:
            form = await request.form()
            if form:
                tmp: Dict[str, Any] = {}
                for key, value in form.multi_items():
                    if key in tmp:
                        if not isinstance(tmp[key], list):
                            tmp[key] = [tmp[key]]
                        tmp[key].append(value)
                    else:
                        tmp[key] = value
                payload = tmp
        except Exception:
            payload = {}

        if not payload:
            try:
                raw = (await request.body() or b"").decode("utf-8", errors="ignore").strip()
                if raw.startswith("{") and raw.endswith("}"):
                    payload = json.loads(raw)
            except Exception:
                payload = {}
        return payload, content_type

    def _clean_symbols(self, symbols_raw: Any) -> List[str]:
        symbols0 = normalize_symbols(symbols_raw)
        cleaned: List[str] = []
        seen = set()
        for item in symbols0:
            symbol = normalize_symbol(item)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            cleaned.append(symbol)
        return cleaned

    async def _process_job(self, job: ChartinkSignalJob) -> Dict[str, Any]:
        user_id = int(job.user_id)
        symbols = list(job.symbols)

        if not symbols:
            result = [{"symbol": "", "status": "ERROR", "reason": "NO_SYMBOLS_PARSED"}]
            await asyncio.gather(
                self.notification.save_alert_received(user_id, job.alert_name, job.timestamp, [], result),
                self.notification.broadcast_alert(user_id, job.alert_name, job.timestamp, [], result),
            )
            return {
                "ok": True,
                "warning": "NO_SYMBOLS_PARSED",
                "alert": job.alert_name,
                "symbols": [],
                "result": result,
                "content_type": job.content_type,
            }

        asyncio.create_task(self.subscribe_symbols(user_id, symbols), name=f"subscribe_alert_{user_id}")

        initial_result = [{"symbol": symbol, "status": "RECEIVED"} for symbol in symbols]
        initial_save_task = asyncio.create_task(
            self.notification.save_alert_received(
                user_id,
                job.alert_name,
                job.timestamp,
                symbols,
                initial_result,
            ),
            name=f"alert_received_{user_id}",
        )

        try:
            result = await self.trade_decision.process_chartink_signal(job)
        except Exception as exc:
            log.exception("WEBHOOK_PANIC | user=%s alert=%s", user_id, job.alert_name)
            await self.notification.store.set_kill(user_id, True)
            result = [
                {"symbol": symbol, "status": "ERROR", "reason": f"CRITICAL_FAIL:{compact_broker_error(exc)}"}
                for symbol in symbols
            ]

        execution_symbols = [
            normalize_symbol(str(row.get("execution_symbol") or ""))
            for row in (result or [])
            if isinstance(row, dict) and str(row.get("status") or "").upper() == "ENTERED"
        ]
        execution_symbols = [symbol for symbol in execution_symbols if symbol and symbol not in symbols]
        if execution_symbols:
            asyncio.create_task(self.subscribe_symbols(user_id, execution_symbols), name=f"subscribe_exec_{user_id}")

        try:
            await initial_save_task
        except Exception as exc:
            log.warning("ALERT_INITIAL_SAVE_FAIL | user=%s alert=%s err=%s", user_id, job.alert_name, exc)

        await asyncio.gather(
            self.notification.save_alert_result(user_id, job.alert_name, job.timestamp, result),
            self.notification.broadcast_alert(user_id, job.alert_name, job.timestamp, symbols, result),
        )

        log.info(
            "WEBHOOK_RESULT | user=%s alert=%s symbols=%s result=%s",
            user_id,
            job.alert_name,
            symbols,
            result,
        )
        return {
            "ok": True,
            "alert": job.alert_name,
            "symbols": symbols,
            "result": result,
            "content_type": job.content_type,
        }
