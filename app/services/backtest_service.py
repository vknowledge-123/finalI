from __future__ import annotations

from typing import Any, Dict, List

from ..backtest import run_custom_strategy_backtest


class BacktestService:
    """Runs backtests outside the live trading hot path."""

    def run_custom_strategy(self, candles: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        return run_custom_strategy_backtest(candles, **kwargs)

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "mode": "in_process"}

