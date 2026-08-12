from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List


class PositionService:
    """Owns persisted open-position state."""

    def __init__(self, store_provider, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self._store_provider = store_provider
        self.ensure_engine = ensure_engine

    @property
    def store(self):
        store = self._store_provider()
        if store is None:
            raise RuntimeError("STORE_NOT_READY")
        return store

    async def list_positions(self, user_id: int) -> List[Dict[str, Any]]:
        return await self.store.list_positions(int(user_id))

    async def upsert_position(self, user_id: int, symbol: str, position: Dict[str, Any]) -> None:
        await self.store.upsert_position(int(user_id), symbol, position)

    async def delete_position(self, user_id: int, symbol: str) -> None:
        await self.store.delete_position(int(user_id), symbol)

    async def rehydrate(self, user_id: int) -> List[str]:
        engine = await self.ensure_engine(int(user_id))
        return await engine.rehydrate_open_positions()

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "store": "redis_or_memory"}

