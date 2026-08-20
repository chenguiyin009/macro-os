"""Tests for D-enhancement denom ceiling + rates shadow posture."""
from __future__ import annotations

from core.denom_ceiling import (
    apply_ceiling_hysteresis,
    bind_denom_ceiling,
    classify_denom_tier,
    series_bind,
)


def test_classify_pine_states():
    assert classify_denom_tier("久期压力") == "tight"
    assert classify_denom_tier("信用传导") == "crisis"
    assert classify_denom_tier("分母端宽松") == "risk_on"
    assert classify_denom_tier("分裂/未确认") == "unconfirmed"
    assert classify_denom_tier("美元压力") == "tight"


def test_exit_hysteresis_holds_tight():
    raw = [0.35, 0.35, 0.80, 0.80, 0.80, 0.80]
    held = apply_ceiling_hysteresis(raw, confirm_enter=1, confirm_exit=3)
    assert held[:2] == [0.35, 0.35]
    assert held[2] == 0.35
    assert held[3] == 0.35
    assert held[4] == 0.80
    assert held[5] == 0.80


def test_enter_tighter_immediate():
    raw = [0.80, 0.35, 0.35]
    held = apply_ceiling_hysteresis(raw, confirm_enter=1, confirm_exit=3)
    assert held == [0.80, 0.35, 0.35]


def test_bind_day_counters():
    block = {
        "crisis": {"ceiling": 0.10},
        "tight": {"ceiling": 0.35},
        "unconfirmed": {"ceiling": 0.55},
        "risk_on": {"ceiling": 0.80},
        "default": 0.55,
    }
    d1 = bind_denom_ceiling("久期压力", cfg_block=block, hyst_cfg={"confirm_enter": 1, "confirm_exit": 3})
    assert d1["ceiling"] == 0.35
    d2 = bind_denom_ceiling(
        "分裂/未确认",
        cfg_block=block,
        hyst_cfg={"confirm_enter": 1, "confirm_exit": 3},
        prev_ceiling=0.35,
        loose_streak=0,
    )
    assert d2["ceiling"] == 0.35
    assert d2["hysteresis"] == "pending_looser"
    d3 = bind_denom_ceiling(
        "分裂/未确认",
        cfg_block=block,
        hyst_cfg={"confirm_enter": 1, "confirm_exit": 3},
        prev_ceiling=0.35,
        loose_streak=2,
    )
    assert d3["ceiling"] == 0.55
    assert d3["hysteresis"] == "exit_looser"


def test_series_bind_pine_path():
    block = {
        "crisis": {"ceiling": 0.10},
        "tight": {"ceiling": 0.35},
        "unconfirmed": {"ceiling": 0.55},
        "risk_on": {"ceiling": 0.80},
        "default": 0.55,
    }
    # exit tight->unconfirmed needs 3 looser days; then unconfirmed->risk_on needs another 3
    states = [
        "久期压力",
        "分裂/未确认",
        "分裂/未确认",
        "分裂/未确认",
        "分母端宽松",
        "分母端宽松",
        "分母端宽松",
    ]
    out = series_bind(states, block, {"confirm_enter": 1, "confirm_exit": 3})
    ceilings = [r["ceiling"] for r in out]
    assert ceilings[0] == 0.35
    assert ceilings[1] == 0.35
    assert ceilings[2] == 0.35
    assert ceilings[3] == 0.55  # released to unconfirmed
    assert ceilings[4] == 0.55  # pending looser to risk_on
    assert ceilings[6] == 0.80


def test_combined_shadow_rates_not_in_min():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "daily_macro_consolidated.py"
    spec = importlib.util.spec_from_file_location("dmc", path)
    dmc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmc)

    cfg = dmc.load_config()
    assert (cfg.get("rates_stress") or {}).get("bind_mode") == "shadow"
    assert (cfg.get("denom_policy") or {}).get("confirm_exit") == 3

    denom = {"main_state": "久期压力"}
    tech = {"decision": {"risk_budget": 0.80}}
    theme = {"risk_bias": "risk_on", "dominant_theme": {"risk_bias": "risk_on"}}
    rates = {
        "rates_cap": 0.45,
        "engaged": True,
        "b_zone": True,
        "bind_mode": "shadow",
        "reason": "level+slope",
    }
    out = dmc.compute_combined_budget(denom, tech, theme, rates=rates, cfg=cfg)
    assert out["denom_tier"] == "tight"
    assert out["denominator_ceiling"] == 0.35
    assert out["rates_ceiling"] is None
    assert out["rates_status"] == "shadow"
    assert "利率" not in (out.get("binding") or [])
    assert out["combined_budget"] == 0.35
    assert out["rates_shadow"]["engaged"] is True
    assert out["tech_policy"] == "frozen_c_tier"


def test_combined_hard_rates_binds():
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
    rates = {"rates_cap": 0.55, "engaged": True, "b_zone": True}
    out = dmc.compute_combined_budget(denom, tech, theme, rates=rates, cfg=cfg)
    assert out["rates_ceiling"] == 0.55
    assert out["combined_budget"] == 0.55
    assert "利率" in out["binding"]
