"""Macro OS v4.4 - Alignment confirmation engine.

Checks if Gold and Credit trends confirm the macro quadrant.
Produces DIVERGED / ALIGNED / NEUTRAL status.
"""

from __future__ import annotations

from .macro_state import MacroState
from .macro_types import ConfirmationStatus, Quadrant


def compute_confirmation(m: MacroState) -> MacroState:
    gold_ok = m.gold_trend > 0
    credit_ok = m.credit_trend > 0

    if m.quadrant == Quadrant.TIGHT_LIQUIDITY:
        status = ConfirmationStatus.DIVERGED if (not gold_ok or not credit_ok) else ConfirmationStatus.ALIGNED
    else:
        status = ConfirmationStatus.NEUTRAL

    return MacroState(
        quadrant=m.quadrant,
        tips_trend=m.tips_trend,
        dxy_trend=m.dxy_trend,
        gold_trend=m.gold_trend,
        credit_trend=m.credit_trend,
        confirmation_status=status,
        macro_confidence=m.macro_confidence,
        credit_confidence=m.credit_confidence,
    )
