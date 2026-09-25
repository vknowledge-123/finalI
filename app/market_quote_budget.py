"""Share Dhan's REST market-data request budget across the local workers."""

import asyncio
import time


async def reserve_quote_slot(store, user_id, *, background=False):
    redis = getattr(store, "redis", None)
    if redis is None:  # In-memory test store has no external broker calls.
        return True
    key = f"dhan:quote-budget:{int(user_id)}"
    priority = key + ":foreground"
    deadline = time.monotonic() + (0 if background else 4)
    while True:
        if background:
            if await redis.exists(priority):
                return False
        else:
            await redis.set(priority, "1", px=1500)
        if await redis.set(key, "1", nx=True, px=1100):
            return True
        if background:
            return False
        if time.monotonic() >= deadline:
            raise RuntimeError("DHAN_MARKET_DATA_BUDGET_BUSY")
        await asyncio.sleep(0.05)
