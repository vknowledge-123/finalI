from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict


class BrokerAdapterLayer:
    """Common broker operations over Dhan/Zerodha through TradeEngine."""

    def __init__(self, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self.ensure_engine = ensure_engine

    async def place_order(self, user_id: int, symbol: str, side: str, qty: int, product: str) -> Any:
        engine = await self.ensure_engine(int(user_id))
        return await engine._place_order(symbol, side, int(qty), product)

    async def cancel_order(self, user_id: int, order_id: str) -> bool:
        engine = await self.ensure_engine(int(user_id))
        return await engine._cancel_order_if_pending(str(order_id))

    async def get_order(self, user_id: int, order_id: str) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_order_snapshot(str(order_id))

    async def get_order_book(self, user_id: int, order_id: str) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_dhan_order_list_snapshot(str(order_id))

    async def get_trade_book(self, user_id: int, order_id: str) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_dhan_trade_snapshot(str(order_id))

    async def get_positions(self, user_id: int) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._broker_positions()

    async def get_ltp(self, user_id: int, symbol: str) -> float:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_ltp(symbol)

    async def get_candles(self, user_id: int, symbol: str, interval: str = "5minute", lookback_days: int = 12):
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_historical_candles(symbol, interval, int(lookback_days))

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "brokers": ["DHAN", "ZERODHA"]}

