"""Local feed retry policy; token metadata is not authentication verification."""

import base64
import json
import math
import os
import random
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


def token_expired(token, now=None):
    try:
        payload = str(token).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expiry = float(claims["exp"])
        return math.isfinite(expiry) and expiry <= (time.time() if now is None else now)
    except (ValueError, TypeError, KeyError, IndexError):
        # Opaque tokens still require broker-side authentication.
        return False


def equity_session_open(now=None, connection_window=False):
    now = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    now = now.astimezone(ZoneInfo("Asia/Kolkata"))
    day = now.date().isoformat()
    holidays = os.getenv("DHAN_MARKET_HOLIDAYS", "").split(",")
    extra_sessions = os.getenv("DHAN_MARKET_EXTRA_SESSIONS", "").split(",")
    if day in holidays or (now.weekday() >= 5 and day not in extra_sessions):
        return False
    minutes = now.hour * 60 + now.minute
    return (8 * 60 <= minutes < 16 * 60) if connection_window else (9 * 60 + 15 <= minutes < 15 * 60 + 30)


def error_status(exc):
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) or getattr(exc, "status_code", None)


def safe_feed_error(exc):
    # Exception text can contain the authenticated URL or the received payload.
    status = error_status(exc)
    return f"HTTP_{status}" if isinstance(status, int) else type(exc).__name__


class FeedBackoff:
    def __init__(self):
        self.failures = 0

    def reset(self):
        self.failures = 0

    def delay(self, exc, session_open=None):
        self.failures += 1
        delay = min(300, 5 * 2 ** min(self.failures - 1, 6))
        if error_status(exc) == 429:
            delay = max(60, delay)
        headers = getattr(getattr(exc, "response", None), "headers", {})
        retry_after = headers.get("Retry-After") if headers else None
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                try:
                    seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    seconds = 0
            if math.isfinite(seconds):
                delay = max(delay, seconds)
        if not (equity_session_open(connection_window=True) if session_open is None else session_open):
            delay = max(300, delay)
        return delay + random.uniform(0, min(5, delay * 0.1))
