# -*- coding: utf-8 -*-
"""Unit tests: tech_rotation report state machine + 对A映射硬规则."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.tech_rotation_daily import (  # noqa: E402
    A_SHARE_AVG_LAG,
    REL5_EARLY_MIN,
    STATE_SCORE_OK_MOMENTUM_WEAK,
    a_share_mapping_from_selected,
    build_markdown,
    state_from_score,
    state_text,
)


@pytest.mark.unit
def test_early_rel5_gate_demotes_to_momentum_weak():
    """10-08 口径：score=61∈[60,75) 且 rel5≈-2.4% 不得标早期轮动。"""
    st = state_from_score(61.0, False, False, -0.0244, 0.0264, prev_score=61.0)
    assert st == STATE_SCORE_OK_MOMENTUM_WEAK
    assert state_text(st) == "分数够、动能不足"
    # 边界：rel5 恰为 -2% 仍可早期（门控为严格 <）
    st_edge = state_from_score(61.0, False, False, REL5_EARLY_MIN, 0.02, prev_score=61.0)
    assert st_edge == 1


@pytest.mark.unit
def test_early_hysteresis_needs_two_days_score_ge_60():
    """升档：连续 ≥2 交易日 score>=60 才进早期；跌出当日 score<60 即降。"""
    # 首日达标但昨日不足 → 态 4
    assert state_from_score(62.0, False, False, 0.01, 0.02, prev_score=50.0) == STATE_SCORE_OK_MOMENTUM_WEAK
    assert state_from_score(62.0, False, False, 0.01, 0.02, prev_score=None) == STATE_SCORE_OK_MOMENTUM_WEAK
    # 两日均 >=60 → 早期
    assert state_from_score(62.0, False, False, 0.01, 0.02, prev_score=60.0) == 1
    # 当日跌破 60 → 立即降（无降侧 55 滞回）
    assert state_from_score(59.0, False, False, 0.01, 0.02, prev_score=70.0) == 0


@pytest.mark.unit
def test_preserved_confirm_crowd_exit_priority():
    """保留确认进入 / 拥挤主升优先 / 撤出条件；不因 rel5 门控改写。"""
    assert state_from_score(75.0, False, False, -0.05, -0.01, prev_score=40.0) == 2
    # 拥挤主升优先于早期 rel5/滞回门控
    assert state_from_score(61.0, True, False, -0.05, 0.01, prev_score=40.0) == 3
    assert state_from_score(40.0, False, False, -0.01, -0.02, prev_score=80.0) == -1
    assert state_from_score(40.0, False, False, 0.01, -0.02, prev_score=80.0) == 0  # rel5 非负不撤出


@pytest.mark.unit
def test_a_share_force_downgrade_on_lag_or_neg_rel5():
    """10-09 口径：51 vs avg≈70（落后≥15）且 rel5<0 → 半导强制降预期。"""
    am = a_share_mapping_from_selected(51.0, 70.38, -0.0454, 0)
    assert am["force_semi_downgrade"] is True
    assert am["semi_chip_stance"] == "降预期/谨慎"
    assert "重回中性" in am["forbid_phrases"]
    assert "可对照加仓" in am["forbid_phrases"]
    assert am["software_cloud_ok"] is True
    assert am["semi_bullish_ok"] is False
    # 仅 score 落后也触发
    am2 = a_share_mapping_from_selected(50.0, 70.0, 0.01, 0)
    assert am2["force_semi_downgrade"] is True
    assert f"score<=avg-{A_SHARE_AVG_LAG:.0f}" in am2["triggers"]
    # 仅 rel5<0 也触发
    am3 = a_share_mapping_from_selected(70.0, 70.0, -0.001, 2)
    assert am3["force_semi_downgrade"] is True


@pytest.mark.unit
def test_a_share_bullish_requires_confirm_and_nonneg_rel5():
    am = a_share_mapping_from_selected(80.0, 70.0, 0.01, 2)
    assert am["force_semi_downgrade"] is False
    assert am["semi_bullish_ok"] is True
    assert "可偏多" in am["semi_chip_stance"]
    # 确认进入但 rel5<0 → 仍强制降级，不可偏多
    am_bad = a_share_mapping_from_selected(80.0, 70.0, -0.01, 2)
    assert am_bad["force_semi_downgrade"] is True
    assert am_bad["semi_bullish_ok"] is False


@pytest.mark.unit
def test_build_markdown_has_stable_a_share_section():
    payload = {
        "as_of": "2026-10-09",
        "run_date": "2026-10-12",
        "version": "test",
        "expired": False,
        "days_left": 30,
        "selected_theme": "半导体",
        "selected_theme_score": 51.0,
        "overall": {
            "status": "强势/确认轮动区",
            "avg_score": 70.38,
            "confirm": 5,
            "crowd": 2,
            "neutral": 2,
            "withdraw": 2,
        },
        "themes": [
            {
                "name": "半导体",
                "proxy": "SMH",
                "rel5_pct": -0.0454,
                "rel20_pct": 0.008,
                "rel60_pct": -0.004,
                "abs5_pct": -0.04,
                "trend": "偏弱",
                "vol_ratio": 0.9,
                "breadth_pct": 70.0,
                "score": 51.0,
                "state": "中性观察",
                "state_code": 0,
                "reason": "中性",
            }
        ],
        "attribution": [{"name": "x", "score": 1, "hint": "h"}],
        "counts": {"weak": 5, "abs_down": 3},
        "data_source": {},
        "quality": {
            "expected_as_of": "2026-10-09",
            "lag_days": 0,
            "stale": False,
            "actionable": True,
        },
        "a_share_mapping": a_share_mapping_from_selected(51.0, 70.38, -0.0454, 0),
    }
    md = build_markdown(payload, days_left=30, expired=False)
    assert "## 四、对A映射（硬规则）" in md
    assert "降预期/谨慎" in md
    assert "重回中性" in md
    assert "可对照加仓" in md
    assert "半导必须降级" in md
