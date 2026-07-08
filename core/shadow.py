"""Macro OS v4.1 — Shadow mode divergence computation.

Compares live vs shadow pipeline outputs:
- DI (Divergence Index): regime + signal divergence
- RDS (Regime Divergence Score): probability distribution divergence
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def compute_divergence_index(
    live_regime: str,
    shadow_regime: str,
    live_confidence: float,
    shadow_confidence: float,
) -> float:
    """Compute Divergence Index between live and shadow outputs.

    DI = 0.0 when identical, 1.0 when maximally divergent.
    Combines regime mismatch + confidence delta.

    Args:
        live_regime: Predicted regime from live pipeline.
        shadow_regime: Predicted regime from shadow pipeline.
        live_confidence: Confidence from live pipeline [0, 1].
        shadow_confidence: Confidence from shadow pipeline [0, 1].

    Returns:
        DI in [0.0, 1.0].
    """
    regime_div = 0.0 if live_regime == shadow_regime else 0.5
    conf_div = abs(live_confidence - shadow_confidence) * 0.5
    return min(1.0, regime_div + conf_div)


def compute_rds(
    live_probs: Dict[str, float],
    shadow_probs: Dict[str, float],
) -> float:
    """Compute Regime Divergence Score via Jensen-Shannon divergence.

    RDS = sqrt(JSD(live_probs || shadow_probs))

    Args:
        live_probs: Regime probability dict from live pipeline.
        shadow_probs: Regime probability dict from shadow pipeline.

    Returns:
        RDS in [0.0, 1.0]. 0 = identical, 1 = maximally divergent.
    """
    all_regimes = set(live_probs.keys()) | set(shadow_probs.keys())
    if not all_regimes:
        return 0.0

    # Build aligned probability vectors
    p = []
    q = []
    for r in all_regimes:
        p.append(live_probs.get(r, 0.0))
        q.append(shadow_probs.get(r, 0.0))

    # Normalize
    p_sum = sum(p)
    q_sum = sum(q)
    if p_sum > 0:
        p = [v / p_sum for v in p]
    if q_sum > 0:
        q = [v / q_sum for v in q]

    # Jensen-Shannon Divergence
    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]

    def kl(a: List[float], b: List[float]) -> float:
        return sum(
            ai * math.log(ai / bi) if ai > 0 and bi > 0 else 0.0
            for ai, bi in zip(a, b)
        )

    jsd = (kl(p, m) + kl(q, m)) / 2.0
    rds = math.sqrt(jsd) if jsd > 0 else 0.0
    return min(1.0, rds)


def compute_shadow_consistency(
    di_history: List[float],
    di_threshold: float = 0.8,
    consecutive_bars: int = 3,
) -> Dict[str, Any]:
    """Check if shadow divergence has been sustained.

    Args:
        di_history: List of recent DI values (newest last).
        di_threshold: DI threshold for divergence.
        consecutive_bars: Number of consecutive bars above threshold.

    Returns:
        Dict with:
        - sustained: bool
        - consecutive_count: int
        - di_mean: float
    """
    count = 0
    for di in reversed(di_history):
        if di > di_threshold:
            count += 1
        else:
            break

    di_mean = sum(di_history) / len(di_history) if di_history else 0.0

    return {
        "sustained": count >= consecutive_bars,
        "consecutive_count": count,
        "di_mean": round(di_mean, 4),
    }
