"""Macro OS v4.1 — Bounded feedback learning system.

Safe adaptive weight updates with:
- Hard bounds (clip to [min, max])
- Soft damping (exponential moving average towards prior)
- Per-step delta cap
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def update_weight(
    w: float,
    delta: float,
    learning_rate: float = 0.1,
    min_weight: float = 0.2,
    max_weight: float = 1.8,
    soft_damping: float = 0.9,
    max_delta: float = 0.5,
) -> float:
    """Update a weight with bounded plasticity.

    Applies: hard clip(w + lr * delta) then soft-damp towards prior.

    Args:
        w: Current weight value.
        delta: Error signal (positive = increase, negative = decrease).
        learning_rate: Step size multiplier.
        min_weight: Absolute lower bound.
        max_weight: Absolute upper bound.
        soft_damping: EMA coefficient (0.0 = no damping, 1.0 = full freeze).
        max_delta: Maximum single-step change.

    Returns:
        Updated weight within bounds.
    """
    # Cap per-step delta
    step = delta * learning_rate
    step = max(-max_delta, min(max_delta, step))

    # Hard clip
    new_w = w + step
    new_w = max(min_weight, min(max_weight, new_w))

    # Soft damping (prevents runaway drift)
    new_w = soft_damping * new_w + (1.0 - soft_damping) * w

    return new_w


class FeedbackController:
    """Manages bounded weight updates for scoring components.

    Tracks each weight's history for monitoring.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.learning_rate = cfg.get("learning_rate", 0.1)
        self.min_weight = cfg.get("min_weight", 0.2)
        self.max_weight = cfg.get("max_weight", 1.8)
        self.soft_damping = cfg.get("soft_damping", 0.9)
        self.max_delta = cfg.get("max_delta", 0.5)

        self._weights: Dict[str, float] = {}
        self._history: Dict[str, List[float]] = {}

    def register(self, name: str, initial: float = 1.0) -> None:
        """Register a weight for tracking."""
        w = max(self.min_weight, min(self.max_weight, initial))
        self._weights[name] = w
        self._history[name] = [w]

    def get(self, name: str) -> float:
        """Get current weight value."""
        return self._weights.get(name, 1.0)

    def update(self, name: str, delta: float) -> float:
        """Apply bounded update to a named weight.

        Args:
            name: Weight identifier.
            delta: Error signal.

        Returns:
            Updated weight value.
        """
        if name not in self._weights:
            self.register(name)
        w = self._weights[name]
        new_w = update_weight(
            w, delta,
            learning_rate=self.learning_rate,
            min_weight=self.min_weight,
            max_weight=self.max_weight,
            soft_damping=self.soft_damping,
            max_delta=self.max_delta,
        )
        self._weights[name] = new_w
        self._history[name].append(new_w)
        return new_w

    def weights(self) -> Dict[str, float]:
        """Snapshot of all current weights."""
        return dict(self._weights)

    def reset(self, name: Optional[str] = None) -> None:
        """Reset weight(s) to midpoint."""
        mid = (self.min_weight + self.max_weight) / 2.0
        if name:
            self._weights[name] = mid
        else:
            for k in self._weights:
                self._weights[k] = mid

    def freeze(self) -> None:
        """Set soft damping to 1.0 (full freeze)."""
        self.soft_damping = 1.0

    def thaw(self) -> None:
        """Restore default soft damping."""
        self.soft_damping = 0.9

    @property
    def params(self) -> Dict[str, Any]:
        return {
            "learning_rate": self.learning_rate,
            "min_weight": self.min_weight,
            "max_weight": self.max_weight,
            "soft_damping": self.soft_damping,
            "max_delta": self.max_delta,
        }
