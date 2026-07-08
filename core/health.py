"""Macro OS v4.1 — System health engine.

Provides:
- MDI (Model Drift Index): KL divergence between HMM live vs replay probs
- SystemState: LOW / MID / HIGH
- Safety Gate: integrates DI + MDI + PnL stability
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from core.schemas import SafetyGateResult, SystemState


def compute_mdi(
    live_probs: Dict[str, float],
    replay_probs: Dict[str, float],
) -> float:
    """Compute Model Drift Index via KL divergence.

    MDI = KL(live || replay), clamped to [0, 1].

    Args:
        live_probs: HMM regime probabilities from live inference.
        replay_probs: HMM regime probabilities from replay baseline.

    Returns:
        MDI in [0.0, 1.0].
    """
    all_regimes = set(live_probs.keys()) | set(replay_probs.keys())
    if not all_regimes:
        return 0.0

    p = [live_probs.get(r, 0.0) for r in all_regimes]
    q = [replay_probs.get(r, 0.0) for r in all_regimes]

    p_sum = sum(p)
    q_sum = sum(q)
    if p_sum > 0:
        p = [v / p_sum for v in p]
    if q_sum > 0:
        q = [v / q_sum for v in q]

    kl = sum(
        pi * math.log(pi / qi) if pi > 0 and qi > 0 else 0.0
        for pi, qi in zip(p, q)
    )
    return min(1.0, max(0.0, kl))


def classify_mdi(mdi: float, low_threshold: float = 0.35, high_threshold: float = 0.70) -> str:
    """Classify MDI into LOW / MID / HIGH.

    Args:
        mdi: Model Drift Index value.
        low_threshold: Upper bound for LOW state.
        high_threshold: Lower bound for HIGH state.

    Returns:
        "LOW", "MID", or "HIGH".
    """
    if mdi <= low_threshold:
        return "LOW"
    elif mdi >= high_threshold:
        return "HIGH"
    return "MID"


def compute_pnl_stability(period_returns: List[float], window: int = 20) -> float:
    """Compute rolling PnL stability as Sharpe over recent returns.

    Args:
        period_returns: List of period returns (newest last).
        window: Lookback window.

    Returns:
        PnL stability score [0, 1]. Higher = more stable.
    """
    recent = period_returns[-window:] if len(period_returns) > window else period_returns
    if len(recent) < 2:
        return 0.5

    mean_ret = sum(recent) / len(recent)
    variance = sum((r - mean_ret) ** 2 for r in recent) / (len(recent) - 1)
    if variance <= 0:
        return 0.5

    sharpe = (mean_ret / math.sqrt(variance)) * math.sqrt(252)
    # Normalize: sharpe of 2.0+ = stable, 0 = unstable
    stability = min(1.0, max(0.0, (sharpe + 2.0) / 4.0))
    return stability


def safety_gate(
    di: float,
    di_consecutive: int,
    di_threshold: float = 0.8,
    consecutive_bars: int = 3,
    mdi: float = 0.0,
    mdi_threshold_high: float = 0.70,
    pnl_stability: float = 0.5,
    pnl_stability_threshold: float = 0.3,
) -> Tuple[SystemState, str]:
    """Evaluate safety gate and return system state.

    Priority order:
    1. DEGRADED: DI > threshold for N consecutive bars
    2. RISK_REDUCE: MDI > high threshold
    3. DEFENSIVE: PnL stability below threshold
    4. ACTIVE: normal operation

    Args:
        di: Current Divergence Index.
        di_consecutive: Consecutive bars above DI threshold.
        di_threshold: DI threshold for divergence.
        consecutive_bars: Bars required for DEGRADED.
        mdi: Model Drift Index.
        mdi_threshold_high: MDI threshold for RISK_REDUCE.
        pnl_stability: Recent PnL stability [0, 1].
        pnl_stability_threshold: Minimum acceptable stability.

    Returns:
        Tuple of (SystemState, reason string).
    """
    # Level 1: DEGRADED (hard kill)
    if di_consecutive >= consecutive_bars:
        return SystemState.DEGRADED, (
            f"Shadow divergence sustained: DI={di:.2f} for {di_consecutive} bars"
        )

    # Level 2: RISK_REDUCE (model mismatch)
    if mdi > mdi_threshold_high:
        return SystemState.DEFENSIVE, (
            f"Model drift elevated: MDI={mdi:.2f}"
        )

    # Level 3: DEFENSIVE (PnL instability)
    if pnl_stability < pnl_stability_threshold:
        return SystemState.DEFENSIVE, (
            f"PnL stability degraded: {pnl_stability:.2f} < {pnl_stability_threshold}"
        )

    # Level 4: ACTIVE
    return SystemState.ACTIVE, "All systems nominal"


def risk_budget_for_state(state: SystemState, active_budget: float = 0.6,
                           defensive_budget: float = 0.3,
                           degraded_budget: float = 0.0) -> float:
    """Return risk budget multiplier for given system state."""
    mapping = {
        SystemState.ACTIVE: active_budget,
        SystemState.DEFENSIVE: defensive_budget,
        SystemState.DEGRADED: degraded_budget,
    }
    return mapping.get(state, 0.3)
