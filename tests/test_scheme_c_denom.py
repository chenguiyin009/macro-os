"""Tests for Scheme C denom floors + A re-risk tuning."""
from __future__ import annotations

from core.denom_ceiling import (
    apply_ceiling_hysteresis,
    classify_denom_tier,
    series_bind,
    tier_to_ceiling,
)
from core.re_risk import ReRiskParams, step_target


def test_squeeze_not_crisis():
    assert classify_denom_tier("LIQUIDITY_SQUEEZE") == "squeeze"
    assert classify_denom_tier("信用传导") == "crisis"
    assert classify_denom_tier("久期压力") == "tight"


def test_squeeze_ceiling_higher_than_crisis():
    block = {
        "crisis": {"ceiling": 0.10},
        "squeeze": {"ceiling": 0.20},
        "tight": {"ceiling": 0.35},
        "unconfirmed": {"ceiling": 0.55},
        "risk_on": {"ceiling": 0.80},
        "default": 0.55,
    }
    assert tier_to_ceiling("squeeze", block) == 0.20
    assert tier_to_ceiling("crisis", block) == 0.10


def test_exit_faster_default_2():
    # tight 0.35 then loosen to 0.80; exit in 2 days
    raw = [0.35, 0.80, 0.80, 0.80]
    held = apply_ceiling_hysteresis(
        raw,
        confirm_enter=1,
        confirm_exit=2,
        block={
            "crisis": {"ceiling": 0.10},
            "squeeze": {"ceiling": 0.20},
            "tight": {"ceiling": 0.35},
            "unconfirmed": {"ceiling": 0.55},
            "risk_on": {"ceiling": 0.80},
        },
        hyst_cfg={"confirm_exit": 2},
    )
    assert held[0] == 0.35
    assert held[1] == 0.35
    assert held[2] == 0.80


def test_series_bind_squeeze_floor():
    block = {
        "crisis": {"ceiling": 0.10},
        "squeeze": {"ceiling": 0.20},
        "tight": {"ceiling": 0.35},
        "unconfirmed": {"ceiling": 0.55},
        "risk_on": {"ceiling": 0.80},
        "default": 0.55,
    }
    states = ["LIQUIDITY_SQUEEZE", "LIQUIDITY_SQUEEZE", "分裂/未确认", "分裂/未确认"]
    out = series_bind(states, block, {"confirm_enter": 1, "confirm_exit": 2})
    assert out[0]["tier"] == "squeeze"
    assert out[0]["ceiling"] == 0.20
    assert out[3]["ceiling"] == 0.55


def test_a_step_params_default_faster():
    p = ReRiskParams()  # tuned defaults 0.15 / 2 / 1
    assert p.max_step_up == 0.15
    assert p.step_confirm_days == 2
    out = step_target(
        prev_target=0.50,
        defense_ceiling=0.80,
        offense_cap=0.80,
        permit=True,
        permit_streak=2,
        days_since_up=10,
        params=p,
    )
    assert out["action"] == "step_up"
    assert abs(out["target_budget"] - 0.65) < 1e-9
