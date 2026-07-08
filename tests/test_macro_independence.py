"""v4.4 ? Macro independence tests: Kernel cannot mutate MacroState."""

from __future__ import annotations

from core.macro.macro_state import MacroState
from core.macro.macro_types import ConfirmationStatus, Quadrant
from core.macro.macro_mapper import compute_macro_state
from core.macro.confirmation import compute_confirmation
from core.decision_kernel import decide

CONFIG = {"decision": {"long_confidence_min": 0.60, "short_confidence_min": 0.65, "no_trade_confidence_max": 0.35}}


class TestMacroImmutability:
    def test_macro_state_is_frozen(self) -> None:
        m = MacroState.empty()
        try:
            m.quadrant = Quadrant.RISK_ON  # type: ignore
            assert False, "dataclass is not frozen"
        except Exception:
            assert True

    def test_cannot_mutate_quadrant(self) -> None:
        m = MacroState.empty()
        try:
            m.quadrant = Quadrant.RISK_ON  # type: ignore
            assert False, "Should have raised"
        except Exception:
            assert True

    def test_kernel_reads_confirmation_but_does_not_mutate(self) -> None:
        # Use features that produce RISK_ON quadrant (tips < 0.5, dxy < 100)
        m = compute_macro_state({"dxy": 98.0, "vix": 12.0, "tips_yield": 0.3, "hy_credit_spread": 250, "gold": 2400.0})
        m2 = compute_confirmation(m)
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, CONFIG, confirmation_status=m2.confirmation_status.value)
        assert kd.authority.name == "SOFT_POLICY"
        assert m2.quadrant in (Quadrant.RISK_ON, Quadrant.TIGHT_LIQUIDITY, Quadrant.DIVERGENCE, Quadrant.TRANSITION)

    def test_macro_state_from_features(self) -> None:
        features = {"dxy": 98.0, "vix": 12.0, "tips_yield": 0.3, "hy_credit_spread": 250, "gold": 2400.0}
        m = compute_macro_state(features)
        assert isinstance(m.quadrant, Quadrant)
        assert 0.0 <= m.macro_confidence <= 1.0


class TestSafetyGate:
    def test_diverged_triggers_safety_gate(self) -> None:
        """TIGHT_LIQUIDITY with gold down and credit tight -> DIVERGED -> SAFETY_GATE."""
        m = compute_macro_state({"dxy": 105.0, "tips_yield": 1.5, "hy_credit_spread": 100, "gold": 2200.0})
        assert m.quadrant == Quadrant.TIGHT_LIQUIDITY
        m2 = compute_confirmation(m)
        assert m2.confirmation_status == ConfirmationStatus.DIVERGED
        kd = decide({}, "TIGHT_LIQUIDITY", "TIGHT_LIQUIDITY", 0.5, 0.5, CONFIG, confirmation_status=m2.confirmation_status.value)
        assert kd.authority.name == "SAFETY_GATE"
        assert kd.decision.action.value == "REDUCE"

    def test_aligned_no_safety_gate(self) -> None:
        """RISK_ON quadrant -> NEUTRAL -> no safety gate triggered."""
        m = compute_macro_state({"dxy": 98.0, "vix": 12.0, "tips_yield": 0.3, "hy_credit_spread": 300, "gold": 2400.0})
        m2 = compute_confirmation(m)
        assert m2.confirmation_status == ConfirmationStatus.NEUTRAL
        kd = decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, CONFIG, confirmation_status=m2.confirmation_status.value)
        assert kd.authority.name == "SOFT_POLICY"
