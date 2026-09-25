"""Day-volume x current LTP ranking. Not exchange-reported traded value."""

import math
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
    def __init__(self, universe, now=None):
        self.universe = dict(universe)
        self.symbols = {sid: symbol for symbol, sid in universe.items()}
        self.day = datetime.fromtimestamp(time.time() if now is None else now, IST).strftime("%Y%m%d")
        self.quotes = {}
        self.connected = False
        self.excluded_symbols = sorted(exclusions())

    def update(self, sid, price, volume, received_at, *, trade_at=None, now=None):
        now = time.time() if now is None else now
        symbol = self.symbols.get(str(sid))
        current = datetime.fromtimestamp(now, IST)
        if self.day != current.strftime("%Y%m%d"):
            self.quotes.clear()
            return False
        try:
            price, volume, received_at = float(price), float(volume), float(received_at)
            if (not symbol or not all(math.isfinite(v) for v in (price, volume, received_at))
                    or not math.isfinite(price * volume) or price < 0 or volume < 0 or not volume.is_integer()
                    or (volume > 0 and price <= 0) or not 0 <= now - received_at <= QUOTE_MAX_AGE
                    or not equity_session_open(current)):
                return False
            # A prior-day last trade cannot establish today's nonzero volume.
            if volume > 0:
                trade_date = datetime.fromtimestamp(float(trade_at), IST)
                if trade_date.date() != current.date() or float(trade_at) > now + 2:
                    return False
        except (ValueError, TypeError, OverflowError, OSError):
            return False
        previous = self.quotes.get(symbol)
        if previous and (received_at < previous["ts"] or volume < previous["volume"]):
            return False
        self.quotes[symbol] = {"symbol": symbol, "security_id": str(sid), "ltp": price,
                               "volume": int(volume), "turnover": price * volume, "ts": received_at}
        return True

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        current = datetime.fromtimestamp(now, IST)
        rows = [dict(row) for row in self.quotes.values() if 0 <= now - row["ts"] <= QUOTE_MAX_AGE]
        rows.sort(key=lambda row: (-row["turnover"], row["symbol"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        session = equity_session_open(current) and current.strftime("%Y%m%d") == self.day
        ready = bool(session and self.connected and self.universe and len(rows) == len(self.universe))
        reason = ("READY" if ready else "TURNOVER_SESSION_CLOSED" if not session else
                  "TURNOVER_FEED_DISCONNECTED" if not self.connected else "TURNOVER_RANK_NOT_READY")
        return {"ready": ready, "reason": reason, "trading_day": self.day, "ts": now,
                "connected": self.connected, "covered": len(rows), "total": len(self.universe),
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
        symbol = norm_symbol(symbol)
        if symbol in snapshot.get("excluded_symbols", []):
            return False, "TURNOVER_FNO_EXCLUDED"
        rows = snapshot["rows"]
        row = next((r for r in rows if r["symbol"] == symbol), None)
        if row is None:
            return False, "TURNOVER_SYMBOL_NOT_IN_UNIVERSE"
        if not 0 <= now - float(row["ts"]) <= QUOTE_MAX_AGE:
            return False, "TURNOVER_RANK_STALE"
        if row["rank"] > int(top_n) or row["turnover"] <= 0:
            return False, "TURNOVER_FILTER"
        return True, "TURNOVER_ALLOWED"
    except (TypeError, KeyError, ValueError):
        return False, "TURNOVER_RANK_NOT_READY"
