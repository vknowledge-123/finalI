from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional


class BrokerReconciliationService:
    """Broker order/trade/position reconciliation boundary."""

    def __init__(self, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self.ensure_engine = ensure_engine

    async def reconcile_order(
        self,
        user_id: int,
        order_id: str,
        symbol: str,
        side: str,
        requested_qty: int,
        before_qty: Optional[int] = None,
        base_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        snapshot, after_qty = await engine._reconcile_dhan_execution_snapshot(
            str(order_id),
            str(symbol),
            "BUY" if str(side).upper() == "BUY" else "SELL",
            int(requested_qty),
            before_qty,
            base_snapshot,
        )
        return {"snapshot": snapshot, "after_qty": after_qty}

    async def broker_quantity(self, user_id: int, symbol: str) -> Optional[int]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_broker_symbol_qty(symbol)

    async def order_book_snapshot(self, user_id: int, order_id: str) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_dhan_order_list_snapshot(order_id)

    async def trade_book_snapshot(self, user_id: int, order_id: str) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine._fetch_dhan_trade_snapshot(order_id)

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "sources": ["order_book", "trade_book", "positions"]}

