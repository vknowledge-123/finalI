"""Shared IST cutoff for scheduled MIS exits and new MIS exposure."""

from datetime import datetime
from zoneinfo import ZoneInfo

AUTO_SQUAREOFF_REASON = "AUTO_SQ_OFF_310"


def squareoff_cutoff_reached(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    return now.hour * 60 + now.minute >= 15 * 60 + 10
