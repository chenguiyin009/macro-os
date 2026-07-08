"""v4.5 - Divergence dynamics tests."""

from __future__ import annotations

from core.divergence.divergence_score import compute_divergence_score
from core.divergence.divergence_phase import map_phase, DivergenceState, compute_confidence
from core.divergence.liquidity_fracture import classify_fracture
from core.divergence.risk_surface import risk_budget_from_phase, safety_gate_response
from core.divergence.divergence_engine import DivergencePhaseEngine
from core.macro.macro_state import MacroState
from core.macro.macro_types import ConfirmationStatus, Quadrant
from core.decision_kernel import decide
from core.schemas import AuthorityLevel, DecisionAction


# Helper: create a mock MacroState
def _ms(quadrant=Quadrant.TRANSITION, tips=0.0, dxy=0.0, gold=0.0, credit=0.0,
        conf=ConfirmationStatus.NEUTRAL, mconf=0.5):
    return MacroState(quadrant=quadrant, tips_trend=tips, dxy_trend=dxy,
                      gold_trend=gold, credit_trend=credit,
                      confirmation_status=conf, macro_confidence=mconf)


class TestDivergenceScore:
    def test_no_divergence(self) -> None:
        m = _ms(tips=0.1, dxy=0.0, gold=0.0, credit=0.0)
        score = compute_divergence_score(m, {"vix": 15})
        assert 0.0 <= score < 0.2

    def test_rate_credit_conflict(self) -> None:
        m = _ms(tips=0.5, dxy=0.0, gold=0.0, credit=-0.1)
        score = compute_divergence_score(m, {"vix": 15})
        assert score >= 0.3

    def test_gold_credit_decoupling(self) -> None:
        m = _ms(tips=0.0, dxy=0.0, gold=0.1, credit=-0.1)
        score = compute_divergence_score(m, {"vix": 15})
        assert score >= 0.25

    def test_dollar_mismatch(self) -> None:
        m = _ms(tips=0.0, dxy=0.5, gold=0.1, credit=0.0)
        score = compute_divergence_score(m, {"vix": 15})
        assert score >= 0.15

    def test_vix_filter_under_15_reduces_score(self) -> None:
        m = _ms(tips=0.5, dxy=0.5, gold=0.1, credit=-0.1)
        score_normal = compute_divergence_score(m, {"vix": 20})
        score_filtered = compute_divergence_score(m, {"vix": 12})
        assert score_filtered <= score_normal

    def test_score_capped_at_one(self) -> None:
        m = _ms(tips=1.0, dxy=1.0, gold=0.5, credit=-0.5)
        score = compute_divergence_score(m, {"vix": 30})
        assert score <= 1.0

    def test_phase_engine_clamps_resonance_score_to_one(self) -> None:
        engine = DivergencePhaseEngine(use_pine_data=False)
        state = engine.compute_state(
            {
                "tips_yield": 3.0,
                "dxy": 130.0,
                "gold": 4000.0,
                "hy_credit_spread": 100.0,
            },
            vix=30.0,
        )
        assert 0.0 <= state.score <= 1.0


class TestPhaseMapping:
    def test_none_below_02(self) -> None:
        assert map_phase(0.1) == "NONE"
        assert map_phase(0.19) == "NONE"

    def test_early_between_02_and_04(self) -> None:
        assert map_phase(0.2) == "EARLY"
        assert map_phase(0.3) == "EARLY"

    def test_mid_between_04_and_06(self) -> None:
        assert map_phase(0.4) == "MID"
        assert map_phase(0.5) == "MID"

    def test_late_between_06_and_085(self) -> None:
        assert map_phase(0.6) == "LATE"
        assert map_phase(0.8) == "LATE"

    def test_crisis_at_085_and_above(self) -> None:
        assert map_phase(0.85) == "CRISIS"
        assert map_phase(1.0) == "CRISIS"


class TestRiskSurface:
    def test_crisis_budget_zero(self) -> None:
        assert risk_budget_from_phase("CRISIS") == 0.0

    def test_late_budget_half(self) -> None:
        assert risk_budget_from_phase("LATE") == 0.5

    def test_mid_budget_075(self) -> None:
        assert risk_budget_from_phase("MID") == 0.75

    def test_early_budget_full(self) -> None:
        assert risk_budget_from_phase("EARLY") == 1.0

    def test_none_budget_default(self) -> None:
        assert risk_budget_from_phase("NONE") == 1.0

    def test_safety_gate_returns_none_for_none(self) -> None:
        assert safety_gate_response("NONE") is None


class TestKernelSafetyGateGradient:
    CONFIG = {"decision": {"long_confidence_min": 0.60, "short_confidence_min": 0.65, "no_trade_confidence_max": 0.35}}

    def test_crisis_risk_reduce_budget_zero(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="CRISIS")
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.decision.action == DecisionAction.RISK_REDUCE
        assert kd.risk_budget == 0.0

    def test_late_reduce_budget_half(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="LATE")
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.decision.action == DecisionAction.REDUCE
        assert kd.risk_budget == 0.5

    def test_mid_reduce_budget_075(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="MID")
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.decision.action == DecisionAction.REDUCE
        assert kd.risk_budget == 0.75

    def test_early_neutral_budget_full(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="EARLY", previous_risk_budget=1.0)
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.decision.action == DecisionAction.NEUTRAL
        assert kd.risk_budget == 1.0

    def test_none_no_safety_gate(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="NONE")
        assert kd.authority != AuthorityLevel.SAFETY_GATE

    def test_legacy_diverged_mapped_to_mid(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, confirmation_status="DIVERGED")
        assert kd.authority == AuthorityLevel.SAFETY_GATE
        assert kd.risk_budget == 0.75

    def test_legacy_empty_no_effect(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, confirmation_status="")
        assert kd.authority != AuthorityLevel.SAFETY_GATE


# Fix 1: Credit Confidence scaling
class TestCreditConfidence:
    def test_low_confidence_reduces_credit_weight(self) -> None:
        from core.divergence.divergence_score import compute_divergence_score
        from core.macro.macro_state import MacroState
        from core.macro.macro_types import ConfirmationStatus, Quadrant
        # Same TIPS > 0, credit < 0 but with credit_confidence=0.3
        m = MacroState(tips_trend=0.5, dxy_trend=0.0, gold_trend=0.0, credit_trend=-0.1,
                        quadrant=Quadrant.TIGHT_LIQUIDITY, confirmation_status=ConfirmationStatus.NEUTRAL,
                        macro_confidence=0.5, credit_confidence=0.3)
        score_low = compute_divergence_score(m, {"vix": 20})
        m2 = MacroState(tips_trend=0.5, dxy_trend=0.0, gold_trend=0.0, credit_trend=-0.1,
                         quadrant=Quadrant.TIGHT_LIQUIDITY, confirmation_status=ConfirmationStatus.NEUTRAL,
                         macro_confidence=0.5, credit_confidence=1.0)
        score_high = compute_divergence_score(m2, {"vix": 20})
        assert score_low < score_high

    def test_macro_state_default_confidence(self) -> None:
        from core.macro.macro_state import MacroState
        from core.macro.macro_types import ConfirmationStatus, Quadrant
        m = MacroState(tips_trend=0.0, dxy_trend=0.0, gold_trend=0.0, credit_trend=0.0,
                        quadrant=Quadrant.TRANSITION, confirmation_status=ConfirmationStatus.NEUTRAL,
                        macro_confidence=0.5)
        assert m.credit_confidence == 1.0

# Fix 2: Vol-Adjusted Threshold
class TestVolAdjustedThreshold:
    def test_crisis_threshold_raises_in_low_vol(self) -> None:
        from core.divergence.divergence_phase import compute_adjusted_crisis_threshold
        assert compute_adjusted_crisis_threshold(14.0) > 0.90
        assert compute_adjusted_crisis_threshold(20.0) == 0.85

    def test_map_phase_adjusted_low_vol(self) -> None:
        from core.divergence.divergence_phase import map_phase_adjusted
        # 0.85 is normally CRISIS but with VIX<16 it should be LATE
        assert map_phase_adjusted(0.85, 14.0) != "CRISIS"
        # 0.93 is above the adjusted threshold
        assert map_phase_adjusted(0.93, 14.0) == "CRISIS"
        ######################################
        assert map_phase_adjusted(0.85, 20.0) == "CRISIS"

# Fix 3: Recovery Protocol
class TestRecoveryProtocol:

    CONFIG = {"decision": {"long_confidence_min": 0.60}}

    def test_late_recovery_before_day_three_stays_locked(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG,
                    divergence_phase="LATE", recovery_active=True,
                    days_in_recovery=2, previous_risk_budget=0.4,
                    proposed_risk=0.8)
        assert kd.risk_budget == 0.4
        assert kd.reason_code == "RECOVERY_TIME_LOCK_ACTIVE"

    def test_mid_recovery_after_day_three_respects_recovery_ramp(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG,
                    divergence_phase="MID", recovery_active=True,
                    days_in_recovery=3, previous_risk_budget=0.6,
                    proposed_risk=0.9)
        assert kd.risk_budget == 0.7
        assert kd.reason_code == "RECOVERY_RAMP_ACTIVE"
    CONFIG = {"decision": {"long_confidence_min": 0.60}}

    def test_recovery_active_increases_late_budget(self) -> None:
        kd_normal = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="LATE")
        kd_recovery = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="LATE", recovery_active=True, days_in_recovery=3, previous_risk_budget=0.60)
        assert kd_recovery.risk_budget > kd_normal.risk_budget

    def test_recovery_active_increases_mid_budget(self) -> None:
        kd_normal = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="MID")
        kd_recovery = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="MID", recovery_active=True, days_in_recovery=3, previous_risk_budget=0.70)
        assert kd_recovery.risk_budget > kd_normal.risk_budget

    def test_crisis_ignores_recovery(self) -> None:
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, self.CONFIG, divergence_phase="CRISIS", recovery_active=True)
        assert kd.risk_budget == 0.0
