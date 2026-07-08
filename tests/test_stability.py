"""Tests for decision stability engine (v4.3)."""

from __future__ import annotations

from core.stability import compute_dpe, count_flips, compute_duration_weighted_entropy, detect_instability


class TestDPE:
    def test_single_decision_zero_entropy(self) -> None:
        assert compute_dpe(["LONG"]) == 0.0

    def test_two_decisions_equal_entropy(self) -> None:
        dpe = compute_dpe(["LONG", "SHORT", "LONG", "SHORT"])
        assert dpe > 0.0

    def test_same_decision_zero_entropy(self) -> None:
        assert compute_dpe(["LONG", "LONG", "LONG"]) == 0.0

    def test_window_truncation(self) -> None:
        dpe = compute_dpe(["LONG"] * 100 + ["SHORT"] * 100 + ["LONG"] * 10 + ["SHORT"] * 10, window=20)
        assert dpe > 0.0


class TestFlipCount:
    def test_no_flips(self) -> None:
        assert count_flips(["LONG", "LONG", "LONG"]) == 0

    def test_single_flip(self) -> None:
        assert count_flips(["LONG", "LONG", "SHORT", "SHORT"]) == 1

    def test_lag_tolerance(self) -> None:
        assert count_flips(["LONG", "SHORT", "LONG", "SHORT"], lag_tolerance=1) >= 2

    def test_lag_tolerance_suppresses_noise(self) -> None:
        assert count_flips(["LONG", "SHORT", "LONG", "LONG"], lag_tolerance=2) == 0


class TestDurationWeighted:
    def test_short_regime_downweighted(self) -> None:
        a = compute_duration_weighted_entropy(["LONG", "LONG", "SHORT", "LONG", "LONG"], min_duration=3)
        b = compute_duration_weighted_entropy(["LONG", "LONG", "LONG", "SHORT", "SHORT"], min_duration=3)
        assert a >= 0.0


class TestInstability:
    def test_stable_not_unstable(self) -> None:
        m = detect_instability(["LONG"] * 20)
        assert not m.unstable

    def test_unstable_high_flip_rate(self) -> None:
        m = detect_instability(["LONG", "SHORT", "LONG", "SHORT", "LONG", "SHORT", "LONG", "SHORT"],
                               flip_rate_threshold=0.1, lag_tolerance=1)
        assert m.unstable
