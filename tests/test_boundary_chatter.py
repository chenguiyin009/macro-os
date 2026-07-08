"""v4.3.1 ? Boundary oscillation stability tests."""

from __future__ import annotations

from core.decision_kernel import decide
from core.schemas import AuthorityLevel, DecisionAction, RegimeType

CONFIG = {"decision": {
    "long_confidence_min": 0.60, "short_confidence_min": 0.65,
    "no_trade_confidence_max": 0.35, "reduce_threshold": 0.30,
}}


class TestBoundaryOscillation:
    def test_rto_transition_oscillation(self) -> None:
        regimes = ["RISK_ON", "TRANSITION"] * 50
        actions = []
        for r in regimes:
            kd = decide({}, r, r, 0.7, 0.7, CONFIG)
            actions.append(kd.decision.action)
        for i, a in enumerate(actions):
            if regimes[i] == "TRANSITION":
                assert a == DecisionAction.RISK_REDUCE

    def test_transition_tight_oscillation(self) -> None:
        regimes = ["TRANSITION", "TIGHT_LIQUIDITY"] * 50
        for r in regimes:
            kd = decide({}, r, r, 0.5, 0.5, CONFIG)
            assert kd.authority == AuthorityLevel.HARD_VETO

    def test_all_veto_regimes_have_zero_budget(self) -> None:
        for regime in ["TRANSITION", "TIGHT_LIQUIDITY", "LIQUIDITY_SQUEEZE"]:
            kd = decide({}, regime, regime, 0.5, 0.5, CONFIG)
            assert kd.risk_budget == 0.0

    def test_risk_on_allows_long_high_confidence(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.8, 0.8, CONFIG)
        assert kd.authority == AuthorityLevel.SOFT_POLICY

    def test_risk_on_budget_non_zero(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.8, 0.8, CONFIG)
        assert kd.risk_budget > 0.0

    def test_regime_switch_no_transition_to_no_trade_jitter(self) -> None:
        regimes = ["RISK_ON", "TRANSITION", "RISK_ON", "TRANSITION"]
        for r in regimes:
            kd = decide({}, r, r, 0.7, 0.7, CONFIG)
            if r == "TRANSITION":
                assert kd.decision.action == DecisionAction.RISK_REDUCE
            else:
                assert kd.authority == AuthorityLevel.SOFT_POLICY
