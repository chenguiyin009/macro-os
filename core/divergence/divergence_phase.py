"""Macro OS v4.5 - Divergence phase mapping + DivergenceState."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DivergenceState:
    score: float = 0.0
    phase: str = "NONE"
    fracture_type: str = "NONE"
    fractures: list = field(default_factory=lambda: ["NONE"])
    confidence: float = 0.0


def map_phase(score: float) -> str:
    if score >= 0.85:
        return "CRISIS"
    elif score >= 0.6:
        return "LATE"
    elif score >= 0.4:
        return "MID"
    elif score >= 0.2:
        return "EARLY"
    return "NONE"




def compute_adjusted_crisis_threshold(vix: float, base: float = 0.85) -> float:
    if vix < 16:
        return 0.92
    return base


def map_phase_adjusted(score: float, vix: float) -> str:
    crisis_threshold = compute_adjusted_crisis_threshold(vix)
    if score >= crisis_threshold:
        return "CRISIS"
    elif score >= 0.6:
        return "LATE"
    elif score >= 0.4:
        return "MID"
    elif score >= 0.2:
        return "EARLY"
    return "NONE"

def compute_confidence(score: float) -> float:
    """Confidence = 1 - distance from midpoint (0.5).
    Higher score = more certain the divergence is real.
    """
    return max(0.0, min(1.0, 1.0 - abs(0.5 - score) * 2.0))

# Budget State Machine
BUDGET_LATE = "LATE_DIVERGENCE"
BUDGET_CONFIRMED = "CONFIRMED_BREAKDOWN"
BUDGET_HEALING = "MACRO_HEALING"

def compute_budget_state(
    phase: str,
    score: float,
    volume_divergence_active: bool = False,
    macro_crack_age: int = 0,
    score_recovery_bars: int = 0,
) -> tuple:
    """State machine for dynamic risk budget.
    
    States:
    - LATE_DIVERGENCE: 50% budget (default defensive)
    - CONFIRMED_BREAKDOWN: 0% (volume confirms macro cracks)
    - MACRO_HEALING: gradual recovery (50% + 10%/bar)
    """
    # CONFIRMED_BREAKDOWN: volume confirms macro divergence
    if phase == "LATE" and volume_divergence_active and macro_crack_age >= 5:
        return (BUDGET_CONFIRMED, 0.0)
    
    # MACRO_HEALING: divergence resolving
    if score < 0.50 and score_recovery_bars >= 3:
        recovery = min(0.50 + score_recovery_bars * 0.10, 1.0)
        return (BUDGET_HEALING, round(recovery, 2))
    
    # LATE_DIVERGENCE: default for late/mid/early
    if phase in ("LATE", "MID", "EARLY"):
        mapping = {"LATE": 0.50, "MID": 0.75, "EARLY": 1.0}
        return (BUDGET_LATE, mapping.get(phase, 0.50))
    
    return ("NORMAL", 1.0)
