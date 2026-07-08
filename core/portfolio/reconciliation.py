"""Portfolio reconciliation engine - L4.5 Constitution checks."""

from __future__ import annotations

import logging
from typing import Dict

from config.config_loader import constraints

logger = logging.getLogger(__name__)

DEFAULT_REBALANCE_THRESHOLD = 0.03


def compute_actionable_diff(target_weights: Dict[str, float], actual_weights: Dict[str, float]) -> Dict[str, float]:
    """Compare target weights vs actual weights and keep only material drift."""
    actionable_diff: Dict[str, float] = {}

    try:
        threshold = float(constraints.reconciliation.get("rebalance_threshold", DEFAULT_REBALANCE_THRESHOLD))
    except Exception:
        threshold = DEFAULT_REBALANCE_THRESHOLD

    logger.info("Reconciliation Engine: Starting diff compute (Threshold: %.1f%%)", threshold * 100)

    all_assets = set(target_weights.keys()).union(set(actual_weights.keys()))

    for asset in sorted(all_assets):
        target = float(target_weights.get(asset, 0.0) or 0.0)
        actual = float(actual_weights.get(asset, 0.0) or 0.0)

        delta = target - actual
        if abs(delta) >= threshold:
            actionable_diff[asset] = round(delta, 4)
            logger.debug(
                "Actionable Diff generated for %s: Target=%.2f, Actual=%.2f -> Delta=%+.2f",
                asset,
                target,
                actual,
                delta,
            )
        elif abs(delta) > 0.001:
            logger.debug(
                "Ignored minor drift for %s: Target=%.2f, Actual=%.2f (Delta %+.2f < Threshold)",
                asset,
                target,
                actual,
                delta,
            )

    if not actionable_diff:
        logger.info("Reconciliation Engine: Portfolio is balanced. No actionable diff generated.")
    else:
        logger.info("Reconciliation Engine: Generated diff targets: %s", actionable_diff)

    return actionable_diff
