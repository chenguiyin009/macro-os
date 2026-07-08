from __future__ import annotations

from core.portfolio.reconciliation import compute_actionable_diff


def test_compute_actionable_diff_returns_only_material_drift() -> None:
    target = {"QQQ": 0.55, "CASH": 0.45}
    actual = {"QQQ": 0.50, "CASH": 0.50, "AAPL": 0.01}

    diff = compute_actionable_diff(target, actual)

    assert diff == {"QQQ": 0.05, "CASH": -0.05}
