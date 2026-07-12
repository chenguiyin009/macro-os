"""Trinity OS v2.2.1 — 主流程适配器 (TrinityAdapter)，集成 Evidence Ledger 归因。

WHY THIS FILE EXISTS
--------------------
v2.2.1 风险网关 (`RiskGateway`) 独立于主流程——它接受 `MarketState` 并输出 `Dict`。
要把网关接入 `DecisionKernel` / `Orchestrator`，需要一层胶水代码：
  1. 将上游原始数据（OHLC + 宏观）翻译为标准 `MarketState`；
  2. 调用 `RiskGateway.process_tick`，捕获异常做 Fail-Safe；
  3. 将网关 dict 日志转换为类型安全的 `RiskAction`；
  4. （C2 增强）自动记录到 EvidenceLedger 做归因审计。

设计要点（来自 docx C1 + C2 规格）：
  * `to_market_state`：穿透 macro_vix / macro_commodity_shock / open（**kwargs 透传）；
  * `get_risk_action`：内置 Fail-Safe（异常不崩溃主流程，返回 HOLD + reason），
    成功后自动调用 `ledger.record_risk_action`；
  * `account_balance` 参数化（默认 1M，调用方可覆盖）；
  * `context` 可选参数（symbol / bar_index 等调用方附加上下文）；
  * action_map 大小写兼容。

纯增量、零耦合：仅依赖 core.risk_gateway_v2_2_1 + core.adapters.risk_action
+ core.ledger.evidence_ledger。
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from core.adapters.risk_action import RiskAction, RiskActionType
from core.ledger.evidence_ledger import EvidenceLedger
from core.risk_gateway_v2_2_1 import MarketState, RiskGateway

logger = logging.getLogger(__name__)


class TrinityAdapter:
    """Trinity OS 主流程适配器（支持 Evidence Ledger 归因）。

    职责：
    - 将原始市场数据 + 宏观特征转换为标准 MarketState
    - 调用 RiskGateway 并转换为统一 RiskAction
    - 自动归因记录到 EvidenceLedger
    - 提供 Fail-Safe 保护
    """

    def __init__(
        self,
        risk_gateway: Optional[RiskGateway] = None,
        ledger: Optional[EvidenceLedger] = None,
    ) -> None:
        self.risk_gateway = risk_gateway or RiskGateway()
        self.ledger = ledger or EvidenceLedger()

    def to_market_state(
        self,
        current_price: float,
        d3_low: float,
        atr: float,
        spacetime_score: float,
        j1_confirmed: bool,
        macro_vix: float = 22.0,
        macro_commodity_shock: float = 0.0,
        **kwargs: Any,
    ) -> MarketState:
        """将上游数据转换为标准 MarketState。

        宏观数据（macro_vix / macro_commodity_shock）在此穿透，确保 RiskGateway
        的 MacroCircuitBreaker 能直接消费。额外 kwarg（如 open）透传，支持跳空检测。
        """
        return MarketState(
            current_price=current_price,
            d3_low=d3_low,
            atr=atr,
            spacetime_score=spacetime_score,
            j1_confirmed=j1_confirmed,
            macro_vix=macro_vix,
            macro_commodity_shock=macro_commodity_shock,
            **kwargs,
        )

    def get_risk_action(
        self,
        state: MarketState,
        account_balance: float = 1_000_000.0,
        context: Optional[Dict[str, Any]] = None,
    ) -> RiskAction:
        """调用 RiskGateway 并转换为 RiskAction，自动归因到 EvidenceLedger。

        具备 Fail-Safe 能力：即使网关抛异常，也返回 HOLD 并记录异常到 Ledger，
        确保主流程不因适配器异常而崩溃。
        """
        try:
            log = self.risk_gateway.process_tick(account_balance, state)

            # 兼容 snake_case（网关实际返回）与 SCREAMING_SNAKE_CASE（docx 规格）
            raw_action = log.get("action", "hold")
            action_map: Dict[str, RiskActionType] = {
                "initial_entry": RiskActionType.INITIAL_ENTRY,
                "pyramid_add": RiskActionType.PYRAMID_ADD,
                "trailing_exit": RiskActionType.TRAILING_EXIT,
                "fatal_gap_liquidation": RiskActionType.FATAL_GAP_LIQUIDATION,
                "FATAL_GAP_LIQUIDATION": RiskActionType.FATAL_GAP_LIQUIDATION,
                "hold": RiskActionType.HOLD,
            }
            action_type = action_map.get(raw_action, RiskActionType.HOLD)

            action = RiskAction(
                action_type=action_type,
                reason=log.get("reason", ""),
                size=self._extract_size(log),
                execution_price=state.current_price,
                breached_stop=log.get("breached_stop"),
                gap_price=log.get("gap_price"),
                position=log.get("position"),
                metadata=log,
            )

            # 自动归因记录
            self.ledger.record_risk_action(action, context=context)
            return action

        except Exception as exc:
            logger.error(
                "TrinityAdapter RiskGateway 调用异常: %s", exc, exc_info=True
            )
            # Fail-Safe：异常时返回 HOLD 并记录到 Ledger，避免主流程崩溃
            hold_action = RiskAction(
                action_type=RiskActionType.HOLD,
                reason=f"adapter_exception: {exc!s}",
                metadata={"exception": str(exc)},
            )
            self.ledger.record_risk_action(hold_action, context=context)
            return hold_action

    def _extract_size(self, log: Dict[str, Any]) -> float:
        """从网关日志中提取 size，兼容不同返回结构。

        - initial_entry / pyramid_add → position.size
        - trailing_exit → exit_size
        - fatal_gap_liquidation → total_closed_size（全平后无 position 对象）
        """
        # 1. 尝试从 position 对象获取 size
        if "position" in log and log["position"] is not None:
            size = getattr(log["position"], "size", 0)
            if size and size > 0:
                return float(size)

        # 2. trailing_exit 场景（log 中直接有 exit_size）
        if "exit_size" in log and log.get("exit_size"):
            return float(log["exit_size"])

        # 3. FATAL_GAP_LIQUIDATION 全平场景
        if "total_closed_size" in log and log.get("total_closed_size"):
            return float(log["total_closed_size"])

        return 0.0
