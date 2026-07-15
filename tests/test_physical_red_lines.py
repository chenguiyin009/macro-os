"""PR-2 回归：physical_red_lines 纯函数 + 临界矩阵（禁止“偶然 HARD_VETO”）。

证明 dry-run / 主路径触发 HARD_VETO 必须是临界矩阵上的真实越线，而非尺度 bug：
- VIX 39/41、HY 6/400/599/600(bp；400=regime入门,600=夺权)、core_pce 3.4%/3.6%、brent 94/96
- hy=6 bp 是噪声级，绝不可触发（回归防护）
- core_pce 为百分比：3.5 阈值的上下各一档
- brent 默认禁用（死规则已移除），但“显式启用时”接线仍验证
- 集成：evaluate 结果折叠进 decide() 的 hard_regime，走既有 HARD_VETO 路径
"""
from __future__ import annotations

from core.decision_kernel import decide
from core.macro.physical_red_lines import evaluate_physical_red_lines
from core.schemas import AuthorityLevel
from config.config_loader import load_red_lines

# 完整配置（含显式启用的 brent），仅用于验证“启用时”的接线是否仍正确。
FULL_RED_LINES = {
    "vix_escape_hatch": 40.0,
    "hy_credit_spread_bp": 600.0,
    "brent_red_line": 95.0,
    "core_pce_max": 3.5,
}

# 默认配置（来自 thresholds.yaml，brent 已禁用）
DEFAULT_RED_LINES = load_red_lines()

CONFIG = {"decision": {"long_confidence_min": 0.60, "short_confidence_min": 0.65, "no_trade_confidence_max": 0.35, "reduce_threshold": 0.30}}


def test_vix_below_hatch_no_trigger() -> None:
    assert not evaluate_physical_red_lines({"vix": 39.0}, FULL_RED_LINES).triggered


def test_vix_at_hatch_triggers() -> None:
    v = evaluate_physical_red_lines({"vix": 41.0}, FULL_RED_LINES)
    assert v.triggered
    assert v.forced_hard_regime == "LIQUIDITY_SQUEEZE"
    assert v.reason_code == "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH"


def test_hy_bp_critical_matrix() -> None:
    # 6 bp 是噪声级，绝不可触发（回归防护：曾误用 0–6 量纲）
    assert not evaluate_physical_red_lines({"hy_credit_spread": 6.0}, FULL_RED_LINES).triggered
    # 400 = regime 入门，不等于物理夺权
    assert not evaluate_physical_red_lines({"hy_credit_spread": 400.0}, FULL_RED_LINES).triggered
    # 599 仍在夺权线下
    assert not evaluate_physical_red_lines({"hy_credit_spread": 599.0}, FULL_RED_LINES).triggered
    # 600 bp = 物理红线夺权
    hi = evaluate_physical_red_lines({"hy_credit_spread": 600.0}, FULL_RED_LINES)
    assert hi.triggered
    assert hi.forced_hard_regime == "LIQUIDITY_SQUEEZE"


def test_core_pce_percent_scale_guard() -> None:
    # core_pce 为百分比：3.4% 不触发，3.6% 触发（阈值 3.5）
    assert not evaluate_physical_red_lines({"core_pce": 3.4}, FULL_RED_LINES).triggered
    hi = evaluate_physical_red_lines({"core_pce": 3.6}, FULL_RED_LINES)
    assert hi.triggered
    assert hi.reason_code == "PHYSICAL_RED_LINE_CORE_PCE_MAX"
    assert hi.forced_hard_regime == "LIQUIDITY_SQUEEZE"


def test_brent_red_line_when_explicitly_enabled() -> None:
    # brent 默认禁用；此用例仅验证“若显式配置”接线仍正确（重接数据源时的回归防护）
    assert not evaluate_physical_red_lines({"brent_shock": 94.0}, FULL_RED_LINES).triggered
    assert evaluate_physical_red_lines({"brent_shock": 96.0}, FULL_RED_LINES).triggered


def test_default_config_has_no_brent_dead_rule() -> None:
    # brent 默认不在 red_lines 中，避免“静默死规则”（永远跳过 = 假保护）
    assert "brent_red_line" not in DEFAULT_RED_LINES
    # 必需的红线仍在（config_validation 要求 vix_escape_hatch / core_pce_max）
    assert "vix_escape_hatch" in DEFAULT_RED_LINES
    assert "core_pce_max" in DEFAULT_RED_LINES
    assert "hy_credit_spread_bp" in DEFAULT_RED_LINES
    assert DEFAULT_RED_LINES["hy_credit_spread_bp"] == 600.0


def test_default_config_ignores_brent_shock_feature() -> None:
    # 即使 features 带了 brent_shock，默认配置下也不评估（pre-kernel 阶段无该数据源）
    assert not evaluate_physical_red_lines(
        {"brent_shock": 96.0, "vix": 20.0}, DEFAULT_RED_LINES
    ).triggered


def test_multiple_lines_reported() -> None:
    v = evaluate_physical_red_lines(
        {"vix": 41.0, "hy_credit_spread": 600.0, "core_pce": 4.0}, FULL_RED_LINES
    )
    assert v.triggered
    assert set(v.triggered_lines) == {"vix_escape_hatch", "hy_credit_spread_bp", "core_pce_max"}


def test_missing_or_non_numeric_feature_safe() -> None:
    assert not evaluate_physical_red_lines({}, FULL_RED_LINES).triggered
    assert not evaluate_physical_red_lines({"vix": "n/a"}, FULL_RED_LINES).triggered


def test_fold_into_decide_matches_critical_matrix() -> None:
    """集成临界矩阵：证明触发不是“偶然 HARD_VETO”。"""
    # hy=6 bp -> 不触发 -> RISK_ON -> SOFT_POLICY
    kd_safe = decide({"hy_credit_spread": 6.0}, "RISK_ON", "RISK_ON", 0.7, 0.8, CONFIG)
    assert kd_safe.authority == AuthorityLevel.SOFT_POLICY

    # hy=600 bp -> 触发 -> LIQUIDITY_SQUEEZE -> HARD_VETO（经既有 decide() 路径）
    verdict = evaluate_physical_red_lines({"hy_credit_spread": 600.0}, FULL_RED_LINES)
    assert verdict.triggered
    kd_veto = decide(
        {"hy_credit_spread": 600.0},
        verdict.forced_hard_regime,
        "RISK_ON",
        0.7,
        0.8,
        CONFIG,
    )
    assert kd_veto.authority == AuthorityLevel.HARD_VETO
    assert kd_veto.risk_budget == 0.0
    assert kd_veto.defense_budget == 1.0
