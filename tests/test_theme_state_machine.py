# -*- coding: utf-8 -*-
"""Unit tests for theme_state_machine_daily + daily_macro_consolidated synthesis."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import theme_state_machine_daily as tsm
from scripts import daily_macro_consolidated as dmc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _xv(**overrides):
    """Neutral cross-asset snapshot; override keys to trip themes."""
    base = {
        "x01": 0.0, "x02": 0.0, "x03": 0.0, "x04": 0.0,
        "x05": 0.0, "x06": 0.0, "x07": 0.0, "x08": 0.0,
        "x09": 0.0, "x10": 0.0, "x11": 0.0, "x12": 0.0,
        "x13": 0.0, "x14": 0.0,
        "x18": 0.0, "x21": 0.0, "x24": 0.0, "x25": 0.0, "x29": False,
    }
    base.update(overrides)
    return base


def _biz_days(n=220, end="2026-07-17"):
    end_ts = pd.Timestamp(end)
    # business days ending at end
    idx = pd.bdate_range(end=end_ts, periods=n)
    return idx


def _flat_closes(n=220, end="2026-07-17", keys=None):
    idx = _biz_days(n, end)
    keys = keys or [k for k, *_ in tsm.SYMBOLS]
    out = {}
    for k in keys:
        # mild drift so roc/std not zero
        rng = np.random.default_rng(abs(hash(k)) % (2**31))
        rets = rng.normal(0, 0.002, size=len(idx))
        px = 100 * np.cumprod(1 + rets)
        out[k] = pd.Series(px, index=idx, dtype=float)
    return out


# ---------------------------------------------------------------------------
# Theme rule golden samples (scalar booleans)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_theme_dollar_squeeze_b09():
    xv = _xv(x05=1.2, x21=0.8, x08=-0.7, x24=-0.8)
    b = tsm.theme_booleans_scalar(xv)
    assert b[9] is True
    assert tsm.theme_risk_bias(9) == "risk_off"
    assert tsm.theme_pressure_override(9) is True


@pytest.mark.unit
def test_theme_carry_unwind_b10():
    xv = _xv(x07=1.2, x12=-0.8, x11=-0.4)
    b = tsm.theme_booleans_scalar(xv)
    assert b[10] is True
    assert tsm.theme_risk_bias(10) == "risk_off"
    assert tsm.theme_pressure_override(10) is True


@pytest.mark.unit
def test_theme_broad_risk_on_b11():
    xv = _xv(x24=0.8)
    b = tsm.theme_booleans_scalar(xv)
    assert b[11] is True
    assert tsm.theme_risk_bias(11) == "risk_on"
    assert tsm.theme_pressure_override(11) is False


@pytest.mark.unit
def test_select_dominant_pressure_pins_over_higher_evidence():
    h = {i: False for i in range(1, 18)}
    n = {i: 0 for i in range(1, 18)}
    g = {i: 0 for i in range(1, 18)}
    h[11] = True
    n[11] = 3
    g[11] = 5
    h[9] = True
    n[9] = 1
    g[9] = 1
    assert tsm.select_dominant(h, n, g) == 9


@pytest.mark.unit
def test_select_dominant_tie_break_by_persist():
    h = {i: False for i in range(1, 18)}
    n = {i: 0 for i in range(1, 18)}
    g = {i: 0 for i in range(1, 18)}
    h[11] = True
    n[11] = 2
    g[11] = 1
    h[12] = True
    n[12] = 2
    g[12] = 4
    assert tsm.select_dominant(h, n, g) == 12


# ---------------------------------------------------------------------------
# Calendar / completed bar / naming / quality
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_irx_and_jpy_labels_are_honest():
    labels = {k: lab for k, _, _, lab in tsm.SYMBOLS}
    assert "2Y" not in labels["x01"]
    assert "3M" in labels["x01"] or "IRX" in labels["x01"]
    assert "取反" in labels["x07"] or "强度" in labels["x07"]


@pytest.mark.unit
def test_align_uses_spy_anchor_ignores_btc_weekend_extension():
    idx = _biz_days(220, "2026-07-17")
    closes = _flat_closes(220, "2026-07-17")
    # BTC continues into weekend after last SPY day
    btc_idx = idx.append(pd.DatetimeIndex([pd.Timestamp("2026-07-18"), pd.Timestamp("2026-07-19")]))
    btc_vals = np.r_[closes["x14"].values, closes["x14"].iloc[-1] * 1.01, closes["x14"].iloc[-1] * 1.02]
    closes["x14"] = pd.Series(btc_vals, index=btc_idx)
    df, meta = tsm.align_closes(closes, as_of=dt.date(2026, 7, 17))
    assert df.index[-1].date() == dt.date(2026, 7, 17)
    assert "x14" in df.columns


@pytest.mark.unit
def test_prefer_latest_uses_anchor_latest_not_completed_session():
    closes = _flat_closes(220, "2026-07-20")
    # midday Monday NY - completed session is prior Friday 7/17
    now = dt.datetime(2026, 7, 20, 11, 30, tzinfo=ZoneInfo("America/New_York"))
    state = tsm.compute_state(closes, now=now, prefer_latest=True)
    assert state["date"] == "2026-07-20"
    assert state["quality"]["incomplete_bar"] is True
    assert state["quality"]["completed_session"] == "2026-07-17"
    # old behavior still available
    state_old = tsm.compute_state(closes, now=now, prefer_latest=False)
    assert state_old["date"] == "2026-07-17"
    assert state_old["quality"]["incomplete_bar"] is False


@pytest.mark.unit
def test_trim_incomplete_equity_bar_before_ny_close():
    # Friday 2026-07-17 10:00 America/New_York -> last completed is prior session
    now = dt.datetime(2026, 7, 17, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    last = tsm.last_completed_equity_session(now)
    assert last == dt.date(2026, 7, 16)


@pytest.mark.unit
def test_trim_incomplete_after_ny_close_keeps_today():
    now = dt.datetime(2026, 7, 17, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    last = tsm.last_completed_equity_session(now)
    assert last == dt.date(2026, 7, 17)


@pytest.mark.unit
def test_missing_critical_marks_degraded_not_zero_fill_silent():
    closes = _flat_closes(220, "2026-07-17")
    del closes["x05"]  # DXY critical
    state = tsm.compute_state(closes, as_of=dt.date(2026, 7, 17), now=dt.datetime(2026, 7, 17, 18, 0, tzinfo=ZoneInfo("America/New_York")))
    q = state["quality"]
    assert q["degraded"] is True
    assert "x05" in q["missing_symbols"]
    assert q["data_quality"] in ("degraded", "stale_degraded")


@pytest.mark.unit
def test_stale_when_as_of_lags_report_expectation():
    # Fri->Mon = 1 bd lag, not stale when threshold=1.
    q_ok = tsm.assess_freshness(
        as_of=dt.date(2026, 7, 17),
        expected_as_of=dt.date(2026, 7, 20),
        stale_after_days=1,
    )
    assert q_ok["stale"] is False
    assert q_ok["lag_days"] == 1

    # Wed->Mon = 3 bd lag -> stale
    q = tsm.assess_freshness(
        as_of=dt.date(2026, 7, 15),
        expected_as_of=dt.date(2026, 7, 20),
        stale_after_days=1,
    )
    assert q["stale"] is True
    assert q["lag_days"] > 1


@pytest.mark.unit
def test_persist_g_counts_lit_h_not_raw_b():
    # b: T T F T T T  -> rolling>=2 h ends True True False True True True?
    # h_i = sum(b[i-2:i+1]) >= 2 for window 3 ending at i
    b = pd.Series([True, True, False, True, True, True])
    g_h = tsm.persist_count_from_h(b)
    # After computing h series, cont from end on h
    h = b.rolling(3).sum() >= 2
    # manual: indices 0,1 nan/false-ish; we'll just assert helper matches _cont(h.fillna(False))
    assert g_h == tsm._cont(h.fillna(False))


# ---------------------------------------------------------------------------
# Consolidated synthesis: no false "一致"
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_synthesize_triple_unconfirmed_not_consistent():
    theme = {
        "date": "2026-07-17",
        "dominant_theme": None,
        "today_leaders": [
            {"label": "原油", "z": 1.8, "dir": "↑"},
            {"label": "纳指", "z": -1.7, "dir": "↓"},
            {"label": "标普", "z": -1.0, "dir": "↓"},
        ],
        "strength_table": [
            {"label": "标普(SPY)", "dir": "↓", "level": "强", "z": -1.0},
            {"label": "纳指(QQQ)", "dir": "↓", "level": "异常", "z": -1.7},
        ],
        "quality": {"stale": True, "degraded": False, "data_quality": "stale", "lag_days": 3},
    }
    tech = {
        "date": "2026-07-20",
        "tech_drawdown": -0.20,
        "decision": {"risk_budget": 0.35, "dampener_active": True},
    }
    denom = {
        "main_state": "分裂 / 未确认 [置信：—]",
        "dont_do": "不加新表达",
        "trigger_hint": "",
    }
    synth = dmc.synthesize(theme, tech, denom, "2026-07-17", "2026-07-20", "2026-07-20")
    assert "一致" not in synth["consistency"]
    assert synth["alignment"] in ("未确认", "数据降级")
    assert synth["bias"] in ("risk_off", "mixed", "none")


@pytest.mark.unit
def test_synthesize_pressure_theme_is_risk_off_not_risk_on():
    theme = {
        "date": "2026-07-17",
        "dominant_theme": {
            "id": 9,
            "name": "美元挤兑",
            "family": "压力覆盖类",
            "risk_bias": "risk_off",
            "pressure_override": True,
            "confidence": {"n3": 2, "persist_days": 3},
        },
        "today_leaders": [],
        "strength_table": [],
        "quality": {"stale": False, "degraded": False, "data_quality": "ok", "lag_days": 0},
    }
    tech = {
        "date": "2026-07-17",
        "tech_drawdown": -0.15,
        "decision": {"risk_budget": 0.35, "dampener_active": True},
    }
    denom = {"main_state": "RISK_OFF 紧缩", "dont_do": "x", "trigger_hint": ""}
    synth = dmc.synthesize(theme, tech, denom, "2026-07-17", "2026-07-17", "2026-07-17")
    assert synth["bias"] == "risk_off"
    assert synth["pressure_override"] is True
    # may be 共振偏空, but never 偏多
    assert "偏多" not in synth["consistency"]


@pytest.mark.unit
def test_newest_prefers_filename_date_not_mtime(tmp_path):
    older_name = tmp_path / "theme_state_machine_2026-07-18.json"
    newer_name = tmp_path / "theme_state_machine_2026-07-10.json"
    older_name.write_text("{}", encoding="utf-8")
    newer_name.write_text("{}", encoding="utf-8")
    # touch newer_name to be more recent mtime but older date in name
    import os, time
    os.utime(newer_name, None)
    time.sleep(0.05)
    os.utime(older_name, (time.time() - 1000, time.time() - 1000))
    hit = dmc._newest(tmp_path, "theme_state_machine_*.json")
    assert hit is not None
    assert "2026-07-18" in hit.name

@pytest.mark.unit
def test_strength_table_includes_ret1d():
    closes = _flat_closes(220, "2026-07-20")
    # force a known 1D pop on QQQ
    s = closes["x12"].copy()
    s.iloc[-1] = float(s.iloc[-2]) * 1.01
    closes["x12"] = s
    state = tsm.compute_state(closes, as_of=dt.date(2026, 7, 20), prefer_latest=True)
    assert "ret1d" in state
    assert abs(state["ret1d"]["x12"] - 0.01) < 1e-9
    payload = tsm.build_json(state)
    row = next(r for r in payload["strength_table"] if r["key"] == "x12")
    assert row["ret1d_pct"] == 1.0
    md = tsm.build_report(state)
    assert "1D%" in md
    assert "+1.00%" in md

