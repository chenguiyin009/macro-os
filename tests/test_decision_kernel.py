"""Tests for decision kernel (v4.3)."""

from __future__ import annotations

from core.decision_kernel import decide, risk_budget_for_kernel
from core.schemas import AuthorityLevel, DecisionAction, KernelDecision, RegimeType

CONFIG = {"decision": {"long_confidence_min": 0.60, "short_confidence_min": 0.65, "no_trade_confidence_max": 0.35, "reduce_threshold": 0.30}}

class TestKernelV5:
    def test_kernel_decision_v5_fields_default(self) -> None:
        kd = KernelDecision()
        assert kd.defense_budget == 0.5
        assert kd.reason_code == ""
        assert list(kd.audit_trail.keys()) == []

    def test_crisis_to_early_is_capped_by_global_velocity_limit(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.8, 0.8, CONFIG,
                    divergence_phase="EARLY", proposed_risk=0.8,
                    previous_risk_budget=0.0)
        assert kd.risk_budget == 0.10
        assert kd.defense_budget == 0.90
        assert kd.reason_code == "GLOBAL_RAMP_ACTIVE"
        assert kd.audit_trail["step_4_global_velocity_limit"]["status"] == "TRIGGERED"

    def test_audit_trail_contains_four_ordered_steps(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.8, 0.8, CONFIG,
                    divergence_phase="EARLY", proposed_risk=0.8,
                    previous_risk_budget=0.0)
        assert list(kd.audit_trail.keys()) == [
            "step_1_safety_gate",
            "step_2_hard_veto",
            "step_3_soft_policy",
            "step_4_global_velocity_limit",
        ]


class TestVeto:
    def test_tight_liquidity_graduated_budget(self) -> None:
        """TIGHT_LIQUIDITY gets SAFETY_GATE with 0.30 base budget (v5.0 graduated)."""
        kd = decide(
            {"dxy": 105.0},
            "TIGHT_LIQUIDITY",
            "RISK_ON",
            0.8,
            0.8,
            CONFIG,
            proposed_risk=0.8,
            previous_risk_budget=0.4,
        )
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.risk_budget > 0.0
        assert kd.risk_budget <= 0.30
        assert kd.reason_code == "GRADUATED_TIGHT"

    def test_hard_veto_for_squeeze(self) -> None:
        # Core crisis (very high VIX) still gets a hard 0.0 budget.
        kd = decide({"vix": 45.0}, "LIQUIDITY_SQUEEZE", "LIQUIDITY_SQUEEZE", 0.5, 0.5, CONFIG)
        assert kd.authority == AuthorityLevel.HARD_VETO
        assert kd.decision.action == DecisionAction.REDUCE
        assert kd.risk_budget == 0.0

    def test_crisis_graduated_release_for_squeeze(self) -> None:
        # P0-2 (v5.1): an EASING SQUEEZE (VIX/HY falling within the envelope) gets a
        # graduated, non-zero budget instead of a hard zero. Authority stays HARD_VETO.
        kd = decide({"vix": 30.0, "hy_credit_spread": 400.0},
                    "LIQUIDITY_SQUEEZE", "LIQUIDITY_SQUEEZE", 0.5, 0.5, CONFIG)
        assert kd.authority == AuthorityLevel.HARD_VETO
        assert kd.risk_budget > 0.0
        # VIX=30, HY=400 satisfies the loosest graduated band (VIX<=35, HY<=420) -> 0.05
        assert kd.risk_budget == 0.05

    def test_tight_liquidity_action_is_reduce(self) -> None:
        kd = decide({"dxy": 105.0}, "TIGHT_LIQUIDITY", "TIGHT_LIQUIDITY", 0.4, 0.5, CONFIG)
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.decision.action == DecisionAction.REDUCE
        assert kd.risk_budget > 0.0

    def test_soft_policy_for_risk_on(self) -> None:
        kd = decide({"dxy": 96.0}, "RISK_ON", "RISK_ON", 0.7, 0.75, CONFIG)
        assert kd.authority == AuthorityLevel.SOFT_POLICY
        assert kd.decision.action == DecisionAction.AGGRESSIVE

    def test_transition_graduated_budget(self) -> None:
        """TRANSITION gets SAFETY_GATE with 0.50 base budget (v5.0 graduated)."""
        kd = decide({"dxy": 101.0}, "TRANSITION", "RISK_ON", 0.5, 0.5, CONFIG)
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.decision.action == DecisionAction.RISK_REDUCE
        assert kd.risk_budget > 0.0
        assert kd.reason_code == "GRADUATED_TRANSITION"

    def test_squeeze_override_high_confidence(self) -> None:
        kd = decide({"vix": 35.0}, "LIQUIDITY_SQUEEZE", "RISK_ON", 0.9, 0.9, CONFIG)
        assert kd.decision.action == DecisionAction.REDUCE
        assert kd.authority == AuthorityLevel.HARD_VETO

    def test_kernel_never_aggressive_in_tight(self) -> None:
        kd = decide({"dxy": 105.0}, "TIGHT_LIQUIDITY", "TIGHT_LIQUIDITY", 0.8, 0.8, CONFIG)
        assert kd.decision.action != DecisionAction.AGGRESSIVE

    def test_hard_veto_emits_uniform_four_step_audit_trail(self) -> None:
        """HARD_VETO (squeeze) must emit same 4-step audit trail keys."""
        kd = decide(
            {"vix": 35.0},
            "LIQUIDITY_SQUEEZE",
            "RISK_ON",
            0.8,
            0.8,
            CONFIG,
            proposed_risk=0.8,
            previous_risk_budget=0.4,
        )
        assert kd.authority == AuthorityLevel.HARD_VETO
        assert list(kd.audit_trail.keys()) == [
            "step_1_safety_gate",
            "step_2_hard_veto",
            "step_3_soft_policy",
            "step_4_global_velocity_limit",
        ]
        assert kd.audit_trail["step_1_safety_gate"]["status"] == "SKIPPED_DUE_TO_VETO"
        assert kd.audit_trail["step_2_hard_veto"]["status"] == "TRIGGERED"
        assert kd.audit_trail["step_2_hard_veto"]["clamped_risk_budget"] == 0.0
        assert kd.audit_trail["step_3_soft_policy"]["status"] == "SKIPPED_DUE_TO_VETO"
        assert kd.audit_trail["step_4_global_velocity_limit"]["status"] == "SKIPPED_DUE_TO_VETO"

