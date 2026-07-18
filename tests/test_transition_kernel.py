"""v5.0 — Transition kernel graduated budget tests.

v5.0 constitutional correction: TRANSITION is no longer HARD_VETO with 0.0 budget.
It now gets SAFETY_GATE with 0.50 base budget (graduated mapping).
Only LIQUIDITY_SQUEEZE and CASH_LIQUIDATION remain HARD_VETO.
"""

from __future__ import annotations

from core.decision_kernel import decide, risk_budget_for_kernel
from core.schemas import AuthorityLevel, DecisionAction, RegimeType

CONFIG = {"decision": {
    "long_confidence_min": 0.60, "short_confidence_min": 0.65,
    "no_trade_confidence_max": 0.35, "reduce_threshold": 0.30,
}}


class TestTransitionGraduated:
    def test_transition_always_risk_reduce(self) -> None:
        for _ in range(100):
            kd = decide({}, "TRANSITION", "TRANSITION", 0.7, 0.7, CONFIG)
            assert kd.decision.action == DecisionAction.RISK_REDUCE

    def test_transition_is_safety_gate(self) -> None:
        for conf in [x * 0.1 for x in range(11)]:
            kd = decide({}, "TRANSITION", "RISK_ON", 0.9, conf, CONFIG)
            assert kd.authority == AuthorityLevel.SAFETY_GATE

    def test_transition_risk_budget_nonzero(self) -> None:
        kd = decide({}, "TRANSITION", "TRANSITION", 0.5, 0.5, CONFIG,
                    previous_risk_budget=0.5)
        budget = risk_budget_for_kernel(kd)
        assert budget > 0.0
        assert budget <= 0.50

    def test_transition_reason_contains_safety_gate(self) -> None:
        kd = decide({}, "TRANSITION", "TRANSITION", 0.5, 0.5, CONFIG)
        assert "SAFETY GATE" in kd.veto_reason or "GRADUATED" in kd.reason_code

    def test_transition_not_affected_by_high_confidence(self) -> None:
        kd = decide({}, "TRANSITION", "RISK_ON", 0.95, 0.95, CONFIG)
        assert kd.decision.action == DecisionAction.RISK_REDUCE
        assert kd.authority == AuthorityLevel.SAFETY_GATE

    def test_transition_velocity_limit_capped(self) -> None:
        """From 0.0 previous budget, transition can only ramp +0.10/day."""
        kd = decide({}, "TRANSITION", "TRANSITION", 0.5, 0.5, CONFIG,
                    previous_risk_budget=0.0)
        assert kd.risk_budget == 0.10  # 0.0 + MAX_DAILY_RISK_LIFT(0.10)
        assert kd.reason_code == "GLOBAL_RAMP_ACTIVE"
