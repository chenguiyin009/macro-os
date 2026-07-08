"""Macro OS v4.4 - Immutable MacroState dataclass.

FROZEN: dataclass(frozen=True). Kernel reads, NEVER writes.
All field updates produce a new instance.
"""

from __future__ import annotations

from dataclasses import dataclass

from .macro_types import ConfirmationStatus, Quadrant


@dataclass(frozen=True)
class MacroState:
    """Immutable world model state.

    Quadrant from TIPS x DXY plane.
    Confirmation from Gold / Credit alignment.
    macro_confidence from coherence of all macro axes.
    """
    quadrant: Quadrant
    tips_trend: float
    dxy_trend: float
    gold_trend: float
    credit_trend: float
    confirmation_status: ConfirmationStatus
    macro_confidence: float
    credit_confidence: float = 1.0

    @classmethod
    def empty(cls) -> MacroState:
        return cls(
            quadrant=Quadrant.TRANSITION,
            tips_trend=0.0,
            dxy_trend=0.0,
            gold_trend=0.0,
            credit_trend=0.0,
            confirmation_status=ConfirmationStatus.NEUTRAL,
            macro_confidence=0.0,
        )
