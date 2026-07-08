"""Macro OS v4.5 - Fracture type classification."""

from __future__ import annotations
from typing import Any, Dict


def classify_fracture(macro_state: Any, score: float, features: Dict[str, Any]) -> str:
    """Classify the primary fracture type driving divergence.

    RATE_LED: rate-driven (TIPS up, credit follows)
    CREDIT_LED: credit-driven (credit widening independently)
    CROSS_ASSET_DISLOCATION: multiple axes misaligned
    LIQUIDITY_BREAKDOWN: VIX high + credit collapsing
    """
    tips_t = macro_state.tips_trend
    dxy_t = macro_state.dxy_trend
    gold_t = macro_state.gold_trend
    credit_t = macro_state.credit_trend
    vix = features.get("vix", 20.0)

    # LIQUIDITY_BREAKDOWN: high VIX + credit collapse + gold surge
    if vix > 25 and credit_t < -0.1:
        return "LIQUIDITY_BREAKDOWN"

    # CREDIT_LED: credit tightening independently
    if credit_t < -0.05 and tips_t <= 0:
        return "CREDIT_LED"

    # CROSS_ASSET_DISLOCATION: multiple axes
    misaligned = sum([
        1 for condition in [
            (tips_t > 0) != (credit_t > 0),
            (gold_t > 0) != (credit_t > 0),
            (dxy_t > 0) == (gold_t > 0),
        ] if condition
    ])
    if misaligned >= 2 and score > 0.3:
        return "CROSS_ASSET_DISLOCATION"

    # Default: RATE_LED (rate-driven tightening)
    if tips_t > 0 and score > 0:
        return "RATE_LED"

    return "NONE"
