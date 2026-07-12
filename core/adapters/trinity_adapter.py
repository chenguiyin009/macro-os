"""Trinity OS v2.2.1 — 主流程适配器 (TrinityAdapter)。

WHY THIS FILE EXISTS
--------------------
v2.2.1 风险网关 (`RiskGateway`) 独立于主流程——它接受 `MarketState` 并输出 `Dict`。
要把网关接入 `DecisionKernel` / `Orchestrator`，需要一层胶水代码：
  1. 将上游原始数据（OHLC + 宏观）翻译为标准 `MarketState`；
  2. 调用 `RiskGateway.process_tick`，捕获异常做 Fail-Safe；
  3. 将网关 dict 日志转换为类型安全的 `RiskAction`。

设计要点（来自 docx 规格）：
  * `to_market_state`：穿透 macro_vix / macro_commodity_shock / open，确保宏观与
    跳空数据直达网关；
  * `get_risk_action`：内置 Fail-Safe（异常不崩溃主流程，返回 HOLD + reason）；
  * `account_balance` 参数化（默认 1M，调用方可覆盖）；
  * action_map 大小写兼容（网关用 snake_case，docx 用 SCREAMING_SNAKE_CASE 做 key）。

纯增量、零耦合：仅依赖 core.risk_gateway_v2_2_1 + core.adapters.risk_action。
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from core.adapters.risk_action import RiskAction, RiskActionType
from core.risk_gateway_v2_2_1 import MarketState, RiskGateway

logger = logging.getLogger(__name__)


class TrinityAdapter:
    """Trinity OS 主流程适配器。

    职责：
    - 将原始市场数据 + 宏观特征转换为标准 MarketState
    - 调用 RiskGateway 并转换为统一 RiskAction
    - 提供 Fail-Safe 保护
    """

    def __init__(self, risk_gateway: Optional[RiskGateway] = None) -> None:
        self.risk_gateway = risk_gateway or RiskGateway()

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
        self, state: MarketState, account_balance: float = 1_000_000.0
    ) -> RiskAction:
        """调用 RiskGateway 并转换为 RiskAction，具备 Fail-Safe 能力。"""
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

            pos = log.get("position")
            return RiskAction(
                action_type=action_type,
                reason=log.get("reason", ""),
                size=getattr(pos, "size", 0.0) if pos is not None else log.get("total_closed_size", 0.0),
                execution_price=state.current_price,
                breached_stop=log.get("breached_stop"),
                gap_price=log.get("gap_price"),
                position=pos,
                metadata=log,
            )

        except Exception as exc:
            logger.error(
                "TrinityAdapter RiskGateway 调用异常: %s", exc, exc_info=True
            )
            # Fail-Safe：异常时返回 HOLD，避免主流程崩溃
            return RiskAction(
                action_type=RiskActionType.HOLD,
                reason=f"adapter_exception: {exc!s}",
                metadata={"exception": str(exc)},
            )
