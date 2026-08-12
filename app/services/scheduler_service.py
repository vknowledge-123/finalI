from __future__ import annotations

import asyncio
import contextlib
from typing import Awaitable, Callable, Dict, List


class SchedulerService:
    """Tracks background jobs owned by the service runtime."""

    def __init__(self) -> None:
        self._tasks: List[asyncio.Task[None]] = []

    def schedule(self, name: str, coro_factory: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(coro_factory(), name=name)
        self._tasks.append(task)

    async def stop(self) -> None:
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def status(self) -> Dict[str, int]:
        return {"enabled": 1, "tasks": len([task for task in self._tasks if not task.done()])}

