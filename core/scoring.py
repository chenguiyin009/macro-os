"""Macro OS — Pure-function scoring engine.

Strictly pure: no IO, no global state, no caching.
Translates features + regime into risk score and confidence.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Tuple

from core.schemas import Decision, DecisionAction, RegimeType


def _sigmoid(x: float, midpoint: float = 0.0, steepness: float = 1.0) -> float:
    """Logistic function for bounded scoring."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def _normalize(value: float, min_val: float, max_val: float) -> float:
    if max_val <= min_val:
        return 0.5
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


def score(features: Dict[str, Any], regime: str, config: Dict[str, Any]) -> Tuple[float, float, str]:
    """Compute risk score and confidence from features and regime."""
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get("weights", {})
    decision_cfg = config.get("decision", {})

    regime_scores = {
        RegimeType.RISK_ON.value: 0.8,
        RegimeType.TRANSITION.value: 0.5,
        RegimeType.TIGHT_LIQUIDITY.value: 0.3,
        RegimeType.LIQUIDITY_SQUEEZE.value: 0.1,
    }
    regime_base = regime_scores.get(regime, 0.5)

    vix = features.get("vix", 20.0)
    trend_strength = 1.0 - _normalize(vix, 10.0, 40.0)
    vol_adj = 1.0 - _sigmoid(vix, midpoint=25.0, steepness=0.3)
    hy = features.get("hy_credit_spread", 300.0)
    liq_adj = 1.0 - _normalize(hy, 200.0, 600.0)

    w_base = weights.get("regime_base", 0.4)
    w_trend = weights.get("trend_strength", 0.25)
    w_vol = weights.get("volatility_adjust", 0.20)
    w_liq = weights.get("liquidity_adjust", 0.15)

    risk_score = (
        w_base * regime_base
        + w_trend * trend_strength
        + w_vol * vol_adj
        + w_liq * liq_adj
    )
    risk_score = max(0.0, min(1.0, risk_score))

    signal_count = sum(
        1 for k in ("dxy", "vix", "tips_yield", "hy_credit_spread") if features.get(k) is not None
    )
    signal_diversity = signal_count / 4.0
    confidence = risk_score * signal_diversity
    confidence = max(0.0, min(1.0, confidence))

    pieces = [f"regime={regime}", f"risk={risk_score:.3f}", f"conf={confidence:.3f}"]
    reason = " | ".join(pieces)

    return risk_score, confidence, reason


def decide(risk_score: float, confidence: float, regime: str, config: Dict[str, Any]) -> Decision:
    """Wrap risk score + confidence into a Decision object."""
    decision_cfg = config.get("decision", {})
    long_min = decision_cfg.get("long_confidence_min", 0.60)
    short_min = decision_cfg.get("short_confidence_min", 0.65)
    no_trade_max = decision_cfg.get("no_trade_confidence_max", 0.35)

    if confidence >= long_min:
        action = DecisionAction.LONG
    elif confidence >= short_min:
        action = DecisionAction.SHORT
    elif confidence <= no_trade_max:
        action = DecisionAction.NO_TRADE
    else:
        action = DecisionAction.REDUCE

    return Decision(
        regime=RegimeType(regime),
        risk_score=risk_score,
        confidence=confidence,
        action=action,
        reason=f"risk={risk_score:.3f} conf={confidence:.3f} action={action.value}",
    )
