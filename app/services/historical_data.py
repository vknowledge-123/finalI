from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List


class HistoricalDataService:
    """Broker candle fetch/cache boundary."""

    def __init__(self, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self.ensure_engine = ensure_engine

    async def candles(
        self,
        user_id: int,
        symbol: str,
        interval: str = "5minute",
        lookback_days: int = 12,
    ) -> List[Dict[str, Any]]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_historical_candles(symbol, interval, int(lookback_days))

    async def backtest_candles(self, user_id: int, symbol: str, interval: str, start, end, warmup_days: int = 1):
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_backtest_candles(symbol, interval, start, end, warmup_days=warmup_days)

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "cache": "broker_worker"}

