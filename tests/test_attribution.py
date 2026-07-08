"""Tests for error attribution engine."""

from __future__ import annotations

from core.schemas import SoftRegimeProbs
from core.attribution import (
    compute_feature_error, compute_regime_error,
    compute_execution_cost_error, explain_error, attribution_summary,
)


class TestFeatureError:
    def test_no_baseline_uses_vix(self) -> None:
        err = compute_feature_error({"vix": 10.0})
        assert 0.0 <= err <= 1.0

    def test_baseline_deviation(self) -> None:
        err = compute_feature_error({"dxy": 105.0}, {"dxy": 100.0})
        assert err > 0.0

    def test_identical_baseline(self) -> None:
        err = compute_feature_error({"dxy": 100.0}, {"dxy": 100.0})
        assert err == 0.0


class TestRegimeError:
    def test_identical_probs_zero_error(self) -> None:
        p = SoftRegimeProbs(risk_on=0.5, tight_liquidity=0.3, liquidity_squeeze=0.1, transition=0.1)
        err = compute_regime_error(p, p)
        assert err < 0.01

    def test_different_probs_positive_error(self) -> None:
        a = SoftRegimeProbs(risk_on=0.7, tight_liquidity=0.1, liquidity_squeeze=0.1, transition=0.1)
        b = SoftRegimeProbs(risk_on=0.1, tight_liquidity=0.7, liquidity_squeeze=0.1, transition=0.1)
        err = compute_regime_error(a, b)
        assert err > 0.0


class TestExecutionCost:
    def test_zero_cost(self) -> None:
        assert compute_execution_cost_error(0.0) == 0.0

    def test_high_cost_capped(self) -> None:
        assert compute_execution_cost_error(200.0) == 1.0

    def test_moderate_cost(self) -> None:
        err = compute_execution_cost_error(50.0)
        assert 0.0 < err < 1.0


class TestExplainError:
    def test_identical_all(self) -> None:
        p = SoftRegimeProbs(risk_on=0.5, tight_liquidity=0.3, liquidity_squeeze=0.1, transition=0.1)
        result = explain_error(p, p, {"vix": 15.0}, {"vix": 15.0}, 0.0)
        assert result.total_error <= 1.0

    def test_total_error_bounded(self) -> None:
        a = SoftRegimeProbs(risk_on=0.8, tight_liquidity=0.1, liquidity_squeeze=0.05, transition=0.05)
        b = SoftRegimeProbs(risk_on=0.1, tight_liquidity=0.8, liquidity_squeeze=0.05, transition=0.05)
        result = explain_error(a, b, {"vix": 30.0}, {"vix": 15.0}, 80.0)
        assert result.total_error >= 0.0


class TestAttributionSummary:
    def test_empty_list(self) -> None:
        s = attribution_summary([])
        assert s["dominant_failure_mode"] == "feature"

    def test_single_result(self) -> None:
        p = SoftRegimeProbs()
        r = explain_error(p, p, {"vix": 15.0}, {"vix": 15.0}, 0.0)
        s = attribution_summary([r])
        assert "feature_error_pct" in s
        assert "regime_error_pct" in s
        assert "execution_cost_pct" in s
