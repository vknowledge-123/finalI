from __future__ import annotations

from typing import Any, Dict

from ..custom_strategy import (
    evaluate_gmma_gold_cross_strategy,
    evaluate_gmma_obv_strategy,
    evaluate_gvk_trend_strategy,
    evaluate_liquidity_sweep_strategy,
    evaluate_precision_sniper,
    evaluate_pure_liquidity_sweep_strategy,
)


class StrategyEvaluationService:
    """Runs custom strategy evaluation functions."""

    def evaluate(self, mode: str, candles, settings: Dict[str, Any], htf_candles=None):
        mode = str(mode or "CLASSIC").upper()
        if mode == "PRECISION_SNIPER":
            return evaluate_precision_sniper(candles, settings, htf_candles)
        if mode == "GMMA_OBV":
            return evaluate_gmma_obv_strategy(candles, settings)
        if mode == "GMMA_GOLD_CROSS":
            return evaluate_gmma_gold_cross_strategy(candles, settings)
        if mode == "LIQUIDITY_SWEEP":
            return evaluate_liquidity_sweep_strategy(candles, settings)
        if mode == "PURE_LIQUIDITY_SWEEP":
            return evaluate_pure_liquidity_sweep_strategy(candles, settings)
        if mode == "GVK_TREND":
            return evaluate_gvk_trend_strategy(candles, settings)
        return None, {"reason": "CLASSIC_NO_CUSTOM_CONFIRMATION"}

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "modes": [
                "CLASSIC",
                "PRECISION_SNIPER",
                "GMMA_OBV",
                "GMMA_GOLD_CROSS",
                "LIQUIDITY_SWEEP",
                "PURE_LIQUIDITY_SWEEP",
                "GVK_TREND",
            ],
        }

