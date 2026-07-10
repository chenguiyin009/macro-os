"""Trinity OS v2.1 - 三位一体量化内核

核心模块:
  - indicators:        指标引擎 (MACD / MA / Bollinger)
  - state_machine:     MACD 拓扑六态状态机
  - structure_parser:  ABCD 结构识别 + ZigZag 转折点
  - spacetime_engine:  时空对称度与完整度评分
  - decision_router:   状态×结构决策矩阵
  - gateway:           数据接入层 (OHLCV 契约)
"""
from trinity.context import (
    ActionType,
    Decision,
    JLevelContext,
    MacroState,
    OHLCV,
    SpacetimeScore,
    StructureEvidence,
    StructureType,
    TradingLevel,
    TrendDirection,
)

__version__ = "2.1.0"
__all__ = [
    "ActionType",
    "Decision",
    "JLevelContext",
    "MacroState",
    "OHLCV",
    "SpacetimeScore",
    "StructureEvidence",
    "StructureType",
    "TradingLevel",
    "TrendDirection",
]
