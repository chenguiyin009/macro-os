"""Macro OS v4.6 - PhaseHysteresisSmoother + DivergencePhaseEngine.

Wraps existing divergence computation with:
- Multi-Front Resonance penalty
- PhaseHysteresisSmoother (instant upgrade, delayed degradation)
"""

from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple

from .divergence_score import compute_divergence_score
from .divergence_phase import (
    DivergenceState, map_phase_adjusted, compute_adjusted_crisis_threshold,
)
from ..macro.macro_mapper import compute_macro_state


class PhaseHysteresisSmoother:
    """Temporal state machine for divergence phase smoothing.

    Upgrade: instant (no delay for risk escalation)
    Downgrade: needs cooldown + window confirmation
    """

    def __init__(self, window_size: int = 3, cooldown_steps: int = 2):
        self.window_size = window_size
        self.cooldown_steps = cooldown_steps
        self.history: List[Tuple[str, float]] = []
        self.current_confirmed_phase: str = "NONE"
        self.cooldown_counter: int = 0

    def smooth(self, raw_phase: str, score: float) -> str:
        self.history.append((raw_phase, score))
        if len(self.history) > self.window_size:
            self.history.pop(0)

        severity = {"NONE": 0, "EARLY": 1, "MID": 2, "LATE": 3, "CRISIS": 4}
        raw_sev = severity.get(raw_phase, 0)
        curr_sev = severity.get(self.current_confirmed_phase, 0)

        # Upgrade: instant, zero-delay
        if raw_sev > curr_sev:
            self.current_confirmed_phase = raw_phase
            self.cooldown_counter = self.cooldown_steps
            return self.current_confirmed_phase

        # Downgrade: requires cooldown + window confirmation
        if raw_sev < curr_sev:
            if self.cooldown_counter > 0:
                self.cooldown_counter -= 1
                return self.current_confirmed_phase

            recent = [h[0] for h in self.history[-self.cooldown_steps:]]
            if all(severity.get(p, 0) <= raw_sev for p in recent):
                if curr_sev == 2 and raw_sev == 1 and score >= 0.25:
                    return self.current_confirmed_phase
                self.current_confirmed_phase = raw_phase

        return self.current_confirmed_phase

    def reset(self) -> None:
        self.history.clear()
        self.current_confirmed_phase = "NONE"
        self.cooldown_counter = 0


class DivergencePhaseEngine:
    """Enhanced divergence engine with Multi-Front Resonance."""

    def __init__(self, use_pine_data: bool = True) -> None:
        self.smoother = PhaseHysteresisSmoother()
        self.use_pine_data = use_pine_data

    def compute_state(self, features: Dict[str, Any], vix: float = 20.0) -> DivergenceState:
        # 1. Compute macro state
        macro = compute_macro_state(features)

        # 2. Base divergence score (from existing module)
        base_score = compute_divergence_score(
            macro, features, pine_data_available=self.use_pine_data
        )

        # 3. Fracture detection
        fractures: List[str] = []
        tips_t = macro.tips_trend
        credit_t = macro.credit_trend
        gold_t = macro.gold_trend
        dxy_t = macro.dxy_trend

        if tips_t > 0 and credit_t < 0:
            fractures.append("CREDIT_LED")
        if gold_t > 0 and credit_t < 0:
            fractures.append("GOLD_DECOUPLING")
        if dxy_t > 0 and gold_t > 0:
            fractures.append("USD_MISMATCH")
        if vix < 15 and base_score > 0.5:
            fractures.append("VOL_MISMATCH_FILTER")

        # 4. Multi-Front Resonance penalty
        score = base_score
        if len([f for f in fractures if f != "VOL_MISMATCH_FILTER"]) >= 2:
            score = min(1.0, score + 0.2)
            fractures.append("MULTI_FRONT_RESONANCE")

        # Downstream layers treat divergence score as normalized state, so clamp it here.
        score = max(0.0, min(1.0, score))

        # 5. Map to adjusted phase
        raw_phase = map_phase_adjusted(score, vix)

        # 6. Smooth
        confirmed_phase = self.smoother.smooth(raw_phase, score)

        return DivergenceState(
            score=round(score, 4),
            phase=confirmed_phase,
            fracture_type=fractures[0] if fractures else "NONE",
            fractures=fractures if fractures else ["NONE"],
            confidence=round(1.0 - abs(0.5 - score) * 2.0, 4),
        )
