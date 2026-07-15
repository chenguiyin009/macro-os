"""Macro OS v5.0 — 物理红线求值（纯函数，无 I/O / 无 eval）。

设计边界（与官方 v5 宪法一致）：
- Kernel 必须 pure：本模块**不读 YAML、不做 eval、不碰 DB/网络**。
- 红线常量来自 config 的 SSOT（thresholds.yaml -> constitution.red_lines），
  由调用方（orchestrator）加载后通过 `red_lines` 参数传入。
- 命中红线只产出 `forced_hard_regime` / `reason_code`，由 orchestrator 折叠进
  `decide()` 的 `hard_regime`，再走既有 HARD_VETO 路径——本模块不替 kernel 做裁决。
- 量纲跟仓库走（百分比为事实标准）：VIX 用 40、HY 用 bp(数百)、
  core_pce 用 3.5（即 3.5%，与 thresholds.yaml / policy_engine 一致）。
- brent 默认**禁用**：brent_shock 来自 Pine 桥，不在 TV MCP -> build_features
  主路径，pre-kernel 阶段无该特征，故默认 config 不挂 brent_red_line（避免静默死规则）。
  要启用，先在 FeatureSchema 加 brent_shock + build_features 透传 + 提供数据源。
- 与 `policy_engine` 的职责边界（纵深防御，非重复实现）：本模块负责 pre-kernel 折叠
  `hard_regime`（命中即 HARD_VETO）；`policy_engine` 另在 allocation 通道对
  core_pce_max / vix_escape_hatch 做二次约束，并独占 danger(0–100) -> 危机阈值通道。
- 已知量纲分歧：`core/glen_red_lines.py` 历史用小数 core_pce（0.034/0.035），属独立
  子系统，待其 owner 统一到本系统的百分比标准，勿在本模块混用小数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RedLineVerdict:
    """物理红线评估结果。triggered=False 时其余字段无意义。"""

    triggered: bool = False
    forced_hard_regime: Optional[str] = None
    force_phase: Optional[str] = None
    reason_code: str = ""
    triggered_lines: List[str] = field(default_factory=list)


# 红线 -> 折叠后的 hard_regime。与既有 decide() 的 HARD_VETO 路径一致：
# hard_regime != RISK_ON 即触发 HARD_VETO。
# 注：brent_red_line 默认不在 config 中（见 thresholds.yaml 注释），仅在显式配置后参与求值。
_RED_LINE_TO_REGIME: Dict[str, str] = {
    "vix_escape_hatch": "LIQUIDITY_SQUEEZE",
    "hy_credit_spread_bp": "LIQUIDITY_SQUEEZE",
    "brent_red_line": "LIQUIDITY_SQUEEZE",  # 默认禁用：brent_shock 不在主路径特征中
    "core_pce_max": "LIQUIDITY_SQUEEZE",
}

# 红线配置键 -> 实际特征字段键（解耦命名，避免 hy_credit_spread_bp vs hy_credit_spread 错位）。
_RED_LINE_FEATURE_KEY: Dict[str, str] = {
    "vix_escape_hatch": "vix",
    "hy_credit_spread_bp": "hy_credit_spread",
    "brent_red_line": "brent_shock",  # 需 Pine 桥注入，默认无数据源
    "core_pce_max": "core_pce",
}


def evaluate_physical_red_lines(
    features: Dict[str, Any],
    red_lines: Dict[str, Any],
) -> RedLineVerdict:
    """基于已加载的红线 SSOT 评估物理红线。

    Args:
        features: 内部特征 dict（来自 build_features）。
        red_lines: 已加载的红线阈值 dict（来自 config，非本函数读取）。

    Returns:
        RedLineVerdict：命中则给出 forced_hard_regime，交由 orchestrator 折叠。
    """
    fired: List[str] = []
    for key, regime in _RED_LINE_TO_REGIME.items():
        threshold = red_lines.get(key)
        if threshold is None:
            continue
        feature_key = _RED_LINE_FEATURE_KEY.get(key, key)
        value = features.get(feature_key)
        if value is None:
            continue
        if _exceeds(value, threshold):
            fired.append(key)

    if not fired:
        return RedLineVerdict()

    primary = fired[0]
    return RedLineVerdict(
        triggered=True,
        forced_hard_regime=_RED_LINE_TO_REGIME.get(primary, "LIQUIDITY_SQUEEZE"),
        reason_code=f"PHYSICAL_RED_LINE_{primary.upper()}",
        triggered_lines=fired,
    )


def _exceeds(value: Any, threshold: Any) -> bool:
    """全部红线为“超过即熔断”的上限型阈值；非数值/缺失安全跳过。"""
    try:
        return float(value) >= float(threshold)
    except (TypeError, ValueError):
        return False
