from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from ..trade_engine import _normalize_order_snapshot


class OrderUpdateService:
    """Normalizes broker order websocket updates."""

    def __init__(self, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self.ensure_engine = ensure_engine

    def normalize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return _normalize_order_snapshot(payload)

    async def publish(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = self.normalize(payload)
        engine = await self.ensure_engine(int(user_id))
        await engine.on_order_update(snapshot)
        return snapshot

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "statuses": ["PENDING", "PARTIAL", "COMPLETE", "REJECTED", "CANCELLED"]}

