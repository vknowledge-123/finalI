from __future__ import annotations

from typing import Any, Dict, List
import asyncio
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import partial
import multiprocessing

from ..backtest import run_custom_strategy_backtest


class BacktestService:
    """Runs backtests outside the live trading hot path."""

    def __init__(self):
        self._pool = None
        self._job = None

    async def run_custom_strategy(self, candles: List[Dict[str, Any]], *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if self._job is not None and not self._job.done():
            raise RuntimeError("BACKTEST_BUSY")
        if len(candles) > 100000:
            raise ValueError("BACKTEST_TOO_MANY_CANDLES")
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        self._job = asyncio.get_running_loop().run_in_executor(
            self._pool, partial(run_custom_strategy_backtest, candles, *args, **kwargs))
        # A disconnected HTTP client must not release capacity while CPU work
        # is still running in the child process.
        try:
            return await asyncio.shield(self._job)
        except BrokenProcessPool:
            await self.close()
            raise

    async def close(self):
        if self._pool is not None:
            pool, self._pool = self._pool, None
            await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)
        if self._job is not None:
            await asyncio.gather(self._job, return_exceptions=True)
            self._job = None

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "mode": "process_pool", "workers": 1}
