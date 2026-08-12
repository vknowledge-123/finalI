from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, List, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")

log = logging.getLogger("services.job_queue")


@dataclass
class _QueuedJob(Generic[T, R]):
    item: T
    future: asyncio.Future[R]


class AsyncJobQueue(Generic[T, R]):
    """Small bounded worker queue used to keep API routes thin and parallel."""

    def __init__(
        self,
        name: str,
        handler: Callable[[T], Awaitable[R]],
        *,
        workers: int = 4,
        maxsize: int = 1000,
    ) -> None:
        self.name = str(name)
        self.handler = handler
        self.workers = max(1, int(workers))
        self.queue: asyncio.Queue[_QueuedJob[T, R]] = asyncio.Queue(maxsize=max(1, int(maxsize)))
        self._tasks: List[asyncio.Task[None]] = []
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._tasks = [
            asyncio.create_task(self._run(i), name=f"{self.name}_worker_{i}")
            for i in range(self.workers)
        ]

    async def stop(self) -> None:
        tasks = list(self._tasks)
        self._tasks.clear()
        self._started = False
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def submit(self, item: T, *, timeout: Optional[float] = None) -> R:
        if not self._started:
            await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[R] = loop.create_future()
        await self.queue.put(_QueuedJob(item=item, future=future))
        return await asyncio.wait_for(future, timeout=timeout) if timeout else await future

    async def _run(self, worker_id: int) -> None:
        while True:
            job = await self.queue.get()
            try:
                if not job.future.cancelled():
                    result = await self.handler(job.item)
                    if not job.future.cancelled():
                        job.future.set_result(result)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                raise
            except Exception as exc:
                log.exception("%s worker %s failed", self.name, worker_id)
                if not job.future.cancelled():
                    job.future.set_exception(exc)
            finally:
                self.queue.task_done()

