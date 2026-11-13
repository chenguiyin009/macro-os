# -*- coding: utf-8 -*-
from scripts.rules import (
    SEMI_OUTCOME_CLEAR_5D,
    a_share_column_hit,
    direction_hit,
    expected_semi_mapping,
    map_posture,
    pine_top3_hit,
    relative_strength_hit,
    select_themes_by_state,
    semi_a_share_gate_integrity_hit,
    semi_a_share_gate_outcome_hit,
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


def test_select_themes_excludes_neutrals_and_keeps_baskets():
    themes = [
        {"name": "半导体", "proxy": "SMH", "score": 54, "state": "中性观察"},
        {"name": "软件", "proxy": "IGV", "score": 44, "state": "中性观察"},
        {"name": "AI综合", "proxy": "AIQ", "score": 42, "state": "中性观察"},
        {"name": "AI应用软件", "proxy": "篮子", "score": 74, "state": "早期轮动"},
        {"name": "AI硬件卖方", "proxy": "篮子", "score": 62, "state": "分数够、动能不足"},
        {"name": "云计算", "proxy": "SKYY", "score": 36, "state": "资金撤出"},
        {"name": "储存", "proxy": "篮子", "score": 34, "state": "资金撤出"},
        {"name": "机器人", "proxy": "BOTZ", "score": 18, "state": "派发/撤出"},
    ]
    strong, weak = select_themes_by_state(themes)
    assert [t["name"] for t in strong] == ["AI应用软件"]
    assert "半导体" not in [t["name"] for t in strong + weak]
    assert "AI硬件卖方" not in [t["name"] for t in strong + weak]
    assert {t["name"] for t in weak} == {"云计算", "储存", "机器人"}
    # 篮子 retained when state is clearly strong/weak
    assert strong[0]["proxy"] == "篮子"
    assert any(t["proxy"] == "篮子" for t in weak)


def test_select_themes_empty_when_only_neutrals():
    themes = [
        {"name": "半导体", "proxy": "SMH", "score": 54, "state": "中性观察"},
        {"name": "软件", "proxy": "IGV", "score": 50, "state": "分数够、动能不足"},
    ]
    strong, weak = select_themes_by_state(themes)
    assert strong == []
    assert weak == []


def test_relative_strength_official_uses_5d_majority():
    r = relative_strength_hit("科技", [0.04, 0.03], [-0.02], 0.01, horizon=5, official=True)
    assert r.hit is True
    assert r.status == "official"
    assert "官方5D" in r.detail
    r2 = relative_strength_hit("科技", [0.0, -0.02], [0.04], 0.01, horizon=5, official=True)
    assert r2.hit is False
    assert r2.status == "official"
    assert r2.suggestion
    assert "拥挤惩罚" not in (r2.suggestion or "")
    assert "过拟合短窗" not in (r2.suggestion or "")


def test_relative_strength_1d_is_observe_not_hit_or_miss():
    # Same numbers that used to be a 1D miss must NOT count and must not
    # emit the short-window overfitting suggestion.
    r = relative_strength_hit("科技", [0.0, -0.01], [0.02], 0.005, horizon=1, official=False)
    assert r.hit is None
    assert r.status == "observe"
    assert r.suggestion is None
    assert "拥挤惩罚" not in (r.detail or "")
    assert "过拟合" not in (r.detail or "")
    assert "观察" in r.detail


def test_relative_strength_abstains_when_no_measurable():
    r = relative_strength_hit("科技", [], [], 0.01, horizon=5, official=True)
    assert r.hit is None
    assert r.status == "abstain"
    r2 = relative_strength_hit("科技", [None], [None], 0.01, horizon=5, official=True)
    assert r2.hit is None
    assert r2.status == "abstain"


def test_semi_integrity_hit_when_mapping_matches_inputs():
    r = semi_a_share_gate_integrity_hit(
        False,
        "中性对照（未达偏多门槛）",
        selected_score=54.0,
        avg_score=42.69,
        selected_rel5_pct=0.76,
        selected_state="中性观察",
        triggers=[],
    )
    assert r.hit is True
    assert r.status == "official"
    assert r.suggestion is None


def test_semi_integrity_miss_when_force_should_have_fired():
    # rel5<0 should force downgrade; mapping left it off → 漏判
    r = semi_a_share_gate_integrity_hit(
        False,
        "中性对照（未达偏多门槛）",
        selected_score=50.0,
        avg_score=44.0,
        selected_rel5_pct=-0.8,
        selected_state="中性观察",
        triggers=[],
    )
    assert r.hit is False
    assert "漏判" in (r.detail + (r.suggestion or ""))


def test_semi_integrity_miss_when_force_should_not_have_fired():
    r = semi_a_share_gate_integrity_hit(
        True,
        "降预期/谨慎",
        selected_score=54.0,
        avg_score=42.0,
        selected_rel5_pct=0.76,
        selected_state="中性观察",
        triggers=["rel5<0"],
    )
    assert r.hit is False
    assert "误触发" in (r.detail + (r.suggestion or ""))


def test_expected_mapping_mirrors_production_thresholds():
    exp = expected_semi_mapping(8.0, 32.62, -11.87, selected_state="派发/撤出")
    assert exp["force_semi_downgrade"] is True
    assert "score<=avg-15" in exp["triggers"]
    assert "rel5<0" in exp["triggers"]
    exp2 = expected_semi_mapping(54.0, 42.69, 0.76, selected_state="中性观察")
    assert exp2["force_semi_downgrade"] is False
    assert exp2["semi_chip_stance"].startswith("中性对照")


def test_semi_outcome_observe_until_5d_ready():
    r = semi_a_share_gate_outcome_hit(
        False, "中性对照（未达偏多门槛）", None, smh_rel1=-0.0071, ready_5d=False
    )
    assert r.hit is None
    assert r.status == "observe"
    assert r.suggestion is None
    assert "漏判" not in (r.detail or "")
    assert "1D" in r.detail


def test_semi_outcome_official_miss_only_when_5d_clearly_opposite():
    # Un-downgraded + clearly weak 5D → miss
    r = semi_a_share_gate_outcome_hit(
        False,
        "中性对照（未达偏多门槛）",
        -0.05,
        smh_rel1=-0.007,
        ready_5d=True,
    )
    assert r.hit is False
    assert r.status == "official"
    assert r.suggestion
    # Downgrade + clearly strong 5D → miss
    r2 = semi_a_share_gate_outcome_hit(True, "降预期/谨慎", 0.04, smh_rel1=0.01, ready_5d=True)
    assert r2.hit is False
    # Downgrade + modest 5D (below 2% threshold) is not a miss
    modest = SEMI_OUTCOME_CLEAR_5D - 0.005
    r3 = semi_a_share_gate_outcome_hit(True, "降预期/谨慎", modest, ready_5d=True)
    assert r3.hit is True
    # No downgrade + modest negative 5D is not a miss
    r4 = semi_a_share_gate_outcome_hit(
        False, "中性对照（未达偏多门槛）", -modest, ready_5d=True
    )
    assert r4.hit is True


def test_semi_outcome_does_not_use_1d_as_miss():
    # 1D clearly weak but 5D not ready → 观察, not 漏判
    r = semi_a_share_gate_outcome_hit(
        False, "中性对照（未达偏多门槛）", None, smh_rel1=-0.02, ready_5d=False
    )
    assert r.hit is None
    assert "漏判" not in (r.detail + (r.suggestion or ""))


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


def test_a_share_column_records_concrete_skip_reason():
    r = a_share_column_hit(
        "defend",
        "降预期/谨慎",
        True,
        None,
        None,
        skip_reason="抓取失败（期望 2026-11-12，缓存仍止于 2026-11-11，源=cache-stale）",
    )
    assert r.hit is None
    assert "抓取失败" in r.detail
    assert "缺次日 A 股收益，跳过" not in r.detail
