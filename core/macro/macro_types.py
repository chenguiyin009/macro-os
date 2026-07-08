"""Macro OS v4.4 - Macro type definitions."""

from enum import Enum


class Quadrant(str, Enum):
    RISK_ON = "RISK_ON"
    TIGHT_LIQUIDITY = "TIGHT_LIQUIDITY"
    DIVERGENCE = "DIVERGENCE"
    TRANSITION = "TRANSITION"

    def __str__(self) -> str:
        return self.value


class ConfirmationStatus(str, Enum):
    ALIGNED = "ALIGNED"
    DIVERGED = "DIVERGED"
    NEUTRAL = "NEUTRAL"

    def __str__(self) -> str:
        return self.value
