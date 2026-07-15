"""PR-1 回归：物理红线 SSOT 的量纲契约。

防止“尺度 bug”回潮：之前曾误用 HY 0–6、danger 0–1 的错量级阈值，
导致永远误触或永不触发。这里把正确量级钉死。
"""
from __future__ import annotations

from config.config_loader import load_hard_constraints, load_red_lines, load_thresholds


def test_red_lines_ssot_scale_correct() -> None:
    red = load_red_lines()
    # VIX 红线：点值 40（非 0–1）
    assert red["vix_escape_hatch"] == 40.0
    # HY 物理夺权线：bp 量纲，与 squeeze 入门 400 拆档
    assert red["hy_credit_spread_bp"] >= 100.0
    assert red["hy_credit_spread_bp"] == 600.0
    # core_pce 红线：百分比量纲（3.5 == 3.5%），与 policy_engine 同源
    assert red["core_pce_max"] == 3.5
    # brent 红线默认禁用
    assert "brent_red_line" not in red


def test_hy_regime_entry_and_redline_are_split() -> None:
    """400 = regime 标签入门；600 = pre-kernel 物理夺权。"""
    th = load_thresholds()
    entry = th["regime"]["liquidity_squeeze"]["hy_credit_spread_min"]
    critical = th["constitution"]["red_lines"]["hy_credit_spread_bp"]
    assert entry == 400
    assert critical == 600.0
    assert critical > entry


def test_danger_scale_is_0_to_100_not_0_to_1() -> None:
    """danger 由 policy_engine 负责，其危机线必须是 0–100 量纲（默认 75）。"""
    hc = load_hard_constraints()
    assert hc.constitution["danger_crisis_threshold"] == 75
