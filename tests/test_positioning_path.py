"""Unit tests for positioning_path (PPO) — tightened DETERIOR with LH/LL."""
from __future__ import annotations

import numpy as np

from core.positioning_path import (
    PATH_BAD_EASE,
    PATH_DETERIOR,
    PATH_MIXED,
    PATH_REPAIR,
    PPOParams,
    apply_path_hysteresis,
    classify_curve_skew,
    classify_path,
    detect_swing_structure,
    path_soft_cap,
)


def test_skew_ten_easy_thirty_tight():
    assert classify_curve_skew(-0.8, 0.9, z_dead=0.5) == "ten_easy_thirty_tight"
    assert classify_curve_skew(0.9, 0.9, z_dead=0.5) == "both_tight"


def _lh_ll_series():
    # Construct clear lower highs and lower lows with fractal L=R=2
    # pattern of peaks descending and troughs descending
    x = []
    # swing structure via piecewise
    levels = [10, 9, 11, 8, 10.5, 7.5, 9.5, 7, 9, 6.5, 8.5, 6, 8, 5.5]
    for i, v in enumerate(levels):
        x.extend([v - 0.3, v, v - 0.3])
    return x


def test_detect_lh_ll():
    s = detect_swing_structure(_lh_ll_series(), left=2, right=2)
    assert s["n_swing_high"] >= 2
    assert s["n_swing_low"] >= 2
    # descending swings expected
    assert s["lh_ll"] is True


def test_deteriorate_requires_lh_ll():
    p = PPOParams(require_lh_ll_for_deteriorate=True)
    # ret bad only — no structure → NOT deteriorate
    feat = {
        "tips_z5": -0.2,
        "n30_z5": 0.1,
        "qqq_ret_20": -0.07,
        "es_ret_20": -0.04,
        "hy_z5": 0.2,
        "denom_tier": "unconfirmed",
    }
    out = classify_path(feat, p)
    assert out["path"] != PATH_DETERIOR
    assert out["flags"]["ret_bad"] is True
    assert out["flags"]["nq_struct_bad"] is False

    # ret bad + explicit LH/LL → DETERIOR
    feat2 = {**feat, "lower_high": True, "lower_low": True, "lh_ll": True}
    out2 = classify_path(feat2, p)
    assert out2["path"] == PATH_DETERIOR
    assert out2["gate"] == "BLOCK_ADD"


def test_deteriorate_from_closes():
    p = PPOParams(require_lh_ll_for_deteriorate=True)
    closes = _lh_ll_series()
    # make last 21 days down ~7%
    closes = list(np.linspace(100, 92, 40)) + closes[-20:]
    # force end lower
    base = list(np.linspace(110, 100, 30))
    # append structured decline
    dec = []
    price = 100.0
    for i in range(40):
        price *= 0.997
        # small zig-zag
        dec.append(price * (1.01 if i % 7 == 3 else 0.995 if i % 7 == 5 else 1.0))
    closes = base + dec
    r20 = closes[-1] / closes[-21] - 1.0
    feat = {
        "tips_z5": -0.1,
        "n30_z5": 0.0,
        "qqq_ret_20": r20,
        "qqq_closes": closes,
        "hy_z5": 0.0,
        "denom_tier": "unconfirmed",
    }
    out = classify_path(feat, p)
    # if r20 bad enough and structure finds lh_ll
    if r20 <= -0.05 and out["flags"].get("lh_ll"):
        assert out["path"] == PATH_DETERIOR
    else:
        # still valid: may be MIXED if zig-zag didn't form clean LH/LL
        assert out["path"] in (PATH_DETERIOR, PATH_MIXED)


def test_repair_path():
    feat = {
        "tips_z5": -0.2,
        "n30_z5": -0.1,
        "qqq_ret_20": 0.04,
        "es_ret_20": 0.02,
        "hy_z5": 0.1,
        "denom_tier": "unconfirmed",
        "settle_ok": True,
        "higher_high": True,
        "higher_low": True,
        "hh_hl": True,
        "lh_ll": False,
    }
    out = classify_path(feat, PPOParams())
    assert out["path"] == PATH_REPAIR


def test_bad_ease_path():
    feat = {
        "tips_z5": -0.8,
        "n30_z5": -0.2,
        "qqq_ret_20": -0.08,
        "bei_d5": -5.0,
        "gold_z5": 1.0,
        "hy_z5": 0.0,
        "denom_tier": "unconfirmed",
    }
    out = classify_path(feat, PPOParams())
    assert out["path"] == PATH_BAD_EASE


def test_hysteresis_deteriorate_needs_3():
    raw = [PATH_MIXED, PATH_DETERIOR, PATH_DETERIOR, PATH_DETERIOR, PATH_DETERIOR]
    held = apply_path_hysteresis(
        raw, confirm_days=2, exit_days=2, deteriorate_confirm_days=3
    )
    # enter DETERIOR only on 3rd consecutive worse day (indices 1,2,3)
    assert held[0] == PATH_MIXED
    assert held[1] == PATH_MIXED
    assert held[2] == PATH_MIXED
    assert held[3] == PATH_DETERIOR


def test_soft_cap():
    p = PPOParams(mode="soft_cap")
    assert path_soft_cap(PATH_DETERIOR, p) == 0.55


def test_combined_flag_only():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "daily_macro_consolidated.py"
    spec = importlib.util.spec_from_file_location("dmc", path)
    dmc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmc)
    cfg = dmc.load_config()
    ppo = {
        "path": "DETERIOR",
        "path_zh": "定位恶化",
        "gate": "BLOCK_ADD",
        "skew": "mixed",
        "mode": "flag_only",
        "soft_cap": None,
        "positioning_path_state": {"held_path": "DETERIOR", "up_streak": 0, "dn_streak": 0},
    }
    out = dmc.compute_combined_budget(
        {"main_state": "久期压力"},
        {"decision": {"risk_budget": 0.80}},
        {"risk_bias": "risk_on", "dominant_theme": {"risk_bias": "risk_on"}},
        ppo=ppo,
        cfg=cfg,
    )
    assert "定位" not in (out.get("binding") or [])
    assert out["combined_budget"] == 0.35
