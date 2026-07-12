"""Trinity OS v2.2.1 — Phase 3 风控主流程集成层 (risk_integration)。

WHY THIS FILE EXISTS
--------------------
v2.2.1 的风险子系统（RiskGateway + TrinityAdapter + EvidenceLedger）是独立模块。
要把它们**外科手术式植入**到 `runtime/orchestrator.py` 的 `run_pipeline` 决策循环中，
需要一个全局单例 + 钩子函数，确保：
  * 系统启动时一次初始化（`initialize_risk_integration`）；
  * 决策循环中零侵入调用（`should_execute_risk_action`）；
  * 全局解耦：Orchestrator 不直接依赖 RiskGateway / TrinityAdapter 的构造细节；
  * Fail-Safe：即使风控层完全崩溃，主流程也能降级为 HOLD 继续运行。

设计要点（来自 Phase 3 docx 规格）：
  * 全局单例：`_trinity_adapter` / `_evidence_ledger` 避免每次循环重建；
  * 配置开关：`ENABLE_RISK_GATEWAY` 控制是否启用（生产环境建议先 False 观察）；
  * `should_execute_risk_action`：信号生成后、实际下单前调用，返回 `RiskAction`。

纯增量、零耦合：仅依赖 core.adapters.trinity_adapter + core.ledger.evidence_ledger，
不 import 任何 runtime 模块。
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from core.adapters.risk_action import RiskAction, RiskActionType
from core.adapters.trinity_adapter import TrinityAdapter
from core.ledger.evidence_ledger import EvidenceLedger

logger = logging.getLogger(__name__)

# 全局单例
_trinity_adapter: Optional[TrinityAdapter] = None
_evidence_ledger: Optional[EvidenceLedger] = None


def initialize_risk_integration(
    risk_gateway: Any = None,
    ledger: Optional[EvidenceLedger] = None,
) -> TrinityAdapter:
    """系统启动时调用一次，初始化风控集成层。

    Args:
        risk_gateway: 可选，RiskGateway 实例（默认自动创建）。
        ledger: 可选，EvidenceLedger 实例（默认自动创建）。
    Returns:
        初始化后的 TrinityAdapter 实例。
    """
    global _trinity_adapter, _evidence_ledger

    _evidence_ledger = ledger or EvidenceLedger()
    _trinity_adapter = TrinityAdapter(risk_gateway=risk_gateway, ledger=_evidence_ledger)
    logger.info("[RiskIntegration] TrinityAdapter + EvidenceLedger 初始化完成")
    return _trinity_adapter


def get_trinity_adapter() -> Optional[TrinityAdapter]:
    """获取全局 TrinityAdapter 实例。"""
    return _trinity_adapter


def get_evidence_ledger() -> Optional[EvidenceLedger]:
    """获取全局 EvidenceLedger 实例（用于外部审计/查询）。"""
    return _evidence_ledger


def should_execute_risk_action(bar_data: Dict[str, Any]) -> RiskAction:
    """主流程风控钩子（信号生成后、实际下单前调用）。

    具备完整的 Fail-Safe 能力：
      * 适配器未初始化 → HOLD；
      * 适配器异常 → HOLD；
      * 正常 → 返回 RiskGateway 的决策。

    Args:
        bar_data: 当前 bar 的行情数据字典，需含 close/open/high/low/atr 等。
    Returns:
        RiskAction（绝不抛异常）。
    """
    adapter = get_trinity_adapter()
    if adapter is None:
        logger.warning("[RiskIntegration] 适配器未初始化，降级为 HOLD")
        return RiskAction(
            action_type=RiskActionType.HOLD, reason="adapter_not_initialized"
        )

    try:
        state = adapter.to_market_state(
            current_price=bar_data["close"],
            d3_low=bar_data.get("d3_low", bar_data["close"] * 0.95),
            atr=bar_data.get("atr", 2.0),
            spacetime_score=bar_data.get("spacetime_score", 0.85),
            j1_confirmed=bar_data.get("j1_confirmed", True),
            open=bar_data.get("open"),
            macro_vix=bar_data.get("vix", 22.0),
            macro_commodity_shock=bar_data.get("brent_shock", 0.0),
        )

        action = adapter.get_risk_action(
            state,
            context={
                "bar_index": bar_data.get("index"),
                "symbol": bar_data.get("symbol", "UNKNOWN"),
            },
        )
        return action

    except Exception as exc:
        logger.error(
            "[RiskIntegration] 钩子严重异常，强制降级 HOLD: %s", exc, exc_info=True
        )
        return RiskAction(
            action_type=RiskActionType.HOLD,
            reason=f"integration_exception: {exc!s}",
        )
