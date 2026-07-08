"""v4.3.1 ? Transition kernel constitutional tests."""

from __future__ import annotations

from core.decision_kernel import decide, risk_budget_for_kernel
from core.schemas import AuthorityLevel, DecisionAction, RegimeType

CONFIG = {"decision": {
    "long_confidence_min": 0.60, "short_confidence_min": 0.65,
    "no_trade_confidence_max": 0.35, "reduce_threshold": 0.30,
}}


class TestTransitionVeto:
    def test_transition_always_risk_reduce(self) -> None:
        for _ in range(100):
            kd = decide({}, "TRANSITION", "TRANSITION", 0.7, 0.7, CONFIG)
            assert kd.decision.action == DecisionAction.RISK_REDUCE

    def test_transition_always_hard_veto(self) -> None:
        for conf in [x * 0.1 for x in range(11)]:
            kd = decide({}, "TRANSITION", "RISK_ON", 0.9, conf, CONFIG)
            assert kd.authority == AuthorityLevel.HARD_VETO

    def test_transition_risk_budget_zero(self) -> None:
        kd = decide({}, "TRANSITION", "TRANSITION", 0.5, 0.5, CONFIG)
        assert risk_budget_for_kernel(kd) == 0.0

    def test_transition_reason_contains_hard_veto(self) -> None:
        kd = decide({}, "TRANSITION", "TRANSITION", 0.5, 0.5, CONFIG)
        assert "HARD VETO" in kd.veto_reason

    def test_transition_not_affected_by_high_confidence(self) -> None:
        kd = decide({}, "TRANSITION", "RISK_ON", 0.95, 0.95, CONFIG)
        assert kd.decision.action == DecisionAction.RISK_REDUCE
        assert kd.authority == AuthorityLevel.HARD_VETO
