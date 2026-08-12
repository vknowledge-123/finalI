from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from .contracts import ChartinkSignalJob


class TradeDecisionService:
    """Combines strategy/risk filters and delegates order placement.

    The current implementation still uses TradeEngine as the execution core.
    Keeping this boundary lets order execution, reconciliation, position, and
    risk services be extracted without changing the API gateway or signal queue.
    """

    def __init__(self, ensure_engine: Callable[[int], Awaitable[Any]]) -> None:
        self.ensure_engine = ensure_engine

    async def process_chartink_signal(self, job: ChartinkSignalJob) -> List[Dict[str, Any]]:
        engine = await self.ensure_engine(int(job.user_id))
        return await engine.on_chartink_alert(job.alert_name, list(job.symbols), ts=job.timestamp)

