"""Macro OS - Feature transformation pipeline.

No decision logic. Transforms raw data into the feature dict consumed
by regime.py and scoring.py.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.schemas import FeatureSchema


def build_features(raw: FeatureSchema) -> Dict[str, Any]:
    """Convert a validated FeatureSchema into the internal feature dict.

    Performs minimal derived calculations (e.g. spreads, ratios)
    but contains zero decision logic.

    Args:
        raw: Validated FeatureSchema from LLM parser or mock.

    Returns:
        Feature dict suitable for regime.py and scoring.py.
    """
    features: Dict[str, Any] = {}

    if raw.dxy is not None:
        features["dxy"] = raw.dxy
    if raw.vix is not None:
        features["vix"] = raw.vix
    if raw.ovx is not None:
        features["ovx"] = raw.ovx
    if raw.hy_credit_spread is not None:
        features["hy_credit_spread"] = raw.hy_credit_spread
    if raw.tips_yield is not None:
        features["tips_yield"] = raw.tips_yield
    if raw.tips_yield_roc_60d is not None:
        features["tips_yield_roc_60d"] = raw.tips_yield_roc_60d
    if raw.dxy_zscore_60d is not None:
        features["dxy_zscore_60d"] = raw.dxy_zscore_60d
    if raw.core_pce is not None:
        features["core_pce"] = raw.core_pce
    if raw.gold is not None:
        features["gold"] = raw.gold
    if raw.equity_tech_rotation is not None:
        features["equity_tech_rotation"] = raw.equity_tech_rotation

    # Already on FeatureSchema; must reach orchestrator/policy consumers.
    # recovery_signal is read by run_pipeline for days_in_recovery.
    # danger_score is owned by policy_engine (0-100), not pre-kernel fold.
    features["danger_score"] = raw.danger_score
    features["fragility_score"] = raw.fragility_score
    features["risk_score"] = raw.risk_score
    features["recovery_signal"] = raw.recovery_signal

    features["_source"] = raw.source.value
    features["_fetched_at"] = raw.fetched_at.isoformat()

    return features


def derive_trend_deltas(
    current: Dict[str, Any], previous: Optional[Dict[str, Any]] = None
) -> Dict[str, float]:
    """Compute deltas between current and previous feature snapshots.

    Args:
        current: Current feature dict.
        previous: Previous feature dict, or None.

    Returns:
        Dict of delta values keyed as ``{field}_delta``.
    """
    deltas: Dict[str, float] = {}
    if previous is None:
        for key in ("dxy", "vix", "tips_yield", "hy_credit_spread", "gold"):
            deltas[f"{key}_delta"] = 0.0
        return deltas

    for key in ("dxy", "vix", "tips_yield", "hy_credit_spread", "gold"):
        cur_val = current.get(key)
        prev_val = previous.get(key)
        if cur_val is not None and prev_val is not None:
            deltas[f"{key}_delta"] = cur_val - prev_val
        else:
            deltas[f"{key}_delta"] = 0.0

    return deltas
