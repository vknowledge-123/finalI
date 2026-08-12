from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Iterable, List


class MarketFeedService:
    """Owns market websocket subscription commands."""

    def __init__(
        self,
        *,
        start_feed: Callable[[int], Awaitable[None]],
        stop_dhan_feed: Callable[[], Awaitable[None]],
        stop_kite_feed: Callable[[], Awaitable[None]],
        subscribe_symbols: Callable[[int, List[str]], Awaitable[None]],
    ) -> None:
        self.start_feed = start_feed
        self.stop_dhan_feed = stop_dhan_feed
        self.stop_kite_feed = stop_kite_feed
        self.subscribe_symbols = subscribe_symbols

    async def start(self, user_id: int) -> None:
        await self.start_feed(int(user_id))

    async def stop_all(self) -> None:
        await self.stop_dhan_feed()
        await self.stop_kite_feed()

    async def subscribe(self, user_id: int, symbols: Iterable[str]) -> None:
        await self.subscribe_symbols(int(user_id), [str(symbol) for symbol in symbols if symbol])

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "streams": ["stock_ticks", "index_ticks", "sector_index_ticks"]}

