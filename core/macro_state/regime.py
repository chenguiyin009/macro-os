"""Macro OS - Macro state regime classifier (v2.0, ZIRP trap removal).

Replaces the legacy absolute TIPS threshold (tips_yield_max: 0.5) with a
marginal-change relative valuation system:

  Gate 1: Hard ceiling — TIPS > 2.5% -> one-vote veto (anti-hyperinflation)
  Gate 2: Signal counting — 60d ROC < -15% OR 2y percentile < 25%
  Gate 3: Final decision — signals >= min_signals_required (default 1)

Design principles:
  - No scipy dependency (pure numpy + pandas)
  - Anti-overfitting: parameters frozen until N > 100 real samples
  - Backward compatible: existing compute_regime() in core/regime.py is
    untouched; this module is a standalone, testable component that can
    be integrated into the pipeline when TIPS history is available.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

import numpy as np
import pandas as pd

from core.schemas import RegimeType

logger = logging.getLogger(__name__)

# 2-year window in business days (approx 252 * 2)
_WINDOW_2Y = 504


def _determine_risk_on(
    tips_yield: float,
    tips_history: pd.Series,
    config: Dict[str, Any],
) -> bool:
    """ZIRP-trap-removal RISK_ON gate.

    Uses marginal-change signals instead of a fixed absolute threshold.

    Args:
        tips_yield: Current TIPS yield in percent (e.g. 2.32 means 2.32%).
        tips_history: TIPS yield historical series (ascending time, latest last).
        config: Full config dict (must contain ``regime.risk_on`` sub-dict).

    Returns:
        True if RISK_ON is permitted.
    """
    cfg = config.get("regime", {}).get("risk_on", {})
    if not cfg:
        logger.warning("Missing 'regime.risk_on' config — denying RISK_ON.")
        return False

    # ================================================================
    # Gate 1: Hard ceiling (one-vote veto)
    # ================================================================
    tips_absolute_max = cfg.get("tips_absolute_max", 2.5)
    if tips_yield > tips_absolute_max:
        logger.info(
            "RISK_ON BLOCKED: TIPS %.2f%% > hard ceiling %.1f%%",
            tips_yield,
            tips_absolute_max,
        )
        return False

    # ================================================================
    # Gate 2: Marginal-change signal counting
    # ================================================================
    signals = 0
    signal_details: list[str] = []

    # --- Signal A: 60-day rate of change ---
    tips_roc_threshold = cfg.get("tips_roc_60d_threshold", -0.15)
    if len(tips_history) >= 60:
        tips_60d_ago = tips_history.iloc[-60]
        if tips_60d_ago != 0:
            roc_60d = (tips_yield - tips_60d_ago) / abs(tips_60d_ago)
        else:
            roc_60d = 0.0
        if roc_60d < tips_roc_threshold:
            signals += 1
            signal_details.append(
                f"60d_ROC={roc_60d:.2%} < {tips_roc_threshold:.2%}"
            )
    else:
        logger.warning(
            "TIPS history length (%d) < 60, skipping 60d ROC signal",
            len(tips_history),
        )

    # --- Signal B: 2-year rolling percentile ---
    tips_percentile_2y = cfg.get("tips_percentile_2y", 0.25)
    if len(tips_history) >= _WINDOW_2Y:
        recent_2y = tips_history.iloc[-_WINDOW_2Y:].values
        # Pure-numpy percentileofscore equivalent (kind='mean'):
        # fraction of values <= tips_yield
        percentile = float(np.mean(recent_2y <= tips_yield))
        if percentile < tips_percentile_2y:
            signals += 1
            signal_details.append(
                f"2y_percentile={percentile:.2%} < {tips_percentile_2y:.2%}"
            )
    else:
        logger.warning(
            "TIPS history length (%d) < %d, skipping 2y percentile signal",
            len(tips_history),
            _WINDOW_2Y,
        )

    # ================================================================
    # Gate 3: Final decision
    # ================================================================
    min_required = cfg.get("min_signals_required", 1)
    risk_on = signals >= min_required

    logger.info(
        "RISK_ON decision: %s | TIPS=%.2f%% | signals=%d/%d | details=[%s]",
        risk_on,
        tips_yield,
        signals,
        min_required,
        ", ".join(signal_details) if signal_details else "none",
    )

    return risk_on


def _extract_tips_yield(features: Union[Dict[str, Any], Any]) -> Optional[float]:
    """Extract tips_yield from either a dict or an object with attributes."""
    if isinstance(features, dict):
        return features.get("tips_yield")
    return getattr(features, "tips_yield", None)


def determine_regime(
    features: Union[Dict[str, Any], Any],
    tips_history: pd.Series,
    config: Dict[str, Any],
) -> str:
    """Main regime classification entry point (ZIRP trap removal v2.0).

    This is a **complementary** classifier to the existing ``compute_regime``
    in ``core/regime.py``. It focuses on the RISK_ON gate using the new
    marginal-change logic. The existing ``compute_regime`` remains the
    primary classifier for the full 4-regime state machine; this module
    can be wired in once TIPS history is available in the feature pipeline.

    Args:
        features: Feature dict or schema object (must contain tips_yield).
        tips_history: TIPS yield historical series (ascending time).
        config: Full config dict.

    Returns:
        RegimeType label string.
    """
    tips_yield = _extract_tips_yield(features)

    if tips_yield is None:
        logger.warning("No TIPS data detected — degrading to TRANSITION.")
        return RegimeType.TRANSITION.value

    if _determine_risk_on(tips_yield, tips_history, config):
        return RegimeType.RISK_ON.value

    return RegimeType.TRANSITION.value
