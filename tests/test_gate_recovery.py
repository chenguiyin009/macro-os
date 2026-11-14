# -*- coding: utf-8 -*-
import copy, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gate_recovery as gr
import daily_allocator as da

TECH_1112 = {
    "tech_drawdown": -0.10915047706632935,
    "tech_drawdown_raw": -0.24186688125071842,
    "trajectory": {"trough_date": "2026-11-05", "recovery_from_trough_pct": 17.82,
                   "drawdown_slope_5d_pp": 4.625, "naive_20d_peak_dd_pct": -10.68},
    "decision": {"tech_drawdown": -0.10915047706632935, "dampener_active": True},
}


def macro(tech, denom="分裂/未确认", nq=False):
    return {"denominator_state": {"main_state": denom}, "plain_advice": {"hard_veto": nq},
            "synthesis": {}, "tech_dampener": tech}


def test_recovery_from_trajectory_hits():
    ok, why = gr.recovery_exit_from_trajectory(TECH_1112["trajectory"])
    assert ok and "17.8%" in why


def test_recovery_needs_positive_slope():
    t = dict(TECH_1112["trajectory"], drawdown_slope_5d_pp=-0.1)
    assert gr.recovery_exit_from_trajectory(t)[0] is False


def test_recovery_needs_rebound():
    t = dict(TECH_1112["trajectory"], recovery_from_trough_pct=9.9)
    assert gr.recovery_exit_from_trajectory(t)[0] is False


def test_recovery_missing_traj_is_conservative():
    assert gr.recovery_exit_from_trajectory(None)[0] is False
    assert gr.recovery_exit_from_trajectory({"recovery_from_trough_pct": None})[0] is False


def test_score_gate_1112_new_rule():
    g = da.score_gate(macro(TECH_1112))
    assert g["eq_acute"] is False and g["eq_soft"] is True and g["recovery_exit"] is True
    assert g["score"] == 0.5 and g["state"] == "OFF" and g["sat_mult"] == 1.0
    assert g["raw_dd_source"] == "current_20d" and abs(g["raw_dd"] + 0.1068) < 1e-9
    # 影子旧规则仍为 WARN 1.0
    assert g["legacy_rule"]["score"] == 1.0 and g["legacy_rule"]["state"] == "WARN"


def test_score_gate_deepening_stays_acute():
    t = copy.deepcopy(TECH_1112)
    t["trajectory"]["drawdown_slope_5d_pp"] = -1.5
    g = da.score_gate(macro(t))
    assert g["eq_acute"] is True and g["score"] == 1.0 and g["state"] == "WARN"


def test_recovery_with_other_leg_gives_bear_band_not_full_bear():
    g = da.score_gate(macro(TECH_1112, nq=True))
    assert g["score"] == 1.5 and g["state"] == "BEAR"   # nq 1 + soft 0.5
    t = copy.deepcopy(TECH_1112); t["trajectory"]["recovery_from_trough_pct"] = 5.0
    g2 = da.score_gate(macro(t, nq=True))
    assert g2["score"] == 2.0


def test_raw_fallback_to_window_when_no_traj():
    t = copy.deepcopy(TECH_1112); t.pop("trajectory")
    g = da.score_gate(macro(t))
    assert g["raw_dd_source"] == "window_worst21" and g["eq_acute"] is True and g["state"] == "WARN"


def test_no_macro_off():
    g = da.score_gate(None)
    assert g["state"] == "OFF" and g["sat_mult"] == 1.0


def test_series_matches_macro_os_trajectory():
    sys.path.insert(0, str(ROOT / "macro-os"))
    from adapters.equity_stress import peak_to_trough_drawdown
    rng = np.random.default_rng(0)
    px = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 120))))
    ww = gr.worst_window_dd_series(px)
    for t in (30, 60, 119):
        assert abs(ww.iloc[t] - peak_to_trough_drawdown(list(px.iloc[: t + 1]), days=20)) < 1e-12
    tr = gr.trajectory_series(px)
    # 与 compute_soxx_trajectory 同口径：5点线性回归斜率
    dd = gr.dd20_series(px).values
    s5 = np.polyfit(np.arange(5.0), dd[-5:], 1)[0] * 100
    assert abs(tr["slope_pp"].iloc[-1] - s5) < 1e-9
    lo = 120 - 30
    assert abs(tr["rebound"].iloc[-1] - (px.iloc[-1] / px.iloc[lo:].min() - 1)) < 1e-12


def test_apply_recovery_to_soft():
    idx = range(3)
    a = pd.Series([True, True, False]); s = pd.Series([False, False, True])
    rec = pd.Series([False, True, True]); sm = pd.Series([-0.12, -0.11, -0.06])
    a2, s2 = gr.apply_recovery(a, s, sm, rec)
    assert list(a2) == [True, False, False] and list(s2) == [False, True, True]
