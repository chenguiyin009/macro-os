"""Macro OS v4.4 - TIPS x DXY world model engine.

First-Class World Model. Processed FIRST in pipeline.
Decision Kernel reads output but NEVER mutates it.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from .macro_state import MacroState
from .macro_types import ConfirmationStatus, Quadrant


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _feature_trends(features: Dict[str, Any]) -> Dict[str, float]:
    tips = features.get("tips_yield", 0.5)
    dxy = features.get("dxy", 100.0)
    gold = features.get("gold", 2300.0)
    credit = features.get("hy_credit_spread", 300.0)
    return {
        "tips_trend": round(tips - 0.5, 4),
        "dxy_trend": round(dxy - 100.0, 4),
        "gold_trend": round((gold - 2300.0) / 2300.0, 4),
        "credit_trend": round((300.0 - credit) / 300.0, 4),
    }


def compute_quadrant(tips_trend: float, dxy_trend: float) -> Quadrant:
    if tips_trend < 0 and dxy_trend < 0:
        return Quadrant.RISK_ON
    if tips_trend > 0 and dxy_trend > 0:
        return Quadrant.TIGHT_LIQUIDITY
    if abs(tips_trend) < 0.2 and abs(dxy_trend) < 0.2:
        return Quadrant.TRANSITION
    return Quadrant.DIVERGENCE


def compute_macro_state(features: Dict[str, Any]) -> MacroState:
    trends = _feature_trends(features)
    quadrant = compute_quadrant(trends["tips_trend"], trends["dxy_trend"])

    axes = [trends["tips_trend"], trends["dxy_trend"] * 0.01,
            trends["gold_trend"], trends["credit_trend"]]
    coherence = 1.0 - min(_std(axes), 1.0)
    macro_confidence = max(0.0, min(1.0, coherence))

    feat_quality = features.get("_feat_quality", {})
    credit_conf = feat_quality.get("hy_credit_spread", 1.0)

    return MacroState(
        quadrant=quadrant,
        tips_trend=trends["tips_trend"],
        dxy_trend=trends["dxy_trend"],
        gold_trend=trends["gold_trend"],
        credit_trend=trends["credit_trend"],
        confirmation_status=ConfirmationStatus.NEUTRAL,
        macro_confidence=round(macro_confidence, 4),
        credit_confidence=credit_conf,
    )
