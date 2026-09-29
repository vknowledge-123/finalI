"""Read-only, bounded polling of Dhan's account positions P&L."""

import asyncio
import hashlib
import json
import logging
import math
import time
from decimal import Decimal, InvalidOperation

import httpx

from ..redis_store import now_ist_date

log = logging.getLogger(__name__)


def dhan_account_totals(rows):
    if not isinstance(rows, list):
        raise ValueError("DHAN_MTM_INVALID_RESPONSE")
    totals = {"realized": Decimal(0), "unrealized": Decimal(0)}
    for row in rows:
        for name, field in (("realized", "realizedProfit"), ("unrealized", "unrealizedProfit")):
            if not isinstance(row, dict) or field not in row or isinstance(row[field], bool):
                raise ValueError("DHAN_MTM_INVALID_RESPONSE")
            try:
                value = Decimal(str(row[field]))
            except InvalidOperation as exc:
                raise ValueError("DHAN_MTM_INVALID_RESPONSE") from exc
            if not value.is_finite():
                raise ValueError("DHAN_MTM_INVALID_RESPONSE")
            totals[name] += value
    totals["total"] = totals["realized"] + totals["unrealized"]
    result = {key: float(value) for key, value in totals.items()}
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("DHAN_MTM_INVALID_RESPONSE")
    return result


class AccountMtmService:
    def __init__(self, store_provider, client=None):
        self.store_provider = store_provider
        self.client = client
        self._cache = {}
        self._locks = {}

    async def close(self):
        if self.client is not None:
            await self.client.aclose()
        self._cache.clear()
        self._locks.clear()

    async def snapshot(self, user_id):
        uid = int(user_id)
        async with self._locks.setdefault(uid, asyncio.Lock()):
            store = self.store_provider()
            broker = await store.load_broker(uid)
            credentials = await store.load_dhan_credentials(uid) if broker == "DHAN" else {}
            client_id = str(credentials.get("client_id") or "").strip()
            token = str(credentials.get("access_token") or "").strip()
            # Never reuse one account/day's MTM after credentials or broker change.
            identity = hashlib.sha256(json.dumps([broker, client_id, token, now_ist_date()]).encode()).hexdigest()
            previous = self._cache.get(uid, {})
            if previous.get("identity") != identity:
                previous = {}
                self._cache.pop(uid, None)
            empty = {"ok": False, "broker": broker, "total": None, "realized": None,
                     "unrealized": None, "as_of": None}
            if broker != "DHAN":
                return dict(empty, status="NOT_APPLICABLE")
            if not client_id or not token:
                return dict(empty, status="NOT_CONNECTED")
            if time.monotonic() < previous.get("retry_at", 0):
                return dict(previous["snapshot"])
            try:
                if self.client is None:
                    self.client = httpx.AsyncClient(timeout=5, follow_redirects=False)
                response = await asyncio.wait_for(self.client.get(
                    "https://api.dhan.co/v2/positions",
                    headers={"access-token": token, "client-id": client_id},
                ), timeout=6)
                response.raise_for_status()
                totals = dhan_account_totals(response.json())
                result = dict(totals, ok=True, broker="DHAN", status="FRESH", as_of=time.time())
            except Exception as exc:
                log.warning("DHAN_ACCOUNT_MTM_FAILED | user=%s type=%s", uid, type(exc).__name__)
                last = previous.get("snapshot", {})
                if last.get("as_of") is not None:
                    result = dict(last, ok=False, status="STALE")
                else:
                    result = dict(empty, status="UNAVAILABLE")
            self._cache[uid] = {"identity": identity, "snapshot": result, "retry_at": time.monotonic() + 5}
            return dict(result)
