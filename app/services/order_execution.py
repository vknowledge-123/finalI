from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional


class OrderExecutionService:
    """Only service boundary intended to place broker orders."""

    def __init__(self, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self.ensure_engine = ensure_engine

    async def place_market_order(
        self,
        user_id: int,
        symbol: str,
        side: str,
        qty: int,
        product: str,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> Any:
        engine = await self.ensure_engine(int(user_id))
        return await engine._place_order_with_execution(
            str(symbol),
            "BUY" if str(side).upper() == "BUY" else "SELL",
            int(qty),
            "CNC" if str(product).upper() == "CNC" else "MIS",
            cfg or {},
        )

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "retry": "remaining_quantity_only", "partial_fills": True}

