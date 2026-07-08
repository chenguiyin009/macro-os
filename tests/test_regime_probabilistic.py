"""Tests for soft regime probability layer."""

from __future__ import annotations

from core.regime_probabilistic import (
    compute_regime_probs, probs_to_hard_label, prob_distance,
)


class TestSoftRegime:
    def test_probs_sum_to_one(self) -> None:
        probs = compute_regime_probs({"dxy": 98.0, "vix": 15.0, "tips_yield": 0.3, "hy_credit_spread": 250})
        total = probs.risk_on + probs.tight_liquidity + probs.liquidity_squeeze + probs.transition
        assert abs(total - 1.0) < 0.01

    def test_risk_on_high_probability(self) -> None:
        probs = compute_regime_probs({"dxy": 90.0, "vix": 8.0, "tips_yield": 0.1, "hy_credit_spread": 150})
        assert probs.risk_on > 0.15

    def test_squeeze_high_vix(self) -> None:
        probs = compute_regime_probs({"dxy": 100.0, "vix": 35.0, "tips_yield": 0.5, "hy_credit_spread": 500})
        assert probs.liquidity_squeeze > probs.risk_on

    def test_probs_to_hard_label_risk_on(self) -> None:
        probs = compute_regime_probs({"dxy": 96.0, "vix": 12.0, "tips_yield": 0.2})
        label = probs_to_hard_label(probs)
        assert label in ("RISK_ON", "TIGHT_LIQUIDITY", "LIQUIDITY_SQUEEZE", "TRANSITION")

    def test_prob_distance_identical(self) -> None:
        probs = compute_regime_probs({"dxy": 100.0, "vix": 20.0})
        assert prob_distance(probs, probs) < 0.001

    def test_prob_distance_different(self) -> None:
        a = compute_regime_probs({"dxy": 96.0, "vix": 12.0})
        b = compute_regime_probs({"dxy": 105.0, "vix": 30.0})
        assert prob_distance(a, b) > 0.001

    def test_empty_features(self) -> None:
        probs = compute_regime_probs({})
        total = probs.risk_on + probs.tight_liquidity + probs.liquidity_squeeze + probs.transition
        assert abs(total - 1.0) < 0.01

    def test_transition_in_mixed_signals(self) -> None:
        probs = compute_regime_probs({"dxy": 101.0, "vix": 22.0, "tips_yield": 0.6, "hy_credit_spread": 350})
        total = probs.risk_on + probs.tight_liquidity + probs.liquidity_squeeze + probs.transition
        assert abs(total - 1.0) < 0.01
