from __future__ import annotations

from typing import Any, Dict, List


class NotificationService:
    """Writes alert history and pushes websocket updates."""

    def __init__(self, store_provider, ws_manager) -> None:
        self._store_provider = store_provider
        self._ws_manager = ws_manager

    @property
    def store(self):
        store = self._store_provider()
        if store is None:
            raise RuntimeError("STORE_NOT_READY")
        return store

    async def save_alert_received(
        self,
        user_id: int,
        alert_name: str,
        timestamp: str,
        symbols: List[str],
        result: List[Dict[str, Any]],
    ) -> None:
        await self.store.save_alert(
            user_id,
            {
                "alert_name": alert_name,
                "time": timestamp,
                "symbols": symbols,
                "result": result,
            },
        )

    async def save_alert_result(
        self,
        user_id: int,
        alert_name: str,
        timestamp: str,
        result: List[Dict[str, Any]],
    ) -> None:
        await self.store.save_alert(
            user_id,
            {
                "alert_name": alert_name,
                "time": timestamp,
                "result": result,
            },
        )

    async def broadcast_alert(
        self,
        user_id: int,
        alert_name: str,
        timestamp: str,
        symbols: List[str],
        result: List[Dict[str, Any]],
    ) -> None:
        await self._ws_manager.broadcast(
            user_id,
            {
                "type": "alert",
                "alert_name": alert_name,
                "time": timestamp,
                "symbols": symbols,
                "result": result,
            },
        )

