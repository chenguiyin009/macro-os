# -*- coding: utf-8 -*-
from scripts.rules import (
    a_share_column_hit,
    direction_hit,
    map_posture,
    pine_top3_hit,
    relative_strength_hit,
    semi_a_share_gate_hit,
)


def test_map_posture_variants():
    assert map_posture("risk_on") == "bullish"
    assert map_posture("risk_on_observe") == "bullish"
    assert map_posture("defend") == "bearish"
    assert map_posture("wait") == "neutral"
    assert map_posture(None, bias="risk_off") == "bearish"
    assert map_posture("mixed") == "neutral"


def test_direction_bullish_up_hits():
    r = direction_hit("risk_on", 0.01)
    assert r.hit is True
    assert "偏多" in r.detail


def test_direction_bullish_down_misses():
    r = direction_hit("risk_on_observe", -0.012, bias="risk_on")
    assert r.hit is False
    assert r.suggestion


def test_direction_defend_down_hits():
    r = direction_hit("defend", -0.005)
    assert r.hit is True


def test_direction_defend_miss_nq_near_thresh_phrasing():
    """binding 含 NQ 且 denom_c≥0.8 → 写「NQ硬否决近阈误伤」而非「分母纪律」."""
    r = direction_hit(
        "defend",
        0.01,
        binding=["NQ动因"],
        denom_ceiling=0.8,
    )
    assert r.hit is False
    assert r.suggestion and "NQ硬否决近阈误伤" in r.suggestion
    assert "分母纪律" not in (r.suggestion or "")
    r2 = direction_hit("defend", 0.01, binding=["分母"], denom_ceiling=0.35)
    assert r2.hit is False
    assert "分母纪律" in (r2.suggestion or "")


def test_direction_wait_chop_hits():
    r = direction_hit("wait", 0.002)
    assert r.hit is True


def test_direction_wait_big_move_misses():
    r = direction_hit("wait", 0.015)
    assert r.hit is False


def test_direction_missing_ret_abstains():
    r = direction_hit("risk_on", None)
    assert r.hit is None


def test_relative_strength_majority():
    # strong beat bench, weak lag → hit
    r = relative_strength_hit("科技", [0.02, 0.015], [-0.01], 0.005)
    assert r.hit is True
    r2 = relative_strength_hit("科技", [0.0, -0.01], [0.02], 0.005)
    assert r2.hit is False
    assert r2.suggestion


def test_semi_downgrade_consistent_when_smh_weak():
    r = semi_a_share_gate_hit(True, "降预期/谨慎", -0.01, -0.03)
    assert r.hit is True


def test_semi_downgrade_inconsistent_when_smh_strong():
    r = semi_a_share_gate_hit(True, "降预期/谨慎", 0.02, 0.03)
    assert r.hit is False
    assert r.suggestion


def test_semi_gate_note_includes_just_neg_magnitude():
    r = semi_a_share_gate_hit(
        True,
        "降预期/谨慎",
        0.02,
        0.03,
        triggers=["rel5<0"],
        shadow_weak_downgrade=True,
        pred_rel5_pct=-0.4,
    )
    assert r.hit is False
    assert "仅刚负" in r.detail
    assert "预判rel5=-0.40%" in r.detail
    assert r.suggestion and "仅刚负" in r.suggestion


def test_semi_no_downgrade_when_weak_misses():
    r = semi_a_share_gate_hit(False, "可偏多", -0.02, -0.04)
    assert r.hit is False


def test_pine_top3_majority_beats():
    r = pine_top3_hit([("XLE", 0.02), ("XLP", 0.01), ("XLU", -0.005)], 0.0)
    assert r.hit is True
    assert "笔记·不驱动C" in r.name
    r2 = pine_top3_hit([("XLE", -0.01), ("XLP", -0.02), ("XLU", 0.001)], 0.0)
    assert r2.hit is False
    assert "不改生产" in (r2.suggestion or "")
    assert "笔记·不驱动C" in (r2.suggestion or "")


def test_a_share_column_separate_from_us_defend():
    r = a_share_column_hit("defend", "降预期/谨慎", True, -0.01, -0.008)
    assert r.hit is True
    assert "对A列" in r.name
    r2 = a_share_column_hit("defend", "降预期/谨慎", True, 0.02, 0.015)
    assert r2.hit is False
    assert r2.suggestion and "美股defend" in r2.suggestion
