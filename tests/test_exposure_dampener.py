from __future__ import annotations

import math

from core.exposure_dampener import NonLinearExposureDampener


def test_sigmoid_curve_owns_mid_and_late_transitions() -> None:
    dampener = NonLinearExposureDampener()

    mid = dampener.calculate_max_exposure(0.5, "MID")
    late = dampener.calculate_max_exposure(0.6, "LATE")

    expected_mid = 1.0 / (1.0 + math.exp(dampener.k * (0.5 - dampener.x0)))
    expected_late = 1.0 / (1.0 + math.exp(dampener.k * (0.6 - dampener.x0)))

    assert abs(mid - expected_mid) < 1e-9
    assert abs(late - expected_late) < 1e-9


def test_phase_ceilings_remain_guardrails_not_primary_output() -> None:
    dampener = NonLinearExposureDampener()

    early = dampener.calculate_max_exposure(0.2, "EARLY")
    mid = dampener.calculate_max_exposure(0.4, "MID")
    late = dampener.calculate_max_exposure(0.6, "LATE")

    assert 0.90 < early < 1.0
    assert 0.50 < mid < 0.75
    assert 0.15 < late < 0.40
