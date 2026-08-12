from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List


class SectorRankingService:
    """Owns sector cache/ranking decisions through the engine boundary."""

    def __init__(self, store_provider, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self._store_provider = store_provider
        self.ensure_engine = ensure_engine

    @property
    def store(self):
        store = self._store_provider()
        if store is None:
            raise RuntimeError("STORE_NOT_READY")
        return store

    async def load_cache(self, user_id: int) -> Dict[str, Any]:
        cache = await self.store.load_sector_cache(int(user_id))
        engine = await self.ensure_engine(int(user_id))
        if cache:
            engine.load_sector_cache(cache)
        return cache

    async def rank(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        await self.load_cache(int(user_id))
        engine = await self.ensure_engine(int(user_id))
        rows = engine.get_sector_rank()[: max(1, int(limit))]
        return [{"name": name, "pct": pct} for name, pct in rows]

    async def filter_allowed(self, user_id: int, symbol: str, top_n: int) -> bool:
        engine = await self.ensure_engine(int(user_id))
        sector = engine.sym_sector.get(str(symbol).upper(), "")
        if not sector:
            return False
        ranked = engine.get_sector_rank()
        top = [name for name, _ in ranked[: max(1, int(top_n))]]
        return sector in top

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "rank_source": "sector_index_cache_plus_live_ticks"}

