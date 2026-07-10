"""Trinity OS v2.1 - 核心数据结构

抛弃 Boolean，全面引入 Score（标量得分）和 Evidence（证据链）。
所有决策必须携带可审计的证据，而非仅仅输出买卖动作。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ========== 枚举定义 ==========

class TradingLevel(Enum):
    """级别嵌套: J+2 → J → J-1 → J-2"""
    J_PLUS_2 = "J+2"
    J = "J"
    J_MINUS_1 = "J-1"
    J_MINUS_2 = "J-2"


class MacroState(Enum):
    """MACD 拓扑六大状态

    定义来源: 《三位一体时空要素和结构要素》对应表
    1. 低位金叉后 DIF 首穿零轴 = 中偏强
    2. DIF 首穿零轴 → DEA 首穿零轴 = 极强
    3. DEA 首穿零轴 → 高位死叉 = 强
    4. 高位死叉 → DIF 首穿零轴 = 中偏弱
    5. DIF 首穿零轴 → DEA 首穿零轴 = 极弱
    6. DEA 首穿零轴 → 低位金叉 = 弱
    """
    EXTREME_STRONG = "极强"
    STRONG = "强"
    MODERATE_STRONG = "中偏强"
    MODERATE_WEAK = "中偏弱"
    WEAK = "弱"
    EXTREME_WEAK = "极弱"

    @property
    def is_extreme(self) -> bool:
        """是否为极端状态（极强/极弱）"""
        return self in (MacroState.EXTREME_STRONG, MacroState.EXTREME_WEAK)

    @property
    def is_bullish(self) -> bool:
        """是否偏多"""
        return self in (MacroState.EXTREME_STRONG, MacroState.STRONG, MacroState.MODERATE_STRONG)


class StructureType(Enum):
    """ABCD 四型结构

    A: 五段式 (第三浪为主升/跌浪)
    B: 双平台式 (九段, 十个拐点, 双平台不重叠)
    C: 单平台式
    D: 三段式
    """
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    UNKNOWN = "UNKNOWN"


class TrendDirection(Enum):
    """趋势方向"""
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class ActionType(Enum):
    """决策动作类型"""
    STRONG_ADD = "STRONG_ADD"                # 强力加仓
    ADD_ON_PULLBACK = "ADD_ON_PULLBACK"      # 回调加仓
    SCOUT_WITH_STOP = "SCOUT_WITH_STOP"      # 试仓带止损
    BUY_CAUTIOUSLY = "BUY_CAUTIOUSLY"        # 谨慎买入
    SELL_ON_BOUNCE = "SELL_ON_BOUNCE"        # 反弹卖出
    STRONG_REDUCE = "STRONG_REDUCE"          # 强力减仓
    REDUCE_CAUTIOUSLY = "REDUCE_CAUTIOUSLY"  # 谨慎减仓
    HOLD = "HOLD"                            # 持有/观望
    TAKE_PROFIT_T = "TAKE_PROFIT_T"          # 做T止盈
    STOP_LOSS = "STOP_LOSS"                  # 止损


# ========== OHLCV 数据契约 ==========

@dataclass(frozen=True)
class OHLCV:
    """标准 K 线契约 - 数据接入层的统一输出"""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


# ========== 评分与证据 ==========

@dataclass
class SpacetimeScore:
    """时空充分性评分

    time_score:  时间对称度 [0, 1], 周线两段下跌时间偏差越小越高
    space_score: 空间完整度 [0, 1], 周线下跌含日线D结构则高
    time_evidence:  时间对称的证据
    space_evidence: 空间完整的证据
    """
    time_score: float = 0.0
    space_score: float = 0.0
    time_evidence: list[str] = field(default_factory=list)
    space_evidence: list[str] = field(default_factory=list)

    @property
    def overall(self) -> float:
        """时空综合得分 (蓝图 v2.1: Time*0.4 + Space*0.6, 空间权重更高)"""
        return self.time_score * 0.4 + self.space_score * 0.6

    @property
    def sufficient(self) -> bool:
        """时空是否充分 (综合 >= 0.7)"""
        return self.overall >= 0.7

    @property
    def space_sufficient_for_strong_add(self) -> bool:
        """空间分是否满足 STRONG_ADD 条件 (蓝图 v2.1: Space Score > 0.75)"""
        return self.space_score > 0.75


@dataclass
class StructureEvidence:
    """结构证据"""
    structure_type: StructureType = StructureType.UNKNOWN
    direction: TrendDirection = TrendDirection.FLAT
    pivots: list[float] = field(default_factory=list)   # 关键转折点价格
    segments: int = 0                                     # 结构段数
    nodes: list[str] = field(default_factory=list)       # D1/D2/D3 等节点标记
    evidence: list[str] = field(default_factory=list)
    tier: str = ""                                        # 形态层级 (T1/T2/T3), 量化结构完整度


# ========== 级别上下文 ==========

@dataclass
class JLevelContext:
    """单级别上下文 - 携带该级别完整状态与证据

    这是 Trinity Kernel 的核心数据结构，每一级 (J+2/J/J-1/J-2) 都有一个实例。
    所有判定结果附带 evidence 证据链，供 Event Ledger 审计回溯。
    """
    level: TradingLevel
    symbol: str = ""
    state: MacroState = MacroState.MODERATE_STRONG
    state_evidence: list[str] = field(default_factory=list)
    structure: StructureEvidence = field(default_factory=StructureEvidence)
    ma55: float = 0.0
    ma233: float = 0.0
    price: float = 0.0
    # 布林带 (J-1 回抽确认用)
    boll_upper: float = 0.0
    boll_mid: float = 0.0
    boll_lower: float = 0.0
    # MACD 原始值
    dif: float = 0.0
    dea: float = 0.0
    macd_hist: float = 0.0

    @property
    def above_ma55(self) -> bool:
        """价格是否在 55 线之上"""
        return self.price > self.ma55 > 0

    @property
    def above_ma233(self) -> bool:
        """价格是否在 233 线之上"""
        return self.price > self.ma233 > 0

    @property
    def bollinger_pullback_confirmed(self) -> bool:
        """J-1 回抽布林带中轨是否确认支撑

        回抽中轨后反弹: 价格曾跌破/接近中轨后回升
        """
        if self.boll_mid <= 0:
            return False
        # 价格在中轨附近 (±2% 容差) 或从中轨反弹
        tolerance = self.boll_mid * 0.02
        near_mid = abs(self.price - self.boll_mid) <= tolerance
        above_mid = self.price > self.boll_mid
        return near_mid or above_mid

    def to_dict(self) -> dict:
        """序列化为可审计字典"""
        return {
            "level": self.level.value,
            "symbol": self.symbol,
            "state": self.state.value,
            "state_evidence": self.state_evidence,
            "structure": self.structure.structure_type.value,
            "structure_direction": self.structure.direction.value,
            "ma55": round(self.ma55, 4),
            "ma233": round(self.ma233, 4),
            "price": round(self.price, 4),
            "boll_mid": round(self.boll_mid, 4),
            "dif": round(self.dif, 6),
            "dea": round(self.dea, 6),
            "macd_hist": round(self.macd_hist, 6),
        }


@dataclass
class Decision:
    """决策输出 - 决策路由器的最终产物

    action:       动作类型
    confidence:   置信度 [0, 1]
    spacetime:    时空评分
    risk_level:   风险等级 [0, 1], 越高越危险
    level:        触发级别
    evidence:     完整证据链 (为什么做这个决定)
    note:         操作备注
    """
    action: ActionType
    confidence: float = 0.0
    spacetime: SpacetimeScore = field(default_factory=SpacetimeScore)
    risk_level: float = 0.0
    level: TradingLevel = TradingLevel.J
    evidence: list[str] = field(default_factory=list)
    note: str = ""
    symbol: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "confidence": round(self.confidence, 4),
            "spacetime_overall": round(self.spacetime.overall, 4),
            "risk_level": round(self.risk_level, 4),
            "level": self.level.value,
            "evidence": self.evidence,
            "note": self.note,
            "symbol": self.symbol,
        }
