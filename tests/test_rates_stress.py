"""Unit tests for rates_stress fifth leg."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.rates_stress import (
    RatesStressParams,
    apply_hysteresis,
    compute_rates_stress_series,
    overlap_with_duration_state,
)


def test_hysteresis_confirm_and_exit():
    raw_t = [False, True, True, True, False, False, False]
    raw_c = [1, 0.55, 0.55, 0.55, 1, 1, 1]
    eng, caps = apply_hysteresis(raw_t, raw_c, confirm_days=2, exit_days=3)
    assert eng == [False, False, True, True, True, True, False]
    assert caps[2] == 0.55
    assert caps[-1] == 1.0


def test_level_only_trigger_on_high_percentile():
    n = 900
    idx = pd.bdate_range("2020-01-01", periods=n)
    lvl = np.linspace(2.0, 6.0, n)
    frame = pd.DataFrame({"nominal_30y": lvl, "tips_yield": lvl - 1.0}, index=idx)
    p = RatesStressParams(
        confirm_days=2,
        exit_days=2,
        pct_enter=97.0,
        z_enter=99.0,
        z_extreme=99.0,
        cap_level_only=0.65,
    )
    out = compute_rates_stress_series(frame, p)
    assert float(out["nominal_30y_pct"].iloc[-1]) >= 97.0
    assert bool(out["engaged"].iloc[-1]) is True
    assert out["rates_cap"].iloc[-1] <= 0.65 + 1e-9


def test_overlap_counts():
    idx = pd.bdate_range("2024-01-01", periods=10)
    rates = pd.DataFrame(
        {"engaged": [True, True, False, True, False, False, True, True, False, False]},
        index=idx,
    )
    st = pd.Series(
        [
            "久期压力",
            "分裂/未确认",
            "分母端宽松",
            "美元压力",
            "分母端宽松",
            "分裂/未确认",
            "分裂/未确认",
            "久期压力",
            "分母端宽松",
            "分母端宽松",
        ],
        index=idx,
    )
    ov = overlap_with_duration_state(rates, st)
    assert ov["rates_engaged_days"] == 5
    assert ov["overlap_duration"] == 2
    assert ov["rates_engaged_not_any_tight"] == 2


def test_combined_budget_rates_leg_hard_mode():
    """Hard mode still available; production default is shadow."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "daily_macro_consolidated.py"
    spec = importlib.util.spec_from_file_location("dmc", path)
    dmc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmc)

    cfg = dmc.load_config()
    cfg["rates_stress"]["bind_mode"] = "hard"
    denom = {"main_state": "分母端宽松"}
    tech = {"decision": {"risk_budget": 0.80}}
    theme = {"risk_bias": "risk_on", "dominant_theme": {"risk_bias": "risk_on"}}
    rates = {"rates_cap": 0.55, "engaged": True}
    out = dmc.compute_combined_budget(denom, tech, theme, rates=rates, cfg=cfg)
    assert out["rates_ceiling"] == 0.55
    assert out["combined_budget"] == 0.55
    assert "利率" in out["binding"]
