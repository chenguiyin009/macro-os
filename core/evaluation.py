"""Macro OS — Replay evaluation metrics.

Computes:
- Regime Confusion Matrix
- Transition Accuracy
- Cost-adjusted Sharpe Ratio
- Stability Score
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.schemas import RegimeType


class ConfusionMatrix:
    """Regime confusion matrix builder and formatter."""

    REGIME_ORDER = [
        RegimeType.RISK_ON.value,
        RegimeType.TIGHT_LIQUIDITY.value,
        RegimeType.LIQUIDITY_SQUEEZE.value,
        RegimeType.TRANSITION.value,
    ]

    def __init__(self) -> None:
        self.labels = self.REGIME_ORDER
        self.matrix: Dict[str, Dict[str, int]] = {
            a: {b: 0 for b in self.labels} for a in self.labels
        }
        self.total = 0

    def add(self, actual: str, predicted: str) -> None:
        """Record one prediction."""
        if actual not in self.matrix:
            self.matrix[actual] = {b: 0 for b in self.labels}
        if predicted not in self.matrix[actual]:
            self.matrix[actual][predicted] = 0
        self.matrix[actual][predicted] += 1
        self.total += 1

    def accuracy(self) -> float:
        """Overall accuracy (diagonal / total)."""
        if self.total == 0:
            return 0.0
        correct = sum(self.matrix[a][a] for a in self.labels if a in self.matrix)
        return correct / self.total

    def to_table(self) -> str:
        """Render confusion matrix as ASCII table."""
        header = f"{'':>18}" + "".join(f"{l:>16}" for l in self.labels)
        lines = [header, "-" * (18 + 16 * len(self.labels))]
        for actual in self.labels:
            row_data = [str(self.matrix.get(actual, {}).get(p, 0)) for p in self.labels]
            pcts = []
            row_total = sum(self.matrix.get(actual, {}).values())
            if row_total > 0:
                pcts = [
                    f"{self.matrix.get(actual, {}).get(p, 0) / row_total * 100:.0f}%"
                    for p in self.labels
                ]
            else:
                pcts = ["0%" for _ in self.labels]
            row = f"{actual:>18}" + "".join(f"{p:>16}" for p in pcts)
            lines.append(row)
        lines.append(f"\nOverall Accuracy: {self.accuracy() * 100:.1f}%")
        return "\n".join(lines)

    def to_heatmap(self, path: Path) -> None:
        """Render confusion matrix as PNG heatmap using matplotlib."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        n = len(self.labels)
        data = np.zeros((n, n))
        for i, a in enumerate(self.labels):
            for j, p in enumerate(self.labels):
                data[i, j] = self.matrix.get(a, {}).get(p, 0)

        # Convert to row percentages
        row_sums = data.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums == 0, 1, row_sums)
        pct = data / row_sums * 100

        fig, ax = plt.subplots(figsize=(10, 8))
        im = ax.imshow(pct, cmap="Blues", vmin=0, vmax=100)

        ax.set_xticks(range(n))
        ax.set_yticks(range(n))
        ax.set_xticklabels(self.labels, rotation=45, ha="right")
        ax.set_yticklabels(self.labels)
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Actual")

        for i in range(n):
            for j in range(n):
                val = f"{pct[i, j]:.0f}%\n({int(data[i, j])})"
                ax.text(j, i, val, ha="center", va="center", fontsize=9)

        fig.colorbar(im, ax=ax, label="%")
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close(fig)


def transition_accuracy(
    actual_sequence: List[str], predicted_sequence: List[str]
) -> float:
    """Fraction of regime transitions correctly detected.

    A transition is correctly detected when regime changes in both
    actual and predicted at the same point.
    """
    if len(actual_sequence) < 2 or len(predicted_sequence) < 2:
        return 0.0

    actual_changes = [
        i for i in range(1, len(actual_sequence))
        if actual_sequence[i] != actual_sequence[i - 1]
    ]
    predicted_changes = [
        i for i in range(1, len(predicted_sequence))
        if predicted_sequence[i] != predicted_sequence[i - 1]
    ]

    if not actual_changes:
        return 1.0 if not predicted_changes else 0.0

    # Count transitions predicted within 1 period of actual
    change_set = set(actual_changes)
    correct = sum(1 for p in predicted_changes if p in change_set)
    return correct / len(actual_changes)


def stability_score(
    predicted_sequence: List[str],
    total_periods: int,
) -> float:
    """Stability = 1 - (regime_switch_frequency / max_switch_frequency).

    Penalizes excessive regime switching.
    """
    if len(predicted_sequence) < 2:
        return 1.0

    switches = sum(
        1 for i in range(1, len(predicted_sequence))
        if predicted_sequence[i] != predicted_sequence[i - 1]
    )

    # Max reasonable switches: one per period
    max_switches = len(predicted_sequence) - 1
    if max_switches == 0:
        return 1.0

    frequency_penalty = switches / max_switches
    return max(0.0, 1.0 - frequency_penalty)


class ReplayEvaluation:
    """Compute all replay evaluation metrics."""

    def __init__(
        self,
        pnl_result: Dict[str, Any],
        actual_regimes: List[str],
        predicted_regimes: List[str],
    ) -> None:
        self.pnl = pnl_result
        self.actual = actual_regimes
        self.predicted = predicted_regimes
        self.cm = ConfusionMatrix()

        for a, p in zip(actual_regimes, predicted_regimes):
            self.cm.add(a, p)

    def metrics(self) -> Dict[str, Any]:
        """Compute all evaluation metrics."""
        return {
            "confusion_matrix": {
                "accuracy": round(self.cm.accuracy(), 4),
                "table": self.cm.to_table(),
                "total_samples": self.cm.total,
            },
            "transition_accuracy": round(
                transition_accuracy(self.actual, self.predicted), 4
            ),
            "stability_score": round(
                stability_score(self.predicted, len(self.predicted)), 4
            ),
            "pnl": {
                "gross_pnl": self.pnl.get("gross_pnl", 0.0),
                "net_pnl": self.pnl.get("net_pnl", 0.0),
                "sharpe": self.pnl.get("sharpe", 0.0),
                "total_costs_bps": self.pnl.get("total_costs_bps", 0.0),
                "trade_count": self.pnl.get("trade_count", 0),
            },
        }

    def save_heatmap(self, path: Path) -> None:
        """Save confusion matrix heatmap to PNG."""
        self.cm.to_heatmap(path)
