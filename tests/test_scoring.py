"""Tests for core.scoring."""

from __future__ import annotations

from core.scoring import score, decide
from core.schemas import Decision, DecisionAction


CONFIG = {
    "scoring": {
        "weights": {
            "regime_base": 0.4,
            "trend_strength": 0.25,
            "volatility_adjust": 0.20,
            "liquidity_adjust": 0.15,
        },
        "confidence": {"high_min": 0.7, "medium_min": 0.4, "low_max": 0.4},
    },
    "decision": {
        "long_confidence_min": 0.60,
        "short_confidence_min": 0.65,
        "no_trade_confidence_max": 0.35,
        "reduce_threshold": 0.30,
    },
}


class TestScore:
    def test_score_bounds(self) -> None:
        f = {"dxy": 104.0, "vix": 20.0, "tips_yield": 0.5, "hy_credit_spread": 300}
        risk, conf, reason = score(f, "TRANSITION", CONFIG)
        assert 0.0 <= risk <= 1.0
        assert 0.0 <= conf <= 1.0
        assert isinstance(reason, str)

    def test_score_risk_on_high_confidence(self) -> None:
        f = {"dxy": 98.0, "vix": 15.0, "tips_yield": 0.3, "hy_credit_spread": 250}
        risk, conf, reason = score(f, "RISK_ON", CONFIG)
        assert risk > 0.5
        assert conf > 0.3

    def test_score_squeeze_low_score(self) -> None:
        f = {"dxy": 105.0, "vix": 30.0, "tips_yield": 1.2, "hy_credit_spread": 500}
        risk, conf, reason = score(f, "LIQUIDITY_SQUEEZE", CONFIG)
        assert risk < 0.5

    def test_empty_features(self) -> None:
        risk, conf, reason = score({}, "TRANSITION", CONFIG)
        assert 0.0 <= risk <= 1.0


class TestDecide:
    def test_long_action(self) -> None:
        d = decide(0.8, 0.75, "RISK_ON", CONFIG)
        assert d.action == DecisionAction.LONG

    def test_no_trade_low_confidence(self) -> None:
        d = decide(0.3, 0.2, "TRANSITION", CONFIG)
        assert d.action == DecisionAction.NO_TRADE

    def test_decision_bounds(self) -> None:
        d = decide(0.5, 0.5, "TRANSITION", CONFIG)
        assert isinstance(d, Decision)
        assert 0.0 <= d.risk_score <= 1.0
        assert 0.0 <= d.confidence <= 1.0

    def test_default_config_fallback(self) -> None:
        d = decide(0.5, 0.5, "TRANSITION", {})
        assert isinstance(d, Decision)
        assert d.action in (
            DecisionAction.LONG, DecisionAction.SHORT,
            DecisionAction.REDUCE, DecisionAction.NO_TRADE,
        )
