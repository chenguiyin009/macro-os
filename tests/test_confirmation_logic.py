"""v4.4 ? Confirmation logic alignment tests."""

from __future__ import annotations

from core.macro.macro_state import MacroState
from core.macro.macro_types import ConfirmationStatus, Quadrant
from core.macro.macro_mapper import compute_macro_state, compute_quadrant
from core.macro.confirmation import compute_confirmation


class TestQuadrantMapping:
    def test_risk_on_quadrant(self) -> None:
        assert compute_quadrant(-0.3, -2.0) == Quadrant.RISK_ON

    def test_tight_liquidity_quadrant(self) -> None:
        assert compute_quadrant(0.5, 3.0) == Quadrant.TIGHT_LIQUIDITY

    def test_divergence_quadrant(self) -> None:
        assert compute_quadrant(0.5, -2.0) == Quadrant.DIVERGENCE

    def test_transition_quadrant(self) -> None:
        assert compute_quadrant(0.1, -0.1) == Quadrant.TRANSITION


class TestConfirmation:
    def test_tight_with_gold_up_credit_ok_aligned(self) -> None:
        m = MacroState(quadrant=Quadrant.TIGHT_LIQUIDITY, tips_trend=0.5, dxy_trend=3.0,
                        gold_trend=0.02, credit_trend=0.1,
                        confirmation_status=ConfirmationStatus.NEUTRAL, macro_confidence=0.8)
        m2 = compute_confirmation(m)
        assert m2.confirmation_status == ConfirmationStatus.ALIGNED

    def test_tight_with_gold_down_diverged(self) -> None:
        m = MacroState(quadrant=Quadrant.TIGHT_LIQUIDITY, tips_trend=0.5, dxy_trend=3.0,
                        gold_trend=-0.02, credit_trend=0.1,
                        confirmation_status=ConfirmationStatus.NEUTRAL, macro_confidence=0.8)
        m2 = compute_confirmation(m)
        assert m2.confirmation_status == ConfirmationStatus.DIVERGED

    def test_risk_on_always_neutral(self) -> None:
        m = MacroState(quadrant=Quadrant.RISK_ON, tips_trend=-0.3, dxy_trend=-2.0,
                        gold_trend=0.01, credit_trend=-0.05,
                        confirmation_status=ConfirmationStatus.NEUTRAL, macro_confidence=0.7)
        m2 = compute_confirmation(m)
        assert m2.confirmation_status == ConfirmationStatus.NEUTRAL

    def test_tight_with_credit_widening_diverged(self) -> None:
        m = MacroState(quadrant=Quadrant.TIGHT_LIQUIDITY, tips_trend=0.5, dxy_trend=3.0,
                        gold_trend=0.02, credit_trend=-0.1,
                        confirmation_status=ConfirmationStatus.NEUTRAL, macro_confidence=0.8)
        m2 = compute_confirmation(m)
        assert m2.confirmation_status == ConfirmationStatus.DIVERGED


class TestConfirmationProducesNewInstance:
    def test_confirmation_returns_new_instance(self) -> None:
        m = MacroState(quadrant=Quadrant.TIGHT_LIQUIDITY, tips_trend=0.5, dxy_trend=3.0,
                        gold_trend=0.02, credit_trend=-0.1,
                        confirmation_status=ConfirmationStatus.NEUTRAL, macro_confidence=0.8)
        m2 = compute_confirmation(m)
        assert m is not m2
        assert m2.confirmation_status == ConfirmationStatus.DIVERGED
