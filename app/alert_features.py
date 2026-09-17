"""Validation and closed-candle rules for optional alert features."""

import math
import re
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def enabled(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def public_config(config):
    result = dict(config)
    result["telegram_token_set"] = bool(result.pop("telegram_bot_token_encrypted", ""))
    result.pop("telegram_bot_token", None)
    return result


def prepare_features(payload, existing, store):
    result = {}
    result["high_break_enabled"] = enabled(payload.get("high_break_enabled", False))
    result["high_break_buffer_enabled"] = enabled(payload.get("high_break_buffer_enabled", False))
    try:
        timeframe = float(payload.get("high_break_timeframe_minutes", 1))
        ttl = float(payload.get("high_break_ttl_minutes", 1))
        buffer = float(payload.get("high_break_buffer", 0))
        if timeframe not in (1, 5) or not math.isfinite(buffer) or not 0 <= buffer <= 10000:
            raise ValueError()
        if not math.isfinite(ttl) or not 1 <= ttl <= 60 or not ttl.is_integer():
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("HIGH_BREAK_SETTINGS_INVALID") from None
    result.update(high_break_timeframe_minutes=int(timeframe), high_break_buffer=buffer,
                  high_break_ttl_minutes=int(ttl))
    result["telegram_enabled"] = enabled(payload.get("telegram_enabled", False))
    chat = str(payload.get("telegram_chat_id", existing.get("telegram_chat_id", "")) or "").strip()
    if chat and not re.fullmatch(r"(?:-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})", chat):
        raise ValueError("TELEGRAM_CHAT_ID_INVALID")
    result["telegram_chat_id"] = chat
    encrypted = existing.get("telegram_bot_token_encrypted", "")
    token = str(payload.get("telegram_bot_token") or "").strip()
    if token:
        if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}", token):
            raise ValueError("TELEGRAM_BOT_TOKEN_INVALID")
        cipher = getattr(getattr(store, "encryption", None), "cipher", None)
        if cipher is None:
            raise ValueError("TELEGRAM_ENCRYPTION_REQUIRED")
        encrypted = cipher.encrypt(token.encode()).decode()
    if result["telegram_enabled"] and (not chat or not encrypted):
        raise ValueError("TELEGRAM_CREDENTIALS_REQUIRED")
    if result["telegram_enabled"]:
        cipher = getattr(getattr(store, "encryption", None), "cipher", None)
        if cipher is None:
            raise ValueError("TELEGRAM_ENCRYPTION_REQUIRED")
        try:
            cipher.decrypt(encrypted.encode())
        except Exception:
            raise ValueError("TELEGRAM_TOKEN_UNREADABLE_REPLACE_TOKEN") from None
    result["telegram_bot_token_encrypted"] = encrypted
    return result


def alert_time(timestamp, now=None):
    now = now or datetime.now(IST)
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)
    except (TypeError, ValueError):
        return now.astimezone(IST)


def breakout_level(candles, reference, minutes, side, buffer=0):
    """Require the exact previous bar; never substitute an older or forming bar."""
    reference = reference.astimezone(IST)
    end = reference.replace(minute=reference.minute - reference.minute % minutes, second=0, microsecond=0)
    start = end - timedelta(minutes=minutes)
    if start.date() != reference.date() or (start.hour, start.minute) < (9, 15):
        raise ValueError("HIGH_BREAK_CANDLE_NOT_READY")
    if reference.weekday() >= 5 or (end.hour, end.minute) > (15, 30):
        raise ValueError("HIGH_BREAK_CANDLE_NOT_READY")
    matching = []
    for candle in candles:
        try:
            stamp = candle.get("date")
            if not isinstance(stamp, datetime):
                stamp = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            stamp = stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp.astimezone(IST)
            if stamp == start:
                matching.append(candle)
        except (ValueError, TypeError):
            continue
    if len(matching) != 1:
        raise ValueError("HIGH_BREAK_CANDLE_NOT_READY")
    try:
        high, low = float(matching[0]["high"]), float(matching[0]["low"])
        if not all(math.isfinite(v) and v > 0 for v in (high, low)) or high < low:
            raise ValueError()
        level = Decimal(str(high if side == "BUY" else low))
        level += Decimal(str(buffer)) * (1 if side == "BUY" else -1)
        if level <= 0:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("HIGH_BREAK_CANDLE_INVALID") from None
    return level
