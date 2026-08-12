from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict


class ExitRiskService:
    """Owns live exits and risk controls through the engine boundary."""

    def __init__(self, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self.ensure_engine = ensure_engine

    async def exit_position(self, user_id: int, symbol: str, reason: str) -> None:
        engine = await self.ensure_engine(int(user_id))
        await engine._exit_position(symbol, reason)

    async def exit_all_open_positions(self, user_id: int, reason: str = "AUTO_SQ_OFF") -> int:
        engine = await self.ensure_engine(int(user_id))
        return await engine.exit_all_open_positions(reason=reason)

    async def squareoff_all_positions(self, user_id: int, reason: str = "MANUAL_EXIT_ALL") -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine.squareoff_all_positions(reason=reason)

    async def trigger_kill_switch(self, user_id: int, reason: str, squareoff_first: bool = True) -> Dict[str, Any]:
        engine = await self.ensure_engine(int(user_id))
        return await engine.trigger_kill_switch(reason=reason, squareoff_first=squareoff_first)

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "rules": ["target", "stop_loss", "trailing_sl", "auto_squareoff", "kill_switch"]}

