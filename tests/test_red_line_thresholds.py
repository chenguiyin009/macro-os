"""PR-1 回归：物理红线 SSOT 的量纲契约。

防止“尺度 bug”回潮：之前曾误用 HY 0–6、danger 0–1 的错量级阈值，
导致永远误触或永不触发。这里把正确量级钉死。
"""
from __future__ import annotations

from config.config_loader import load_hard_constraints, load_red_lines


def test_red_lines_ssot_scale_correct() -> None:
    red = load_red_lines()
    # VIX 红线：点值 40（非 0–1）
    assert red["vix_escape_hatch"] == 40.0
    # HY 红线：bp 量纲（数百），必须 >= 100 守卫，杜绝 0–6 小数误触
    assert red["hy_credit_spread_bp"] >= 100.0
    # core_pce 红线：百分比量纲（3.5 == 3.5%），与 policy_engine 同源
    assert red["core_pce_max"] == 3.5
    # brent 红线默认禁用：brent_shock 不在 pre-kernel 主路径特征中，
    # 留在默认配置会成“静默死规则”（永远跳过 = 假保护）。重接需数据源。
    assert "brent_red_line" not in red


def test_danger_scale_is_0_to_100_not_0_to_1() -> None:
    """danger 由 policy_engine 负责，其危机线必须是 0–100 量纲（默认 75）。"""
    hc = load_hard_constraints()
    assert hc.constitution["danger_crisis_threshold"] == 75
