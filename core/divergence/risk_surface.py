"""Macro OS v4.5 - Risk surface from divergence phase."""

from __future__ import annotations
from typing import Optional, Tuple


def risk_budget_from_phase(phase: str, default_budget: float = 1.0) -> float:
    """Map divergence phase to risk budget multiplier."""
    mapping = {
        "CRISIS": 0.0,
        "LATE": 0.5,
        "MID": 0.75,
        "EARLY": 1.0,
        "NONE": default_budget,
    }
    return mapping.get(phase, default_budget)


def safety_gate_response(phase: str) -> Optional[Tuple[str, float]]:
    """Return (action, budget) for SAFETY_GATE if phase requires it.
    Returns None for NONE (no gate activation).
    """
    mapping = {
        "CRISIS": ("RISK_REDUCE", 0.0),
        "LATE": ("REDUCE", 0.5),
        "MID": ("REDUCE", 0.75),
        "EARLY": ("NEUTRAL", 1.0),
    }
    result = mapping.get(phase)
    if result is None:
        return None
    return result
