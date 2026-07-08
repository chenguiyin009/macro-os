from __future__ import annotations

import pytest

from core.shadow_engine import ShadowEngine


def test_shadow_engine_uses_close_prices_for_counterfactual_returns() -> None:
    engine = ShadowEngine()

    engine.update_daily({"qqq_close": 100.0}, {"QQQ": 0.5, "CASH": 0.5})
    engine.update_daily({"qqq_close": 90.0}, {"QQQ": 0.5, "CASH": 0.5})

    report = engine.generate_counterfactual_report()

    assert engine.days_run == 2
    assert engine.portfolios["baseline"].nav == pytest.approx(0.95)
    assert engine.portfolios["aggressive"].max_drawdown == pytest.approx(-0.08)
    assert engine.portfolios["conservative"].max_drawdown == pytest.approx(-0.01)
    assert "Running days: 2" in report
    assert "Baseline (Expected Risk)" in report
    assert "Aggressive (80% QQQ)" in report
    assert "> Avoided **3.00%** extreme DD." in report
