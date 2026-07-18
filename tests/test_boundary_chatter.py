"""v5.0 — Boundary oscillation stability tests.

Updated for graduated budget mapping:
- TRANSITION → SAFETY_GATE with 0.50 budget (not HARD_VETO/0.0)
- TIGHT_LIQUIDITY → SAFETY_GATE with 0.30 budget (not HARD_VETO/0.0)
- LIQUIDITY_SQUEEZE → HARD_VETO with 0.0 budget (unchanged)
"""

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
            kd = decide({}, r, r, 0.7, 0.7, CONFIG, previous_risk_budget=0.5)
            actions.append(kd.decision.action)
        for i, a in enumerate(actions):
            if regimes[i] == "TRANSITION":
                assert a == DecisionAction.RISK_REDUCE

    def test_transition_tight_oscillation(self) -> None:
        """Both TRANSITION and TIGHT_LIQUIDITY are SAFETY_GATE (v5.0 graduated)."""
        regimes = ["TRANSITION", "TIGHT_LIQUIDITY"] * 50
        for r in regimes:
            kd = decide({}, r, r, 0.5, 0.5, CONFIG, previous_risk_budget=0.3)
            assert kd.authority == AuthorityLevel.SAFETY_GATE

    def test_squeeze_has_zero_budget(self) -> None:
        """A genuine core-crisis SQUEEZE (very high VIX) still gets a hard 0.0 budget.
        (P0-2 v5.1: an EASING squeeze releases a graduated >0 budget instead.)"""
        kd = decide({"vix": 45.0}, "LIQUIDITY_SQUEEZE", "LIQUIDITY_SQUEEZE", 0.5, 0.5, CONFIG)
        assert kd.risk_budget == 0.0
        assert kd.authority == AuthorityLevel.HARD_VETO

    def test_cash_liquidation_stays_zero(self) -> None:
        """CASH_LIQUIDATION is never relaxed by the crisis gradient — always 0.0."""
        kd = decide({"vix": 30.0, "hy_credit_spread": 400.0},
                    "CASH_LIQUIDATION", "CASH_LIQUIDATION", 0.5, 0.5, CONFIG)
        assert kd.risk_budget == 0.0
        assert kd.authority == AuthorityLevel.HARD_VETO

    def test_graduated_regimes_have_nonzero_budget(self) -> None:
        """TRANSITION and TIGHT_LIQUIDITY get non-zero budgets."""
        for regime in ["TRANSITION", "TIGHT_LIQUIDITY"]:
            kd = decide({}, regime, regime, 0.5, 0.5, CONFIG,
                        previous_risk_budget=0.5)
            assert kd.risk_budget > 0.0
            assert kd.authority == AuthorityLevel.SAFETY_GATE

    def test_risk_on_allows_long_high_confidence(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.8, 0.8, CONFIG)
        assert kd.authority == AuthorityLevel.SOFT_POLICY

    def test_risk_on_budget_non_zero(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.8, 0.8, CONFIG)
        assert kd.risk_budget > 0.0

    def test_regime_switch_no_transition_to_no_trade_jitter(self) -> None:
        regimes = ["RISK_ON", "TRANSITION", "RISK_ON", "TRANSITION"]
        for r in regimes:
            kd = decide({}, r, r, 0.7, 0.7, CONFIG, previous_risk_budget=0.5)
            if r == "TRANSITION":
                assert kd.decision.action == DecisionAction.RISK_REDUCE
            else:
                assert kd.authority == AuthorityLevel.SOFT_POLICY
