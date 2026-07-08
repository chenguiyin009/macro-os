"""Macro OS v4.2 — Soft regime probability layer.

Transforms hard regime classification into probabilistic regime space.
Each regime gets a probability [0,1] representing regime mixture.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from core.schemas import SoftRegimeProbs


def _sigmoid(x: float, midpoint: float = 0.0, steepness: float = 1.0) -> float:
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def _softmax(scores: Dict[str, float]) -> Dict[str, float]:
    total = sum(math.exp(s) for s in scores.values())
    if total <= 0:
        return {k: 0.25 for k in scores}
    return {k: math.exp(v) / total for k, v in scores.items()}


def compute_regime_probs(features: Dict[str, Any]) -> SoftRegimeProbs:
    """Compute soft regime probabilities from feature vector.

    Uses sigmoid-based scoring for each regime dimension,
    then softmax-normalizes into a probability distribution.

    Args:
        features: Dict with keys like dxy, vix, tips_yield, hy_credit_spread.

    Returns:
        SoftRegimeProbs with probability for each regime.
    """
    dxy = features.get("dxy", 100.0)
    vix = features.get("vix", 20.0)
    tips = features.get("tips_yield", 0.5)
    hy = features.get("hy_credit_spread", 300.0)

    # Regime scores (higher = more likely)
    # RISK_ON: low TIPS, weak DXY, low VIX
    risk_on_score = (
        _sigmoid(-tips, midpoint=0.5, steepness=3.0)
        + _sigmoid(-dxy, midpoint=100.0, steepness=0.3)
        + _sigmoid(-vix, midpoint=20.0, steepness=0.2)
    )

    # TIGHT_LIQUIDITY: high TIPS, strong DXY, moderate VIX
    tight_score = (
        _sigmoid(tips, midpoint=0.8, steepness=3.0)
        + _sigmoid(dxy, midpoint=103.0, steepness=0.3)
        + _sigmoid(-vix, midpoint=25.0, steepness=0.2)
    )

    # LIQUIDITY_SQUEEZE: high VIX, wide HY spreads
    squeeze_score = (
        _sigmoid(vix, midpoint=25.0, steepness=0.3)
        + _sigmoid(hy, midpoint=400, steepness=0.01)
    )

    # TRANSITION: catch-all (moderate on all dimensions)
    transition_score = 1.0

    scores = {
        "risk_on": risk_on_score,
        "tight_liquidity": tight_score,
        "liquidity_squeeze": squeeze_score,
        "transition": transition_score,
    }

    probs = _softmax(scores)
    return SoftRegimeProbs(
        risk_on=round(probs["risk_on"], 4),
        tight_liquidity=round(probs["tight_liquidity"], 4),
        liquidity_squeeze=round(probs["liquidity_squeeze"], 4),
        transition=round(probs["transition"], 4),
    )


def probs_to_hard_label(probs: SoftRegimeProbs) -> str:
    """Convert soft probs back to a hard regime label (for compatibility).

    Args:
        probs: SoftRegimeProbs instance.

    Returns:
        Hard regime label string.
    """
    mapping = {
        "RISK_ON": probs.risk_on,
        "TIGHT_LIQUIDITY": probs.tight_liquidity,
        "LIQUIDITY_SQUEEZE": probs.liquidity_squeeze,
        "TRANSITION": probs.transition,
    }
    return max(mapping, key=mapping.get)


def prob_distance(a: SoftRegimeProbs, b: SoftRegimeProbs) -> float:
    """Compute L2 distance between two soft regime distributions.

    Args:
        a, b: SoftRegimeProbs instances.

    Returns:
        Euclidean distance [0, 2].
    """
    return math.sqrt(
        (a.risk_on - b.risk_on) ** 2
        + (a.tight_liquidity - b.tight_liquidity) ** 2
        + (a.liquidity_squeeze - b.liquidity_squeeze) ** 2
        + (a.transition - b.transition) ** 2
    )
