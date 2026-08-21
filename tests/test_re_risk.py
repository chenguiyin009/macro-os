"""Tests for Scheme A re-risk dual budget."""
from __future__ import annotations

from core.re_risk import ReRiskParams, compute_re_risk, evaluate_permit, step_target


def test_permit_blocks_tight_and_lhll():
    p = evaluate_permit(
        denom_tier="tight",
        tech_ceiling=0.80,
        ppo_gate="NEUTRAL",
        ppo_flags={"lh_ll": True},
        qqq_ret_20=-0.02,
        params=ReRiskParams(),
    )
    assert p["risk_on_permit"] is False
    assert "denom_tier_tight" in p["permit_blockers"]


def test_permit_allows_clean():
    p = evaluate_permit(
        denom_tier="risk_on",
        tech_ceiling=0.80,
        ppo_gate="ALLOW_DISCUSS",
        ppo_flags={"lh_ll": False},
        qqq_ret_20=0.03,
        curve_skew="mixed",
        rates_shadow={"engaged": False},
        params=ReRiskParams(),
    )
    assert p["risk_on_permit"] is True
    assert p["permit_blockers"] == []


def test_fast_cut_defense():
    out = step_target(
        prev_target=0.70,
        defense_ceiling=0.35,
        offense_cap=0.80,
        permit=True,
        permit_streak=10,
        days_since_up=10,
        params=ReRiskParams(),
    )
    assert out["target_budget"] == 0.35
    assert out["action"] == "cut_defense"


def test_no_raise_without_permit():
    out = step_target(
        prev_target=0.55,
        defense_ceiling=0.80,
        offense_cap=0.80,
        permit=False,
        permit_streak=0,
        days_since_up=10,
        params=ReRiskParams(),
    )
    assert out["target_budget"] == 0.55
    assert out["action"] == "hold_no_permit"


def test_step_up_after_confirm():
    p = ReRiskParams(step_confirm_days=3, max_step_up=0.10, min_hold_after_up_days=0)
    out = step_target(
        prev_target=0.55,
        defense_ceiling=0.80,
        offense_cap=0.80,
        permit=True,
        permit_streak=3,
        days_since_up=10,
        params=p,
    )
    assert out["action"] == "step_up"
    assert abs(out["target_budget"] - 0.65) < 1e-9


def test_wait_confirm():
    out = step_target(
        prev_target=0.55,
        defense_ceiling=0.80,
        offense_cap=0.80,
        permit=True,
        permit_streak=1,
        days_since_up=10,
        params=ReRiskParams(step_confirm_days=3),
    )
    assert out["action"] == "wait_confirm"
    assert out["target_budget"] == 0.55


def test_compute_re_risk_integration():
    snap = compute_re_risk(
        defense_ceiling=0.55,
        prev_state={"target_budget": 0.55, "permit_streak": 0, "days_since_up": 9},
        denom_tier="risk_on",
        tech_ceiling=0.80,
        ppo={"gate": "ALLOW_DISCUSS", "flags": {"lh_ll": False}, "metrics": {"qqq_ret_20": 0.04}, "skew": "mixed"},
        rates_shadow={"engaged": False},
        params=ReRiskParams(step_confirm_days=1, min_hold_after_up_days=0),
    )
    assert snap["risk_on_permit"] is True
    assert snap["target_budget"] >= 0.55


def test_consolidator_attaches_re_risk():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "daily_macro_consolidated.py"
    spec = importlib.util.spec_from_file_location("dmc", path)
    dmc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmc)
    cfg = dmc.load_config()
    assert "re_risk" in cfg
    combined = dmc.compute_combined_budget(
        {"main_state": "分母端宽松"},
        {"decision": {"risk_budget": 0.80}},
        {"risk_bias": "risk_on", "dominant_theme": {"risk_bias": "risk_on"}},
        cfg=cfg,
    )
    ppo = {
        "path": "REPAIR",
        "gate": "ALLOW_DISCUSS",
        "skew": "mixed",
        "flags": {"lh_ll": False},
        "metrics": {"qqq_ret_20": 0.05},
        "mode": "flag_only",
    }
    rr = dmc.build_re_risk_snapshot(combined, ppo, cfg=cfg, prev_combined=None)
    assert rr is not None
    assert "target_budget" in rr
    assert rr["defense_ceiling"] == combined["combined_budget"]
