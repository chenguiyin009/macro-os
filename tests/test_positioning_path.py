"""Unit tests for positioning_path (PPO)."""
from __future__ import annotations

from core.positioning_path import (
    PATH_BAD_EASE,
    PATH_DETERIOR,
    PATH_MIXED,
    PATH_REPAIR,
    PPOParams,
    apply_path_hysteresis,
    classify_curve_skew,
    classify_path,
    path_soft_cap,
)


def test_skew_ten_easy_thirty_tight():
    assert classify_curve_skew(-0.8, 0.9, z_dead=0.5) == "ten_easy_thirty_tight"
    assert classify_curve_skew(0.9, 0.9, z_dead=0.5) == "both_tight"
    assert classify_curve_skew(-0.8, -0.8, z_dead=0.5) == "both_easy"


def test_deteriorate_path():
    feat = {
        "tips_z5": -0.2,
        "n30_z5": 0.1,
        "qqq_ret_20": -0.07,
        "es_ret_20": -0.04,
        "hy_z5": 0.2,
        "denom_state": "分裂/未确认",
        "denom_tier": "unconfirmed",
    }
    out = classify_path(feat, PPOParams())
    assert out["path"] == PATH_DETERIOR
    assert out["gate"] == "BLOCK_ADD"


def test_repair_path():
    feat = {
        "tips_z5": -0.2,
        "n30_z5": -0.1,
        "qqq_ret_20": 0.04,
        "es_ret_20": 0.02,
        "hy_z5": 0.1,
        "denom_tier": "unconfirmed",
        "settle_ok": True,
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


def test_hysteresis_exit():
    raw = [PATH_DETERIOR, PATH_MIXED, PATH_MIXED, PATH_REPAIR]
    held = apply_path_hysteresis(raw, confirm_days=1, exit_days=2)
    assert held[0] == PATH_DETERIOR
    assert held[1] == PATH_DETERIOR  # pending better
    assert held[2] == PATH_MIXED


def test_soft_cap():
    p = PPOParams(mode="soft_cap")
    assert path_soft_cap(PATH_DETERIOR, p) == 0.55
    assert path_soft_cap(PATH_BAD_EASE, p) == 0.45
    assert path_soft_cap(PATH_REPAIR, p) is None


def test_combined_flag_only_no_ppo_leg():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "daily_macro_consolidated.py"
    spec = importlib.util.spec_from_file_location("dmc", path)
    dmc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmc)
    cfg = dmc.load_config()
    assert (cfg.get("positioning_path") or {}).get("mode") == "flag_only"
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
    assert out.get("positioning_path", {}).get("path") == "DETERIOR"


def test_combined_soft_cap_binds():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "daily_macro_consolidated.py"
    spec = importlib.util.spec_from_file_location("dmc", path)
    dmc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmc)
    cfg = dmc.load_config()
    cfg["positioning_path"]["mode"] = "soft_cap"
    ppo = {
        "path": "DETERIOR",
        "soft_cap": 0.55,
        "mode": "soft_cap",
        "path_zh": "x",
        "gate": "BLOCK_ADD",
        "skew": "mixed",
        "positioning_path_state": {"held_path": "DETERIOR"},
    }
    out = dmc.compute_combined_budget(
        {"main_state": "分母端宽松"},
        {"decision": {"risk_budget": 0.80}},
        {"risk_bias": "risk_on", "dominant_theme": {"risk_bias": "risk_on"}},
        ppo=ppo,
        cfg=cfg,
    )
    assert out["combined_budget"] == 0.55
    assert "定位" in out["binding"]
