"""Macro OS v4.2 — Error attribution engine.

Decomposes replay error into:
- feature_error: noise/quality of features
- regime_error: misclassification by regime model
- execution_cost: transaction cost impact
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from core.schemas import AttributionResult, SoftRegimeProbs


def compute_feature_error(
    features: Dict[str, Any],
    baseline_features: Optional[Dict[str, Any]] = None,
) -> float:
    """Estimate error attributable to feature noise.

    Computes normalized deviation of current features from baseline.
    Higher = more feature noise / uncertainty.

    Args:
        features: Current feature dict.
        baseline_features: Reference feature dict (e.g. smoothed). If None,
                          assumes features themselves are uncertain.

    Returns:
        Feature error score [0, 1].
    """
    if baseline_features is None:
        # No baseline: estimate uncertainty from feature volatility
        vol = abs(features.get("vix", 20.0)) / 40.0
        return min(1.0, vol)

    errors = []
    for key in ("dxy", "vix", "tips_yield", "hy_credit_spread", "gold"):
        cur = features.get(key)
        base = baseline_features.get(key)
        if cur is not None and base is not None and base != 0:
            rel_err = abs(cur - base) / abs(base)
            errors.append(min(1.0, rel_err))

    return sum(errors) / len(errors) if errors else 0.0


def compute_regime_error(
    predicted_probs: SoftRegimeProbs,
    actual_probs: SoftRegimeProbs,
) -> float:
    """Compute error from regime misclassification using KL divergence.

    Args:
        predicted_probs: Soft regime probs from the model.
        actual_probs: Ground truth soft regime probs.

    Returns:
        Regime error score [0, 1].
    """
    p = [predicted_probs.risk_on, predicted_probs.tight_liquidity,
         predicted_probs.liquidity_squeeze, predicted_probs.transition]
    q = [actual_probs.risk_on, actual_probs.tight_liquidity,
         actual_probs.liquidity_squeeze, actual_probs.transition]

    # Normalize
    p_sum = sum(p) or 1.0
    q_sum = sum(q) or 1.0
    p = [v / p_sum for v in p]
    q = [v / q_sum for v in q]

    # KL(p || q) clamped
    kl = sum(
        pi * math.log(pi / qi) if pi > 0 and qi > 0 else 0.0
        for pi, qi in zip(p, q)
    )
    return min(1.0, max(0.0, kl))


def compute_execution_cost_error(total_costs_bps: float, max_reasonable_bps: float = 100.0) -> float:
    """Normalize transaction costs into an error score.

    Args:
        total_costs_bps: Total costs in basis points.
        max_reasonable_bps: Max bps considered (default 100 = 1%).

    Returns:
        Execution cost error [0, 1].
    """
    return min(1.0, total_costs_bps / max_reasonable_bps)


def explain_error(
    predicted_probs: SoftRegimeProbs,
    actual_probs: SoftRegimeProbs,
    features: Dict[str, Any],
    baseline_features: Optional[Dict[str, Any]] = None,
    total_costs_bps: float = 0.0,
) -> AttributionResult:
    """Decompose total error into feature / regime / execution components.

    Args:
        predicted_probs: Soft probs from the model.
        actual_probs: Ground truth soft probs.
        features: Current feature dict.
        baseline_features: Baseline feature dict (optional).
        total_costs_bps: Total transaction costs in bps.

    Returns:
        AttributionResult with decomposed errors.
    """
    feature_err = compute_feature_error(features, baseline_features)
    regime_err = compute_regime_error(predicted_probs, actual_probs)
    execution_err = compute_execution_cost_error(total_costs_bps)

    total = feature_err + regime_err + execution_err

    # Normalize so components sum to total
    return AttributionResult(
        feature_error=round(feature_err / (total or 1.0), 4),
        regime_error=round(regime_err / (total or 1.0), 4),
        execution_cost=round(execution_err / (total or 1.0), 4),
        total_error=round(min(1.0, total / 3.0), 4),
    )


def attribution_summary(results: List[AttributionResult]) -> Dict[str, Any]:
    """Summarize attribution across multiple replay steps.

    Args:
        results: List of AttributionResult from each step.

    Returns:
        Dict with average attribution breakdown.
    """
    if not results:
        return {"feature_error_pct": 0, "regime_error_pct": 0, "execution_cost_pct": 0, "dominant_failure_mode": "feature"}

    feat = sum(r.feature_error for r in results) / len(results)
    reg = sum(r.regime_error for r in results) / len(results)
    exec_ = sum(r.execution_cost for r in results) / len(results)

    return {
        "feature_error_pct": round(feat * 100, 1),
        "regime_error_pct": round(reg * 100, 1),
        "execution_cost_pct": round(exec_ * 100, 1),
        "dominant_failure_mode": max(
            ("feature", feat),
            ("regime", reg),
            ("execution_cost", exec_),
            key=lambda x: x[1],
        )[0],
    }
