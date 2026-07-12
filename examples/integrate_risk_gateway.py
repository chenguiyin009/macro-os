"""Trinity OS v2.2.1 — 最小可运行集成示例 (DecisionKernel)。

本文件演示如何在决策内核中安全接入 TrinityAdapter + RiskGateway，
不依赖真实行情数据，可直接运行验证。

运行方式：
    python examples/integrate_risk_gateway.py
"""
from __future__ import annotations

import logging

from core.adapters.risk_action import RiskActionType
from core.adapters.trinity_adapter import TrinityAdapter
from core.risk_gateway_v2_2_1 import RiskGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class DecisionKernel:
    """简化版决策内核示例——演示如何安全接入风控网关。"""

    def __init__(self) -> None:
        self.risk_adapter = TrinityAdapter(RiskGateway())

    def on_new_bar(self, bar_data: dict) -> None:
        """每根K线调用一次。这是主流程中植入的钩子示例。"""
        try:
            # 1. 构建 MarketState（宏观数据在此穿透）
            state = self.risk_adapter.to_market_state(
                current_price=bar_data["close"],
                d3_low=bar_data.get("d3_low", bar_data["close"] * 0.95),
                atr=bar_data.get("atr", 2.0),
                spacetime_score=bar_data.get("spacetime_score", 0.85),
                j1_confirmed=bar_data.get("j1_confirmed", True),
                macro_vix=bar_data.get("vix", 22.0),
                macro_commodity_shock=bar_data.get("brent_shock", 0.0),
            )

            # 2. 获取风控动作（带 Fail-Safe）
            risk_action = self.risk_adapter.get_risk_action(state)

            # 3. 根据动作类型执行决策
            self._handle_risk_action(risk_action)

        except Exception as e:
            # 最终防御层：即使适配器外层也异常，主流程仍能继续
            logger.critical(
                "DecisionKernel 严重异常，已安全降级: %s", e, exc_info=True
            )

    def _handle_risk_action(self, action) -> None:
        at = action.action_type
        if at == RiskActionType.FATAL_GAP_LIQUIDATION:
            logger.warning(
                "【强平】跳空击穿止损！价格: %s, 止损位: %s",
                action.gap_price,
                action.breached_stop,
            )
        elif at == RiskActionType.INITIAL_ENTRY:
            logger.info("【入场】仓位大小: %s", action.size)
        elif at == RiskActionType.PYRAMID_ADD:
            logger.info("【加仓】仓位大小: %s", action.size)
        elif at == RiskActionType.TRAILING_EXIT:
            logger.info("【减仓】减仓数量: %s", action.size)
        # HOLD: 无操作


# ==================== 使用示例 ====================
if __name__ == "__main__":
    kernel = DecisionKernel()

    # 模拟一根K线数据（包含宏观特征）
    bar = {
        "close": 102.5,
        "atr": 2.8,
        "spacetime_score": 0.91,
        "j1_confirmed": True,
        "vix": 28.5,
        "brent_shock": 0.12,
    }
    kernel.on_new_bar(bar)
    logger.info("集成示例运行完成。")
