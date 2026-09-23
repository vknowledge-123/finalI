from __future__ import annotations

import asyncio
import inspect
import json
import logging
import struct
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd
import pytz
import websockets

from .dhan_feed_policy import FeedBackoff, error_status, safe_feed_error, token_expired

try:  # Keep the app importable if the broker SDK changes or is temporarily broken.
    from dhanhq import DhanContext, MarketFeed, OrderUpdate, dhanhq

    DHAN_SDK_AVAILABLE = True
    DHAN_SDK_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - exercised only when SDK import fails
    DHAN_SDK_AVAILABLE = False
    DHAN_SDK_IMPORT_ERROR = compact_msg = str(exc)

    class DhanContext:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"DHAN_SDK_UNAVAILABLE:{compact_msg}")

    class MarketFeed:  # type: ignore[no-redef]
        NSE = 1
        IDX = 0
        NSE_FNO = 2
        Full = 21

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"DHAN_MARKET_FEED_UNAVAILABLE:{compact_msg}")

    class OrderUpdate:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"DHAN_ORDER_UPDATE_UNAVAILABLE:{compact_msg}")

    def dhanhq(*_args: Any, **_kwargs: Any) -> Any:  # type: ignore[no-redef]
        raise RuntimeError(f"DHAN_SDK_UNAVAILABLE:{compact_msg}")

from .redis_store import norm_symbol

log = logging.getLogger("dhan_broker")

DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


def _value(row: Any, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() and str(value).lower() != "nan":
            return str(value).strip()
    return ""


class DhanInstrumentRegistry:
    """In-memory NSE equity symbol/security-id map from Dhan's official master."""

    def __init__(self) -> None:
        self.symbol_to_security: Dict[str, str] = {}
        self.security_to_symbol: Dict[str, str] = {}
        self.security_to_feed_segment: Dict[str, int] = {}
        self.security_to_tick_size: Dict[str, float] = {}
        self.symbol_to_segment: Dict[str, int] = {}
        self.instrument_to_symbol: Dict[tuple, str] = {}
        self.instrument_to_tick: Dict[tuple, float] = {}
        self.security_segments: Dict[str, set] = {}
        self._master_frame: Optional[pd.DataFrame] = None
        self._lock = asyncio.Lock()
        self.loaded_at: Optional[datetime] = None

    INDEX_SECURITY_IDS = {
        "NIFTY": "13",
        "NIFTY50": "13",
        "BANKNIFTY": "25",
        "NIFTYBANK": "25",
        "NIFTY BANK": "25",
    }

    INDEX_DISPLAY = {
        "13": "NIFTY",
        "25": "BANKNIFTY",
    }

    async def ensure_loaded(self, force: bool = False) -> bool:
        if self.loaded_at is not None and self.symbol_to_security and not force:
            return True
        async with self._lock:
            if self.loaded_at is not None and self.symbol_to_security and not force:
                return True
            try:
                frame = await self._load_master_frame(force=force)
                records = await asyncio.to_thread(self._parse_equity_master, frame)
                if not records:
                    raise RuntimeError("DHAN_SCRIP_MASTER_EMPTY")
                replacement = DhanInstrumentRegistry()
                for symbol, segment in self.symbol_to_segment.items():
                    if segment != MarketFeed.NSE:
                        sid = self.symbol_to_security[symbol]
                        replacement.register_instrument(symbol, sid, segment,
                            self.instrument_to_tick.get((segment, sid)))
                for symbol, security_id, tick in records:
                    replacement.register_instrument(symbol, security_id, MarketFeed.NSE, tick)
                for symbol, security_id in self.INDEX_SECURITY_IDS.items():
                    replacement.register_instrument(symbol, security_id, MarketFeed.IDX, 0.05)
                # Publish together on the owning loop; remove obsolete equity IDs
                # only after a successful refresh, preserving other segments.
                for name in ("symbol_to_security", "symbol_to_segment", "security_to_symbol",
                             "security_to_feed_segment", "security_to_tick_size", "instrument_to_symbol",
                             "instrument_to_tick", "security_segments"):
                    setattr(self, name, getattr(replacement, name))
                self.loaded_at = datetime.now()
                log.info("Loaded %s Dhan NSE equity instruments", len(records))
                return True
            except Exception as exc:
                log.error("Dhan instrument master load failed: %s", exc)
                return False

    @staticmethod
    def _parse_equity_master(frame):
        def column(*names):
            for name in names:
                if name in frame:
                    return frame[name].fillna("").astype(str).str.strip().str.upper()
            return pd.Series("", index=frame.index)

        mask = ((column("SEM_EXM_EXCH_ID", "EXCH_ID") == "NSE")
                & column("SEM_SEGMENT", "SEGMENT").isin(["E", "C"])
                & column("SEM_SERIES", "SERIES").isin(["", "EQ", "BE", "BZ"])
                & column("SEM_INSTRUMENT_NAME", "INSTRUMENT").isin(["", "EQUITY", "EQ"]))
        result = []
        for row in frame.loc[mask].to_dict("records"):
            symbol = norm_symbol(_value(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL"))
            sid = _value(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
            if symbol and sid:
                result.append((symbol, sid, _value(row, "SEM_TICK_SIZE", "TICK_SIZE")))
        return result

    async def _load_master_frame(self, force: bool = False) -> pd.DataFrame:
        if self._master_frame is not None and not force:
            return self._master_frame
        frame = await asyncio.to_thread(
            pd.read_csv,
            DHAN_SCRIP_MASTER_URL,
            low_memory=False,
        )
        self._master_frame = frame
        return frame

    async def security_id(self, symbol: str) -> Optional[str]:
        normalized = norm_symbol(symbol)
        if normalized in self.INDEX_SECURITY_IDS:
            return self.INDEX_SECURITY_IDS.get(normalized)
        if normalized in self.symbol_to_security:
            return self.symbol_to_security[normalized]
        await self.ensure_loaded()
        return self.symbol_to_security.get(normalized)

    def is_index_symbol(self, symbol: str) -> bool:
        return norm_symbol(symbol) in self.INDEX_SECURITY_IDS

    def index_security_id(self, symbol: str) -> Optional[str]:
        return self.INDEX_SECURITY_IDS.get(norm_symbol(symbol))

    async def atm_index_option(
        self,
        underlying: str,
        side: str,
        spot_price: float,
        today: Optional[date] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized = norm_symbol(underlying)
        if normalized in {"NIFTY50", "NIFTY BANK"}:
            normalized = "NIFTY" if normalized == "NIFTY50" else "BANKNIFTY"
        if normalized not in {"NIFTY", "BANKNIFTY"}:
            return None
        option_type = "CE" if str(side).upper() == "BUY" else "PE"
        strike_step = 50 if normalized == "NIFTY" else 100
        atm_strike = round(float(spot_price) / strike_step) * strike_step
        frame = await self._load_master_frame()
        if frame.empty:
            return None
        today = today or datetime.now().date()

        rows = frame.copy()
        for col in ("SEM_TRADING_SYMBOL", "SEM_SEGMENT", "SEM_EXPIRY_DATE"):
            if col not in rows.columns:
                return None
        rows = rows[
            (rows["SEM_TRADING_SYMBOL"].astype(str).str.contains(normalized, na=False))
            & (rows["SEM_SEGMENT"].astype(str).str.upper() == "OPTIDX")
        ].copy()
        if rows.empty:
            return None

        rows["_expiry"] = pd.to_datetime(rows["SEM_EXPIRY_DATE"], errors="coerce").dt.date
        rows = rows[rows["_expiry"].notna() & (rows["_expiry"] >= today)]
        if rows.empty:
            return None

        if "SEM_OPTION_TYPE" in rows.columns:
            rows = rows[rows["SEM_OPTION_TYPE"].astype(str).str.upper().str.endswith(option_type)]
        else:
            rows = rows[rows["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.endswith(option_type)]
        if rows.empty:
            return None

        strike_col = "SEM_STRIKE_PRICE" if "SEM_STRIKE_PRICE" in rows.columns else ""
        if strike_col:
            rows["_strike"] = pd.to_numeric(rows[strike_col], errors="coerce")
            rows = rows[rows["_strike"].notna()]
            rows["_strike_distance"] = (rows["_strike"] - atm_strike).abs()
        else:
            rows["_strike_distance"] = rows["SEM_TRADING_SYMBOL"].astype(str).str.extract(r"(\d+)(?:CE|PE)$")[0]
            rows["_strike_distance"] = pd.to_numeric(rows["_strike_distance"], errors="coerce").sub(atm_strike).abs()
            rows = rows[rows["_strike_distance"].notna()]
        if rows.empty:
            return None

        rows = rows.sort_values(["_expiry", "_strike_distance"])
        row = rows.iloc[0]
        security_id = _value(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
        trading_symbol = norm_symbol(_value(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL"))
        if not security_id or not trading_symbol:
            return None
        self.register_instrument(
            trading_symbol,
            security_id,
            MarketFeed.NSE_FNO,
            self._normalise_tick_size(_value(row, "SEM_TICK_SIZE", "TICK_SIZE")),
        )
        return {
            "security_id": security_id,
            "trading_symbol": trading_symbol,
            "underlying": normalized,
            "option_type": option_type,
            "strike": float(row.get("_strike", atm_strike) or atm_strike),
            "expiry": str(row.get("_expiry") or ""),
        }

    @staticmethod
    def _normalise_tick_size(value: Any) -> float:
        try:
            tick = float(value or 0.0)
        except Exception:
            tick = 0.0
        if tick <= 0:
            return 0.05
        # Dhan equity/derivative master reports tick in paise for many NSE rows:
        # 5 => Rs 0.05, 10 => Rs 0.10. Sub-rupee values are already rupees.
        if tick >= 1.0:
            tick = tick / 100.0
        return max(0.01, float(tick))

    def register_instrument(
        self,
        symbol: str,
        security_id: str,
        feed_segment: Optional[int] = None,
        tick_size: Optional[float] = None,
    ) -> None:
        normalized = norm_symbol(symbol)
        security_id = str(security_id or "").strip()
        if not normalized or not security_id:
            return
        self.symbol_to_security[normalized] = security_id
        segment = MarketFeed.NSE if feed_segment is None else int(feed_segment)
        self.symbol_to_segment[normalized] = segment
        self.instrument_to_symbol[(segment, security_id)] = normalized
        # Legacy ID-only lookups must not silently select a different segment.
        segments = self.security_segments.setdefault(security_id, set())
        segments.add(segment)
        if len(segments) == 1:
            self.security_to_symbol[security_id] = normalized
            self.security_to_feed_segment[security_id] = segment
        else:
            self.security_to_symbol.pop(security_id, None)
            self.security_to_feed_segment.pop(security_id, None)
        if tick_size is not None:
            self.instrument_to_tick[(segment, security_id)] = self._normalise_tick_size(tick_size)

    def instrument_key(self, symbol):
        symbol = norm_symbol(symbol)
        sid = self.symbol_to_security.get(symbol)
        if not sid:
            return None
        return (self.symbol_to_segment.get(symbol, MarketFeed.NSE), sid)

    def feed_segment(self, security_id: Any) -> int:
        if len(self.security_segments.get(str(security_id), set())) > 1:
            raise ValueError("DHAN_AMBIGUOUS_SECURITY_ID")
        return self.security_to_feed_segment.get(str(security_id), MarketFeed.NSE)

    def tick_size(self, security_id: Any, default: float = 0.05, segment: Optional[int] = None) -> float:
        try:
            fallback = self._normalise_tick_size(default)
        except Exception:
            fallback = 0.05
        segment = self.feed_segment(security_id) if segment is None else segment
        return self.instrument_to_tick.get((segment, str(security_id)), self.security_to_tick_size.get(str(security_id), fallback))

    def exchange_segment_for_symbol(self, symbol: str, dhan: Any) -> Any:
        normalized = norm_symbol(symbol)
        if self.is_index_symbol(normalized):
            return getattr(dhan, "INDEX", "IDX_I")
        security_id = self.symbol_to_security.get(normalized)
        if security_id and self.symbol_to_segment.get(normalized) == MarketFeed.NSE_FNO:
            return getattr(dhan, "FNO", "NSE_FNO")
        return dhan.NSE

    def symbol(self, security_id: Any, segment: Optional[int] = None) -> str:
        if segment is not None:
            return self.instrument_to_symbol.get((int(segment), str(security_id)), "")
        return self.security_to_symbol.get(str(security_id), "")


DHAN_INSTRUMENTS = DhanInstrumentRegistry()


def compact_broker_error(value: Any, limit: int = 300) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[: max(20, int(limit))]


def broker_error_message(response: Any) -> str:
    keys = (
        "omsErrorDescription",
        "oms_error_description",
        "rejectionReason",
        "rejection_reason",
        "errorMessage",
        "error_message",
        "message",
        "remarks",
        "remark",
        "error",
        "detail",
    )
    stack = [response]
    seen = 0
    while stack and seen < 200:
        seen += 1
        value = stack.pop()
        if isinstance(value, dict):
            for key in keys:
                item = value.get(key)
                if item not in (None, ""):
                    return compact_broker_error(item)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return ""


def broker_response_status(response: Any) -> str:
    keys = ("status", "orderStatus", "order_status", "orderStatusText")
    data = response_data(response)
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return str(value).strip().upper().replace(" ", "_")
    if isinstance(response, dict):
        for key in keys:
            value = response.get(key)
            if value not in (None, ""):
                return str(value).strip().upper().replace(" ", "_")
    return ""


def ensure_no_broker_error(response: Any, context: str) -> None:
    status = broker_response_status(response)
    message = broker_error_message(response)
    if status in {"FAILURE", "FAILED", "ERROR", "REJECTED"} or message:
        reason = message or status or "UNKNOWN_BROKER_ERROR"
        raise RuntimeError(f"{context}:{reason}")


def dhan_client(client_id: str, access_token: str) -> dhanhq:
    return dhanhq(DhanContext(str(client_id), str(access_token)))


def response_data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response.get("data")
    return response


def order_id_from_response(response: Any) -> str:
    data = response_data(response)
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        value = data.get("orderId") or data.get("order_id") or data.get("orderNo")
        if value:
            return str(value)
        message = broker_error_message(data)
        if message:
            raise RuntimeError(str(message))
    if isinstance(response, dict):
        message = broker_error_message(response)
        if message:
            raise RuntimeError(str(message))
    if isinstance(response, (str, int)):
        return str(response)
    raise RuntimeError(f"DHAN_ORDER_REJECTED:{response}")


def normalize_dhan_positions(response: Any) -> Dict[str, Any]:
    ensure_no_broker_error(response, "DHAN_POSITIONS_FAILED")
    rows = response_data(response)
    if not isinstance(rows, list):
        raise ValueError("DHAN_POSITIONS_INVALID_RESPONSE")
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        qty = int(float(row["netQty"] if row.get("netQty") is not None else row.get("quantity") or 0))
        normalized.append(
            {
                "tradingsymbol": norm_symbol(
                    str(row.get("tradingSymbol") or row.get("tradingsymbol") or "")
                ),
                "quantity": qty,
                "average_price": float(
                    row.get("costPrice")
                    or row.get("averagePrice")
                    or row.get("buyAvg")
                    or 0.0
                ),
                "product": "CNC"
                if str(row.get("productType") or "").upper() == "CNC"
                else "MIS",
                "pnl": float(
                    row.get("realizedProfit")
                    or 0.0
                )
                + float(row.get("unrealizedProfit") or 0.0),
                "security_id": str(row.get("securityId") or ""),
                "_raw": row,
            }
        )
    return {"net": normalized, "day": []}


def normalize_dhan_holdings(response: Any) -> List[Dict[str, Any]]:
    ensure_no_broker_error(response, "DHAN_HOLDINGS_FAILED")
    data = response_data(response)
    if isinstance(data, dict):
        for key in ("holdings", "data", "result"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
    if not isinstance(data, list):
        raise ValueError("DHAN_HOLDINGS_INVALID_RESPONSE")

    normalized: List[Dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        symbol = norm_symbol(
            str(
                row.get("tradingSymbol")
                or row.get("tradingsymbol")
                or row.get("symbol")
                or row.get("nseSymbol")
                or row.get("securityName")
                or ""
            )
        )
        try:
            qty = int(
                float(
                    next((row[key] for key in ("availableQty", "sellableQty", "totalQty", "holdingQty", "quantity", "qty")
                          if row.get(key) is not None), 0)
                )
            )
        except Exception:
            qty = 0
        if not symbol or qty < 0:
            continue
        normalized.append(
            {
                "tradingsymbol": symbol,
                "quantity": qty,
                "average_price": float(
                    row.get("avgCostPrice")
                    or row.get("averagePrice")
                    or row.get("costPrice")
                    or row.get("buyAvg")
                    or row.get("avgPrice")
                    or 0.0
                ),
                "product": "CNC",
                "security_id": str(row.get("securityId") or row.get("security_id") or ""),
                "_raw": row,
            }
        )
    return normalized


def normalize_dhan_candles(response: Any, interval_minutes: int) -> List[Dict[str, Any]]:
    ensure_no_broker_error(response, "DHAN_CANDLES_FAILED")
    data = response_data(response)
    if isinstance(data, list):
        rows: List[Dict[str, Any]] = []
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        for row in data:
            if not isinstance(row, dict):
                continue
            raw_stamp = row.get("timestamp") or row.get("start_Time") or row.get("date") or row.get("time")
            if raw_stamp is None:
                continue
            if isinstance(raw_stamp, (int, float)):
                stamp = datetime.fromtimestamp(float(raw_stamp), tz=ist)
            else:
                stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = ist.localize(stamp)
                else:
                    stamp = stamp.astimezone(ist)
            if stamp + timedelta(minutes=interval_minutes) > now:
                continue
            rows.append(
                {
                    "date": stamp,
                    "open": float(row.get("open") or row.get("Open") or 0.0),
                    "high": float(row.get("high") or row.get("High") or 0.0),
                    "low": float(row.get("low") or row.get("Low") or 0.0),
                    "close": float(row.get("close") or row.get("Close") or 0.0),
                    "volume": float(row.get("volume") or row.get("Volume") or 0.0),
                }
            )
        return rows
    if not isinstance(data, dict):
        raise ValueError("DHAN_CANDLES_INVALID_RESPONSE")
    if not all(isinstance(data.get(key), list) for key in ("open", "high", "low", "close")):
        raise ValueError("DHAN_CANDLES_INVALID_RESPONSE")
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    volumes = data.get("volume") or []
    stamps = data.get("timestamp") or data.get("start_Time") or []
    if not isinstance(stamps, list) or len({len(opens), len(highs), len(lows), len(closes), len(stamps)}) != 1:
        raise ValueError("DHAN_CANDLES_INVALID_RESPONSE")
    size = min(len(opens), len(highs), len(lows), len(closes), len(stamps))
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    rows: List[Dict[str, Any]] = []
    for index in range(size):
        raw_stamp = stamps[index]
        if isinstance(raw_stamp, (int, float)):
            stamp = datetime.fromtimestamp(float(raw_stamp), tz=ist)
        else:
            stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = ist.localize(stamp)
            else:
                stamp = stamp.astimezone(ist)
        if stamp + timedelta(minutes=interval_minutes) > now:
            continue
        rows.append(
            {
                "date": stamp,
                "open": float(opens[index]),
                "high": float(highs[index]),
                "low": float(lows[index]),
                "close": float(closes[index]),
                "volume": float(volumes[index]) if index < len(volumes) else 0.0,
            }
        )
    return rows


def resample_intraday_candles(candles: List[Dict[str, Any]], interval_minutes: int) -> List[Dict[str, Any]]:
    """Aggregate 1-minute/session candles into custom intraday bars."""
    interval = max(1, int(interval_minutes))
    if interval <= 1:
        return list(candles)
    ist = pytz.timezone("Asia/Kolkata")
    buckets: Dict[tuple, Dict[str, Any]] = {}
    for candle in sorted(candles, key=lambda item: item.get("date") or datetime.min):
        stamp = candle.get("date")
        if not isinstance(stamp, datetime):
            continue
        stamp = stamp.astimezone(ist) if stamp.tzinfo else ist.localize(stamp)
        session_start = stamp.replace(hour=9, minute=15, second=0, microsecond=0)
        minutes_from_open = int((stamp - session_start).total_seconds() // 60)
        if minutes_from_open < 0:
            continue
        bucket_offset = (minutes_from_open // interval) * interval
        bucket_time = session_start + timedelta(minutes=bucket_offset)
        key = (bucket_time.date().isoformat(), bucket_time.strftime("%H:%M"))
        current = buckets.get(key)
        if not current:
            buckets[key] = {
                "date": bucket_time,
                "open": float(candle.get("open") or 0.0),
                "high": float(candle.get("high") or 0.0),
                "low": float(candle.get("low") or 0.0),
                "close": float(candle.get("close") or 0.0),
                "volume": float(candle.get("volume") or 0.0),
            }
            continue
        current["high"] = max(float(current["high"]), float(candle.get("high") or 0.0))
        current["low"] = min(float(current["low"]), float(candle.get("low") or 0.0))
        current["close"] = float(candle.get("close") or 0.0)
        current["volume"] = float(current.get("volume") or 0.0) + float(candle.get("volume") or 0.0)
    return sorted(buckets.values(), key=lambda item: item.get("date") or datetime.min)


class _DhanFeedDisconnect(Exception):
    def __init__(self, code):
        super().__init__(f"DHAN_DISCONNECT_{code}")
        self.status_code = 401 if code in (806, 807, 808, 809) else (429 if code == 805 else None)


class _SafeDhanOrderUpdate(OrderUpdate):
    async def connect_order_update(self):
        # Preserve Dhan's SELF protocol without the SDK's credential-printing path.
        async with websockets.connect(self.order_feed_wss, open_timeout=15, close_timeout=5) as ws:
            await ws.send(json.dumps({"LoginReq": {
                "MsgCode": 42, "ClientId": str(self.client_id), "Token": str(self.access_token),
            }, "UserType": "SELF"}))
            while not token_expired(self.access_token):
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    continue
                # Reject malformed frames as a whole: never infer an order/fill
                # from a truncated, concatenated or non-JSON error response.
                data = json.loads(message)
                if not isinstance(data, dict):
                    raise ValueError("DHAN_ORDER_UPDATE_INVALID_SHAPE")
                if data.get("Type") == "order_alert":
                    if not isinstance(data.get("Data"), dict):
                        raise ValueError("DHAN_ORDER_UPDATE_INVALID_DATA")
                    result = self.on_update(data) if callable(self.on_update) else None
                    if inspect.isawaitable(result):
                        await result
                elif str(data.get("Type", "")).lower() in {"error", "auth_error"}:
                    raise ValueError("DHAN_ORDER_UPDATE_SERVER_ERROR")


class _QueuedDhanMarketFeed(MarketFeed):
    def server_disconnection(self, data):
        code = struct.unpack("<BHBIH", data[:10])[4]
        raise _DhanFeedDisconnect(code)

    async def disconnect(self):
        if self.ws:
            try:
                # v2 disconnect is JSON only; don't send the SDK's v1 header too.
                await self.ws.send(json.dumps({"RequestCode": 12}))
            finally:
                await self.ws.close()
                self.ws = None
        if self.on_close:
            self.on_close(self)

    async def _run_async(self):
        backoff = FeedBackoff()
        while self._running:
            if token_expired(self.access_token):
                self.health_detail = "token_expired_reauthenticate"
                if self.on_close:
                    self.on_close(self)
                return
            connected_at = time.monotonic()
            try:
                await self.connect()
                self.health_detail = "websocket_connected"
                while self._running and not token_expired(self.access_token):
                    try:
                        await self.get_instrument_data()
                    except asyncio.TimeoutError:
                        # An idle event-based feed is not a disconnected socket.
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if time.monotonic() - connected_at >= 60:
                    backoff.reset()
                self.health_detail = safe_feed_error(exc)
                if self.on_error:
                    self.on_error(self, exc)
                if error_status(exc) in (401, 403):
                    self.health_detail = "authentication_rejected_reauthenticate"
                    return
                delay = backoff.delay(exc)
                log.warning("DHAN_MARKET_RETRY | error=%s delay_sec=%.1f", self.health_detail, delay)
                if self.ws:
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                    self.ws = None
                await asyncio.sleep(delay)

    def run(self):
        asyncio.set_event_loop(self.loop)
        self._running = True
        self._runner_task = self.loop.create_task(self._run_async())
        try:
            self.loop.run_until_complete(self._runner_task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("DHAN_FEED_RECEIVER_STOPPED | error=%s", safe_feed_error(exc))
        finally:
            if self.ws:
                try:
                    self.loop.run_until_complete(asyncio.wait_for(self.ws.close(), timeout=5))
                except Exception:
                    pass
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()
            asyncio.set_event_loop(None)

    def close_connection(self):
        self._running = False
        if self.loop.is_closed() or not self.loop.is_running():
            return

        async def shutdown():
            try:
                await asyncio.wait_for(self.disconnect(), timeout=5)
            finally:
                # Also release a receiver waiting for delivery queue capacity.
                runner = getattr(self, "_runner_task", None)
                if runner:
                    runner.cancel()

        future = asyncio.run_coroutine_threadsafe(shutdown(), self.loop)
        future.result(timeout=8)

    async def subscribe_instruments(self):
        if not hasattr(self, "_send_lock"):
            self._send_lock = asyncio.Lock()
        async with self._send_lock:
            snapshot = list(self.instruments)
            await super().subscribe_instruments()
            self.last_sent_instruments = snapshot

    async def get_instrument_data(self):
        packet = await asyncio.wait_for(super().get_instrument_data(), timeout=60)
        if isinstance(packet, dict):
            packet = dict(packet, received_at=time.time())
            # Awaiting queue capacity keeps the SDK loop free to answer ping/pong.
            future = asyncio.run_coroutine_threadsafe(self.deliver(packet), self.delivery_loop)
            await asyncio.wrap_future(future)
        return packet


@dataclass
class DhanFeedService:
    user_id: int
    client_id: str
    access_token: str
    on_tick: Callable[[Dict[str, Any]], Any]
    on_order_update: Callable[[Dict[str, Any]], Any]
    on_state: Optional[Callable[[bool], None]] = None

    def __post_init__(self) -> None:
        self.context = DhanContext(self.client_id, self.access_token)
        self.feed: Optional[MarketFeed] = None
        self.order_update: Optional[OrderUpdate] = None
        self.security_ids: set[tuple] = set()
        self.feed_thread: Any = None
        self.order_task: Optional[asyncio.Task[None]] = None
        self.sent_security_ids: set[tuple] = set()
        self._subscription_lock = asyncio.Lock()
        self._tick_tasks: List[asyncio.Task] = []
        self._tick_queues: List[asyncio.Queue] = []
        self._stopping = False
        self.health_detail = "starting"
        self.order_health_detail = "starting"

    @property
    def reconnect_managed(self):
        detail = getattr(self.feed, "health_detail", self.health_detail)
        return bool(token_expired(self.access_token) or detail == "authentication_rejected_reauthenticate"
                    or (self.feed_thread and self.feed_thread.is_alive()))

    async def _deliver_tick(self, packet: Dict[str, Any]) -> None:
        if self._stopping:
            return
        key = (packet.get("exchange_segment"), str(packet.get("security_id") or packet.get("securityId") or ""))
        queue = self._tick_queues[hash(key) % len(self._tick_queues)]
        await queue.put(packet)

    async def _consume_ticks(self, queue: asyncio.Queue) -> None:
        while True:
            packet = await queue.get()
            try:
                result = self.on_tick(packet)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("DHAN_TICK_HANDLER_FAILED | user=%s", self.user_id)
            finally:
                queue.task_done()

    async def start(self, security_ids: Iterable[Any]) -> None:
        if self.feed is not None or self.order_task is not None:
            await self.subscribe(security_ids)
            return
        if token_expired(self.access_token):
            self.health_detail = "token_expired_reauthenticate"
            self.order_health_detail = self.health_detail
            if self.on_state:
                self.on_state(False)
            log.warning("DHAN_FEED_AUTH_EXPIRED | user=%s", self.user_id)
            return
        self.security_ids.update(self._keys(security_ids))
        if len(self.security_ids) > 5000:
            raise ValueError("DHAN_SUBSCRIPTION_LIMIT_EXCEEDED")
        delivery_loop = asyncio.get_running_loop()
        self._stopping = False
        self._tick_queues = [asyncio.Queue(maxsize=256) for _ in range(4)]
        self._tick_tasks = [
            asyncio.create_task(self._consume_ticks(queue), name=f"dhan_ticks_{self.user_id}_{i}")
            for i, queue in enumerate(self._tick_queues)
        ]
        instruments = [
            (segment, security_id, MarketFeed.Full)
            for segment, security_id in sorted(self.security_ids)
        ]
        log.info(
            "DHAN_FEED_START | user=%s instruments=%s sample=%s",
            self.user_id,
            len(instruments),
            instruments[:10],
        )

        def connected(_feed: MarketFeed) -> None:
            sent = {(int(item[0]), str(item[1])) for item in _feed.last_sent_instruments}
            delivery_loop.call_soon_threadsafe(self.sent_security_ids.update, sent)
            if self.on_state:
                self.on_state(True)

        def closed(_feed: MarketFeed) -> None:
            delivery_loop.call_soon_threadsafe(self.sent_security_ids.clear)
            if self.on_state:
                self.on_state(False)

        def errored(_feed: MarketFeed, exc: Exception) -> None:
            delivery_loop.call_soon_threadsafe(self.sent_security_ids.clear)
            if self.on_state:
                self.on_state(False)

        self.feed = _QueuedDhanMarketFeed(
            self.context,
            instruments,
            "v2",
            on_connect=connected,
            on_close=closed,
            on_error=errored,
        )
        self.feed.deliver = self._deliver_tick
        self.feed.delivery_loop = delivery_loop
        self.feed_thread = self.feed.start()

        self.order_update = _SafeDhanOrderUpdate(self.context)
        self.order_update.on_update = self.on_order_update
        self.order_task = asyncio.create_task(
            self._run_order_updates(),
            name=f"dhan_order_updates_{self.user_id}",
        )

    async def _run_order_updates(self) -> None:
        backoff = FeedBackoff()
        while self.order_update is not None and not self._stopping:
            if token_expired(self.access_token):
                self.order_health_detail = "token_expired_reauthenticate"
                return
            connected_at = time.monotonic()
            try:
                await self.order_update.connect_order_update()
                failure = ConnectionError("Order stream closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = exc
            if token_expired(self.access_token):
                self.order_health_detail = "token_expired_reauthenticate"
                return
            if time.monotonic() - connected_at >= 60:
                backoff.reset()
            self.order_health_detail = safe_feed_error(failure)
            if error_status(failure) in (401, 403):
                self.order_health_detail = "authentication_rejected_reauthenticate"
                return
            delay = backoff.delay(failure)
            log.warning("DHAN_ORDER_RETRY | user=%s error=%s delay_sec=%.1f",
                        self.user_id, self.order_health_detail, delay)
            await asyncio.sleep(delay)

    @staticmethod
    def _keys(items):
        return {(int(item[0]), str(item[1])) if isinstance(item, (tuple, list)) else
                (DHAN_INSTRUMENTS.feed_segment(item), str(item)) for item in items if item}

    async def subscribe(self, security_ids: Iterable[Any]) -> bool:
        async with self._subscription_lock:
            desired = self.security_ids | self._keys(security_ids)
            if len(desired) > 5000:
                raise ValueError("DHAN_SUBSCRIPTION_LIMIT_EXCEEDED")
            self.security_ids = desired
            new_ids = desired - self.sent_security_ids
            if not new_ids:
                return True
            feed = self.feed
            if feed is None or not feed.loop.is_running():
                return False

            async def send() -> bool:
                # Mutate SDK subscription state only on its owning loop. The full
                # desired list is retained for reconnect even when this send fails.
                feed.instruments = [
                    (segment, sid, MarketFeed.Full)
                    for segment, sid in sorted(desired)
                ]
                if not feed.ws or feed._is_ws_closed():
                    return False
                await feed.subscribe_instruments()
                return True

            future = asyncio.run_coroutine_threadsafe(send(), feed.loop)
            try:
                sent = await asyncio.wait_for(asyncio.wrap_future(future), timeout=10)
            except Exception:
                self.sent_security_ids.difference_update(new_ids)
                log.warning("DHAN_FEED_SUBSCRIBE_FAILED | user=%s", self.user_id)
                raise
            if sent:
                self.sent_security_ids.update(desired)
                log.info("DHAN_FEED_SUBSCRIBE_SENT | user=%s count=%s", self.user_id, len(desired))
            return sent

    async def stop(self) -> None:
        self._stopping = True
        feed = self.feed
        feed_thread = self.feed_thread
        self.feed = None
        self.feed_thread = None

        if feed:
            try:
                await asyncio.wait_for(asyncio.to_thread(feed.close_connection), timeout=10)
            except Exception as exc:
                log.debug("Dhan market feed close failed: %s", safe_feed_error(exc))

            try:
                if feed_thread and getattr(feed_thread, "is_alive", lambda: False)():
                    await asyncio.to_thread(feed_thread.join, 3)
            except Exception as exc:
                log.debug("Dhan market feed thread join failed: %s", safe_feed_error(exc))

            try:
                loop = getattr(feed, "loop", None)
                if loop is not None and not loop.is_closed() and not loop.is_running():
                    loop.close()
            except Exception as exc:
                log.debug("Dhan market feed loop close failed: %s", safe_feed_error(exc))

        if self.order_task:
            self.order_task.cancel()
            try:
                await self.order_task
            except asyncio.CancelledError:
                pass
        self.order_task = None
        self.order_update = None
        for task in getattr(self, "_tick_tasks", []):
            task.cancel()
        await asyncio.gather(*getattr(self, "_tick_tasks", []), return_exceptions=True)
        self._tick_tasks = []
        if self.on_state:
            self.on_state(False)
