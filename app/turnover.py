"""Day-volume x current LTP ranking. Not exchange-reported traded value."""

import asyncio
import math
import os
import time
from datetime import datetime
from pathlib import Path

from .alert_features import IST
from .dhan_feed_policy import equity_session_open
from .redis_store import norm_symbol

SNAPSHOT_MAX_AGE = 10
QUOTE_MAX_AGE = 120
EXCLUSIONS_PATH = Path(__file__).with_name("data") / "turnover_fno_exclusions.txt"


def exclusions():
    return {norm_symbol(line) for line in EXCLUSIONS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def equity_universe(rows, excluded=None):
    """Filter the official master, including EQ/BE/SM equity, not ETFs/indices/F&O.

    Compact and detailed schemas are accepted. Ambiguous symbol IDs fail closed.
    """
    excluded = exclusions() if excluded is None else excluded
    result = {}
    for row in rows:
        def field(compact, detailed):
            value = row.get(compact, row.get(detailed, ""))
            return str(value or "").strip().upper()
        if (field("SEM_EXM_EXCH_ID", "EXCH_ID") != "NSE"
                or field("SEM_SEGMENT", "SEGMENT") != "E"
                or field("SEM_INSTRUMENT_NAME", "INSTRUMENT") != "EQUITY"
                or field("SEM_SERIES", "SERIES") not in {"EQ", "BE", "BZ", "SM", "ST"}
                or field("SEM_EXCH_INSTRUMENT_TYPE", "INSTRUMENT_TYPE") != "ES"):
            continue
        symbol = norm_symbol(field("SEM_TRADING_SYMBOL", "SYMBOL_NAME"))
        sid = field("SEM_SMST_SECURITY_ID", "SECURITY_ID")
        if not symbol or not sid.isdecimal() or symbol in excluded:
            continue
        if symbol in result and result[symbol] != sid:
            raise ValueError("TURNOVER_AMBIGUOUS_INSTRUMENT:" + symbol)
        result[symbol] = sid
    if not result or len(result) > 5000 or len(set(result.values())) != len(result):
        raise ValueError("TURNOVER_UNIVERSE_INVALID")
    return result


class TurnoverBook:
    def __init__(self, universe, now=None, *, min_coverage_pct=None, allow_partial=None):
        self.universe = dict(universe)
        self.symbols = {sid: symbol for symbol, sid in universe.items()}
        self.day = datetime.fromtimestamp(time.time() if now is None else now, IST).strftime("%Y%m%d")
        self.quotes = {}
        self.rejections = {}
        self.connected = False
        self.excluded_symbols = sorted(exclusions())
        self.min_coverage_pct = float(os.getenv("TURNOVER_MIN_COVERAGE_PCT", "99")
                                      if min_coverage_pct is None else min_coverage_pct)
        if not math.isfinite(self.min_coverage_pct) or not 1 <= self.min_coverage_pct <= 100:
            raise ValueError("TURNOVER_MIN_COVERAGE_PCT_INVALID")
        setting = os.getenv("TURNOVER_ALLOW_PARTIAL", "1") if allow_partial is None else str(allow_partial)
        if setting.lower() not in {"1", "0", "true", "false"}:
            raise ValueError("TURNOVER_ALLOW_PARTIAL_INVALID")
        self.allow_partial = setting.lower() in {"1", "true"}

    def reject(self, sid, reason):
        symbol = self.symbols.get(str(sid))
        if symbol:
            self.rejections[symbol] = reason
        return False

    def update(self, sid, price, volume, received_at, *, trade_at=None, now=None, source="UNKNOWN"):
        now = time.time() if now is None else now
        symbol = self.symbols.get(str(sid))
        current = datetime.fromtimestamp(now, IST)
        if self.day != current.strftime("%Y%m%d"):
            self.quotes.clear()
            self.rejections.clear()
            return self.reject(sid, "DAY_CHANGED")
        try:
            price, volume, received_at = float(price), float(volume), float(received_at)
            if (not symbol or not all(math.isfinite(v) for v in (price, volume, received_at))
                    or not math.isfinite(price * volume) or price < 0 or volume < 0 or not volume.is_integer()
                    or (volume > 0 and price <= 0) or not 0 <= now - received_at <= QUOTE_MAX_AGE
                    or not equity_session_open(current)):
                return self.reject(sid, "INVALID_OR_STALE_QUOTE")
            # A prior-day last trade cannot establish today's nonzero volume.
            if volume > 0:
                trade_date = datetime.fromtimestamp(float(trade_at), IST)
                if trade_date.date() != current.date() or float(trade_at) > now + 2:
                    return self.reject(sid, "INVALID_TRADE_DAY")
        except (ValueError, TypeError, OverflowError, OSError):
            return self.reject(sid, "INVALID_QUOTE_FIELDS")
        previous = self.quotes.get(symbol)
        if previous and (received_at < previous["ts"] or volume < previous["volume"]):
            return self.reject(sid, "OUT_OF_ORDER_OR_VOLUME_REGRESSION")
        self.quotes[symbol] = {"symbol": symbol, "security_id": str(sid), "ltp": price,
                               "volume": int(volume), "turnover": price * volume, "ts": received_at,
                               "source": source}
        self.rejections.pop(symbol, None)
        return True

    def pending(self, now=None):
        now = time.time() if now is None else now
        return [{"symbol": symbol, "security_id": sid,
                 "reason": self.rejections.get(symbol, "QUOTE_STALE" if symbol in self.quotes else "QUOTE_MISSING"),
                 "previously_observed": symbol in self.quotes}
                for symbol, sid in self.universe.items()
                if symbol not in self.quotes or not 0 <= now - self.quotes[symbol]["ts"] <= QUOTE_MAX_AGE]

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        current = datetime.fromtimestamp(now, IST)
        # Retain stale records in rank order so their disappearance cannot promote
        # another candidate into the top N. Decisions guard stale leaders below.
        rows = [dict(row, stale=not 0 <= now - row["ts"] <= QUOTE_MAX_AGE) for row in self.quotes.values()]
        rows.sort(key=lambda row: (-row["turnover"], row["symbol"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        session = equity_session_open(current) and current.strftime("%Y%m%d") == self.day
        covered = sum(not row["stale"] for row in rows)
        coverage = covered * 100 / len(self.universe) if self.universe else 0
        complete = bool(self.universe and covered == len(self.universe))
        sufficient = complete or (self.allow_partial and coverage >= self.min_coverage_pct)
        ready = bool(session and self.connected and self.universe and sufficient)
        state = ("UNAVAILABLE" if not session or not self.connected else
                 "COMPLETE" if complete else "PARTIAL" if ready else "WARMING_UP")
        reason = ("READY" if ready and complete else "TURNOVER_RANK_PARTIAL" if ready else "TURNOVER_SESSION_CLOSED" if not session else
                  "TURNOVER_FEED_DISCONNECTED" if not self.connected else "TURNOVER_RANK_NOT_READY")
        return {"ready": ready, "reason": reason, "trading_day": self.day, "ts": now,
                "state": state, "coverage_pct": coverage, "min_coverage_pct": self.min_coverage_pct,
                "allow_partial": self.allow_partial, "missing": self.pending(now),
                "missing_count": len(self.universe) - len(rows), "stale_count": len(rows) - covered,
                "ranking_scope": "FULL_UNIVERSE" if complete else "OBSERVED_STOCKS",
                "connected": self.connected, "covered": covered, "total": len(self.universe),
                "formula": "cumulative_day_volume * ltp", "rows": rows,
                "excluded_symbols": self.excluded_symbols}


def turnover_decision(snapshot, symbol, top_n, now=None):
    now = time.time() if now is None else now
    try:
        if not snapshot or not 0 <= now - float(snapshot["ts"]) <= SNAPSHOT_MAX_AGE:
            return False, "TURNOVER_RANK_STALE"
        if snapshot["trading_day"] != datetime.fromtimestamp(now, IST).strftime("%Y%m%d"):
            return False, "TURNOVER_RANK_STALE"
        if not equity_session_open(datetime.fromtimestamp(now, IST)):
            return False, "TURNOVER_SESSION_CLOSED"
        if not snapshot.get("ready"):
            return False, str(snapshot.get("reason") or "TURNOVER_RANK_NOT_READY")
        if "min_coverage_pct" in snapshot:
            total = int(snapshot["total"])
            covered = sum(0 <= now - float(r["ts"]) <= QUOTE_MAX_AGE for r in snapshot["rows"])
            threshold = float(snapshot["min_coverage_pct"]) if snapshot.get("allow_partial") else 100
            if not snapshot.get("connected") or total <= 0 or covered * 100 / total < threshold:
                return False, "TURNOVER_RANK_NOT_READY"
        symbol = norm_symbol(symbol)
        if symbol in snapshot.get("excluded_symbols", []):
            return False, "TURNOVER_FNO_EXCLUDED"
        rows = snapshot["rows"]
        row = next((r for r in rows if r["symbol"] == symbol), None)
        if row is None:
            if any(r["symbol"] == symbol for r in snapshot.get("missing", [])):
                return False, "TURNOVER_QUOTE_MISSING"
            return False, "TURNOVER_SYMBOL_NOT_IN_UNIVERSE"
        if not 0 <= now - float(row["ts"]) <= QUOTE_MAX_AGE:
            return False, "TURNOVER_RANK_STALE"
        if row["rank"] > int(top_n) or row["turnover"] <= 0:
            return False, "TURNOVER_FILTER"
        if any(r.get("stale") or not 0 <= now - float(r["ts"]) <= QUOTE_MAX_AGE
               for r in rows if r["rank"] <= int(top_n)):
            return False, "TURNOVER_TOP_N_STALE"
        return True, "TURNOVER_ALLOWED"
    except (TypeError, KeyError, ValueError):
        return False, "TURNOVER_RANK_NOT_READY"


async def candidate_turnover_decision(store, user_id, snapshot, symbol, top_n, *, wait_seconds=5):
    """Ask the read-only worker to refresh a missing candidate; never fetch in execution."""
    decision = turnover_decision(snapshot, symbol, top_n)
    redis = getattr(store, "redis", None)
    symbol = norm_symbol(symbol)
    missing = {r["symbol"] for r in snapshot.get("missing", [])}
    missing |= {r["symbol"] for r in snapshot.get("rows", [])
                if not 0 <= time.time() - float(r["ts"]) <= QUOTE_MAX_AGE}
    targets = {symbol} & missing
    if decision[1] == "TURNOVER_TOP_N_STALE":
        targets |= {r["symbol"] for r in snapshot.get("rows", [])
                    if r["rank"] <= int(top_n) and (r.get("stale") or r["symbol"] in missing)}
    if decision[0] or not targets or redis is None:
        return decision

    async def refresh_and_wait():
        nonlocal decision
        key = f"turnover:refresh:{int(user_id)}"
        async with redis.pipeline(transaction=True) as pipe:
            pipe.zadd(key, {s: time.time() for s in targets})
            pipe.expire(key, 30)
            await pipe.execute()
        while True:
            await asyncio.sleep(0.5)
            snapshot = await store.load_turnover_snapshot(user_id)
            decision = turnover_decision(snapshot, symbol, top_n)
            if decision[0] or decision[1] in {"TURNOVER_FILTER", "TURNOVER_SESSION_CLOSED", "TURNOVER_FEED_DISCONNECTED"}:
                return decision

    try:
        return await asyncio.wait_for(refresh_and_wait(), timeout=max(0, min(5, wait_seconds)))
    except asyncio.TimeoutError:
        return decision
    except Exception:
        return False, "TURNOVER_RANK_NOT_READY"
