"""Macro OS v4.6 - NonLinearExposureDampener.

Replaces hard budget cuts with sigmoid-based continuous exposure curve.
Phase with score: Exposure = sigmoid(k * (score - x0)).
Phase ceilings are guardrails, not the primary output curve.
"""

from __future__ import annotations

import math


class NonLinearExposureDampener:
    """Sigmoid-based exposure ceiling, no hard cuts."""

    def __init__(self, k: float = 8.0, x0: float = 0.5) -> None:
        self.k = k      # curve steepness
        self.x0 = x0    # inflection point (EARLY -> MID boundary)

    def calculate_max_exposure(self, score: float, phase: str) -> float:
        score = max(0.0, min(1.0, score))
        if phase == "NONE":
            return 1.0
        if phase == "CRISIS":
            return 0.0

        continuous = 1.0 / (1.0 + math.exp(self.k * (score - self.x0)))
        ceilings = {"EARLY": 0.98, "MID": 0.72, "LATE": 0.35}
        return min(continuous, ceilings.get(phase, 1.0))
