"""Trinity OS v2.1 - 核心引擎

将《三位一体》核心逻辑与工程架构集成的最终实战版本。
包含状态机引擎、时空共振量化逻辑、事件溯源与防失忆机制以及决策路由网关。

这是 Trinity OS v2.1 研发的基石模块。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ================================================================
# 1. 核心状态与契约定义
# ================================================================


class State(Enum):
    """MACD 拓扑七大状态

    六态循环 + 混沌态:
      极强 → 强 → 中偏强 → 中偏弱 → 弱 → 极弱 → (循环)
      混沌: 当信号矛盾或数据不足时, 进入混沌态

    定义来源: 《三位一体时空要素和结构要素》对应表
    """
    EXTREME_BULL = "极强"
    BULL = "强"
    MID_BULL = "中偏强"
    MID_BEAR = "中偏弱"
    BEAR = "弱"
    EXTREME_BEAR = "极弱"
    NEUTRAL = "混沌"

    @property
    def is_extreme(self) -> bool:
        return self in (State.EXTREME_BULL, State.EXTREME_BEAR)

    @property
    def is_bullish(self) -> bool:
        return self in (State.EXTREME_BULL, State.BULL, State.MID_BULL)

    @property
    def is_clear(self) -> bool:
        """非混沌态, 信号明确"""
        return self != State.NEUTRAL


# 引擎状态别名 (供外部使用)
EngineState = State


@dataclass
class SpacetimeScore:
    """时空综合得分

    time_score:   时间对称度 [0, 1]
    space_score:  空间完整度 [0, 1]
    total_score:  时空综合 [0, 1]
    reference:    人可读的参考描述
    details:      详细数据
    """
    time_score: float
    space_score: float
    total_score: float
    reference: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    """决策输出

    action:        动作 (STRONG_ADD / HOLD / STRONG_REDUCE 等)
    confidence:    置信度 [0, 1]
    reasons:       决策理由 (证据链)
    spacetime_score: 时空评分 (可为 None)
    risk_level:    风险等级 [0, 1]
    timestamp:     决策时间
    """
    action: str
    confidence: float
    reasons: List[str]
    spacetime_score: Optional[SpacetimeScore]
    risk_level: float
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class JLevelContext:
    """级别嵌套上下文

    携带 J+2(周线) / J(日线) 级别的状态与关键指标,
    以及 J-1(60分钟) 的背离与回抽确认信号。
    """
    symbol: str
    timestamp: datetime
    state_j2: State            # 周线级别状态
    state_j: State             # 日线级别状态
    j_minus_1_has_divergence: bool   # J-1 是否存在背离
    j_close: float             # J 级别收盘价
    j_ma55: float              # J 级别 55 均线
    j_has_completed_ma55_pullback: bool  # J 是否完成 55 线回抽
    price: float               # 当前价格

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["state_j2"] = self.state_j2.value
        d["state_j"] = self.state_j.value
        return d


# ================================================================
# 2. 核心量化内核 (Logic Engine)
# ================================================================


class StructureParser:
    """形态解析器：将 K 线转化为 D 型结构骨架

    识别 H-L-H-L 序列, 判定是否包含完整 D1-D2-D3 节点。
    结构必完美: 周线下跌须含日线 D 结构。
    """

    def evaluate_d_structure(self, pivots: List[Dict]) -> Tuple[float, str]:
        """评估 D 型结构完整度

        Args:
            pivots: 转折点列表 [{"price": float, "is_high": bool, "index": int}, ...]

        Returns:
            (score [0,1], description)
        """
        if len(pivots) < 4:
            return 0.25, "结构不足"

        # 检查高低交替
        alternating = all(
            pivots[i]["is_high"] != pivots[i - 1]["is_high"]
            for i in range(1, min(len(pivots), 4))
        )
        if not alternating:
            return 0.3, "转折点非交替, 结构不完美"

        # D 结构: D1(高) → D2(低) → D3(高, 低于D1) → D4(低, 破D2)
        d1 = pivots[0].get("price", 0)
        d2 = pivots[1].get("price", 0)
        d3 = pivots[2].get("price", 0) if len(pivots) > 2 else 0
        d4 = pivots[3].get("price", 0) if len(pivots) > 3 else 0

        # 结构必完美: D3 < D1 且 D4 < D2 (下跌 D 结构破位)
        if pivots[0].get("is_high", True):
            if d3 < d1 and d4 < d2:
                return 0.95, "D1-D2-D3 完整, D4破位确认 (结构必完美)"
            elif d3 < d1:
                return 0.85, "D1-D2-D3 完整"
            else:
                return 0.6, "D3未破D1, 结构待确认"
        else:
            # 上涨 D 结构: D1(低) → D2(高) → D3(低, 高于D1) → D4(高, 破D2)
            if d3 > d1 and d4 > d2:
                return 0.95, "D1-D2-D3 完整, D4破位确认 (结构必完美)"
            elif d3 > d1:
                return 0.85, "D1-D2-D3 完整"
            else:
                return 0.6, "D3未破D1, 结构待确认"


def calculate_spacetime(
    ctx: JLevelContext,
    structure_score: float,
    duration_curr: int,
    duration_ref: int,
) -> SpacetimeScore:
    """计算时空综合得分 (Score Based)

    时间得分: 当前下跌段与参考段的时间对称度
    空间得分: 结构完整度 (来自 StructureParser)
    综合: 时间与空间的加权平均

    Args:
        ctx:             级别上下文
        structure_score: 空间结构得分 (来自 evaluate_d_structure)
        duration_curr:   当前下跌段持续时长 (周)
        duration_ref:    ��考下跌段持续时长 (周)
    """
    # 时间对称度: 偏差越小得分越高
    if max(duration_curr, duration_ref) > 0:
        time_score = max(0.0, 1.0 - abs(duration_curr - duration_ref) / max(duration_curr, duration_ref))
    else:
        time_score = 0.0

    space_score = structure_score

    # 综合得分: 蓝图 v2.1 公式 Time*0.4 + Space*0.6 (空间权重更高)
    total = round(time_score * 0.4 + space_score * 0.6, 4)

    # 参考描述
    deviation = abs(duration_curr - duration_ref)
    reference = (
        f"当前{duration_curr}周 vs 参考{duration_ref}周, "
        f"时间偏差{deviation}周, 结构得分{structure_score:.2f}"
    )

    return SpacetimeScore(
        time_score=round(time_score, 4),
        space_score=round(space_score, 4),
        total_score=total,
        reference=reference,
        details={
            "duration_curr": duration_curr,
            "duration_ref": duration_ref,
            "time_deviation": deviation,
            "structure_score": structure_score,
            "symbol": ctx.symbol,
        },
    )


# ================================================================
# 3. 状态机引擎 (StateMachineEngine)
# ================================================================


class StateMachineEngine:
    """MACD 拓扑状态机引擎

    基于 MACD 与零轴的拓扑关系, 定义七大状态循环:
      极强 → 强 → 中偏强 → 中偏弱 → 弱 → 极弱 → (循环)
      混沌: 信号矛盾时进入

    状态转换由 MACD 关键事件驱动:
      - 低位金叉 (LOW_GOLDEN_CROSS)
      - DIF 上/下穿零轴 (DIF_CROSS_UP/DOWN)
      - DEA 上/下穿零轴 (DEA_CROSS_UP/DOWN)
      - 高位死叉 (HIGH_DEATH_CROSS)
    """

    # 状态转换表: (当前状态, 事件) → 新状态
    TRANSITIONS: Dict[Tuple[State, str], State] = {
        (State.BEAR, "LOW_GOLDEN_CROSS"): State.MID_BULL,
        (State.MID_BULL, "DIF_CROSS_UP"): State.EXTREME_BULL,
        (State.EXTREME_BULL, "DEA_CROSS_UP"): State.BULL,
        (State.BULL, "HIGH_DEATH_CROSS"): State.MID_BEAR,
        (State.MID_BEAR, "DIF_CROSS_DOWN"): State.EXTREME_BEAR,
        (State.EXTREME_BEAR, "DEA_CROSS_DOWN"): State.BEAR,
    }

    # 置信度基准
    STATE_CONFIDENCE: Dict[State, float] = {
        State.EXTREME_BULL: 0.9,
        State.BULL: 0.75,
        State.MID_BULL: 0.55,
        State.NEUTRAL: 0.3,
        State.MID_BEAR: 0.45,
        State.BEAR: 0.3,
        State.EXTREME_BEAR: 0.2,
    }

    def determine_state(
        self,
        dif: float,
        dea: float,
        prev_dif: Optional[float] = None,
        prev_dea: Optional[float] = None,
    ) -> Tuple[State, List[str]]:
        """根据 MACD 值判定当前状态

        优先使用事件驱动 (需要 prev 值), 否则用瞬时值判定。

        Returns:
            (state, evidence)
        """
        evidence: list[str] = []
        evidence.append(f"DIF={dif:.6f}, DEA={dea:.6f}")

        # 尝试事件驱动
        if prev_dif is not None and prev_dea is not None:
            event = self._detect_event(prev_dif, prev_dea, dif, dea)
            if event:
                evidence.append(f"事件: {event}")

        # 瞬时状态判定
        state = self._instantaneous_state(dif, dea)

        if state.is_extreme:
            evidence.append("极端状态: 忽略顶/底背离, 55线重要性大幅上升")
        elif state == State.NEUTRAL:
            evidence.append("混沌态: 信号不明确, 建议观望")

        evidence.append(f"状态判定: {state.value}")
        return state, evidence

    def _instantaneous_state(self, dif: float, dea: float) -> State:
        """瞬时状态判定 (无历史时的降级方案)"""
        if dif == 0 and dea == 0:
            return State.NEUTRAL
        if dif > 0 and dea < 0:
            return State.EXTREME_BULL
        if dif < 0 and dea > 0:
            return State.EXTREME_BEAR
        if dif > 0 and dea > 0:
            return State.BULL if dif > dea else State.MID_BEAR
        if dif < 0 and dea < 0:
            return State.MID_BULL if dif > dea else State.BEAR
        return State.NEUTRAL

    def _detect_event(
        self, prev_dif: float, prev_dea: float, cur_dif: float, cur_dea: float
    ) -> Optional[str]:
        """检测 MACD 关键事件"""
        if prev_dif <= prev_dea and cur_dif > cur_dea:
            return "LOW_GOLDEN_CROSS" if cur_dif < 0 and cur_dea < 0 else "GOLDEN_CROSS"
        if prev_dif >= prev_dea and cur_dif < cur_dea:
            return "HIGH_DEATH_CROSS" if cur_dif > 0 and cur_dea > 0 else "DEATH_CROSS"
        if prev_dif <= 0 and cur_dif > 0:
            return "DIF_CROSS_UP"
        if prev_dif >= 0 and cur_dif < 0:
            return "DIF_CROSS_DOWN"
        if prev_dea <= 0 and cur_dea > 0:
            return "DEA_CROSS_UP"
        if prev_dea >= 0 and cur_dea < 0:
            return "DEA_CROSS_DOWN"
        return None

    def run_sequence(
        self,
        dif_series: List[float],
        dea_series: List[float],
    ) -> List[State]:
        """对 MACD 序列运行状态机, 返回每根 K 线的状态"""
        if not dif_series:
            return []
        states: list[State] = []
        current = State.NEUTRAL
        for i in range(len(dif_series)):
            if i == 0:
                current = self._instantaneous_state(dif_series[i], dea_series[i])
            else:
                event = self._detect_event(
                    dif_series[i - 1], dea_series[i - 1], dif_series[i], dea_series[i]
                )
                if event:
                    key = (current, event)
                    if key in self.TRANSITIONS:
                        current = self.TRANSITIONS[key]
                    else:
                        current = self._instantaneous_state(dif_series[i], dea_series[i])
            states.append(current)
        return states


# ================================================================
# 3.5 结构化证据归因引擎 (AttributionEngine)
# ================================================================


@dataclass
class EvidenceFactor:
    """结构化证据因子 (蓝图 v2.1 §4.2)

    不再仅记录"买入", 而是记录"为什么买"——每个因子携带:
      module:       来源级别 (J+2 / J / J-1 / J-2)
      factor:       指标名称 (MACD_HIST / MA55 / BOLL_MID / STATE / STRUCTURE ...)
      value:        原始值
      weight:       该因子在决策中的权重 [0, 1]
      contribution: value * weight, 该因子对决策的贡献度
    """
    module: str
    factor: str
    value: float
    weight: float
    contribution: float = 0.0

    def __post_init__(self):
        if self.contribution == 0.0:
            self.contribution = round(self.value * self.weight, 4)

    def to_dict(self) -> dict:
        return asdict(self)


class AttributionEngine:
    """归因引擎 (蓝图 v2.1 §4.2)

    收集决策的结构化证据因子, 统计哪个因子对 Alpha 贡献最大。
    核心能力:
      1. collect: 从决策过程中提取结构化证据因子
      2. attribute: 统计各因子的平均贡献度
      3. rank: 按贡献度排序, 找出最关键的决策因子
    """

    def __init__(self):
        self._factors: List[List[EvidenceFactor]] = []  # 每次决策一组因子
        self._outcomes: List[Optional[float]] = []       # 对应的收益结果 (正=盈, 负=亏)

    def collect(
        self,
        ctx: JLevelContext,
        spacetime: Optional[SpacetimeScore] = None,
        structure_score: float = 0.0,
        direction: str = "UP",
    ) -> List[EvidenceFactor]:
        """从决策上下文提取结构化证据因子"""
        factors: list[EvidenceFactor] = []

        # J+2 状态因子
        state_weight = StateMachineEngine.STATE_CONFIDENCE.get(ctx.state_j2, 0.3)
        factors.append(EvidenceFactor(
            module="J+2", factor="STATE",
            value=state_weight, weight=0.3,
        ))

        # J 结构因子
        struct_value = structure_score if direction == "UP" else 1.0 - structure_score
        factors.append(EvidenceFactor(
            module="J", factor="STRUCTURE",
            value=struct_value, weight=0.25,
        ))

        # J-1 回抽确认因子
        pullback_value = 1.0 if ctx.j_has_completed_ma55_pullback else 0.0
        factors.append(EvidenceFactor(
            module="J-1", factor="MA55_PULLBACK",
            value=pullback_value, weight=0.2,
        ))

        # J-1 背离因子 (有背离 = 负面信号)
        divergence_value = 0.0 if ctx.j_minus_1_has_divergence else 1.0
        factors.append(EvidenceFactor(
            module="J-1", factor="DIVERGENCE",
            value=divergence_value, weight=0.15,
        ))

        # 时空综合因子
        st_value = spacetime.total_score if spacetime else 0.0
        factors.append(EvidenceFactor(
            module="SPACETIME", factor="TOTAL_SCORE",
            value=st_value, weight=0.1,
        ))

        self._factors.append(factors)
        self._outcomes.append(None)
        return factors

    def attribute(self) -> Dict[str, Dict[str, float]]:
        """统计各因子的归因数据

        Returns:
            {factor_name: {"avg_contribution": float, "count": int, "total_weight": float}}
        """
        stats: dict[str, dict[str, float]] = {}
        for factors in self._factors:
            for f in factors:
                key = f.factor
                if key not in stats:
                    stats[key] = {"avg_contribution": 0.0, "count": 0, "total_contribution": 0.0}
                stats[key]["count"] += 1
                stats[key]["total_contribution"] += f.contribution

        for key in stats:
            cnt = stats[key]["count"]
            stats[key]["avg_contribution"] = round(stats[key]["total_contribution"] / cnt, 4) if cnt > 0 else 0.0

        return stats

    def rank(self) -> List[Tuple[str, float]]:
        """按平均贡献度排序因子 (降序)"""
        stats = self.attribute()
        ranked = sorted(
            [(k, v["avg_contribution"]) for k, v in stats.items()],
            key=lambda x: x[1], reverse=True,
        )
        return ranked

    def attribute_by_outcome(self) -> Dict[str, Dict[str, float]]:
        """按盈亏结果归因: 哪些因子在盈利决策中贡献最大

        Returns:
            {"profitable": {factor: avg_contrib}, "unprofitable": {factor: avg_contrib}}
        """
        profitable: dict[str, list[float]] = {}
        unprofitable: dict[str, list[float]] = {}

        for factors, outcome in zip(self._factors, self._outcomes):
            if outcome is None:
                continue
            target = profitable if outcome > 0 else unprofitable
            for f in factors:
                target.setdefault(f.factor, []).append(f.contribution)

        result: dict[str, dict[str, float]] = {"profitable": {}, "unprofitable": {}}
        for factor, contribs in profitable.items():
            result["profitable"][factor] = round(sum(contribs) / len(contribs), 4)
        for factor, contribs in unprofitable.items():
            result["unprofitable"][factor] = round(sum(contribs) / len(contribs), 4)
        return result

    def update_outcome(self, index: int, outcome: float) -> bool:
        """回填决策收益结果 (用于归因统计)"""
        if 0 <= index < len(self._outcomes):
            self._outcomes[index] = outcome
            return True
        return False

    def to_dict(self) -> dict:
        """序列化全部归因数据"""
        return {
            "factors": [[f.to_dict() for f in group] for group in self._factors],
            "outcomes": self._outcomes,
            "attribution": self.attribute(),
            "ranking": self.rank(),
        }

    def save(self, filepath: str) -> None:
        """持久化归因数据到 JSON"""
        data = {
            "version": "2.1.0",
            "module": "AttributionEngine",
            "saved_at": datetime.now().isoformat(),
            "factors": [[f.to_dict() for f in group] for group in self._factors],
            "outcomes": self._outcomes,
        }
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def load(self, filepath: str) -> None:
        """从 JSON 加载归因数据"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._factors = [
            [EvidenceFactor(**fd) for fd in group]
            for group in data.get("factors", [])
        ]
        self._outcomes = data.get("outcomes", [])

    @property
    def count(self) -> int:
        return len(self._factors)


# ================================================================
# 4. 事件溯源与防失忆机制 (AntiAmnesiaTracker)
# ================================================================


@dataclass
class MemoryEvent:
    """记忆事件 - 防失忆账本的单条记录

    记录完整的决策推理路径, 而非仅仅记录买卖动作。
    防失忆: 当相似情境再现时, 可回溯历史决策。
    """
    event_id: str
    timestamp: datetime
    symbol: str
    context_snapshot: Dict[str, Any]     # 上下文快照
    decision: Dict[str, Any]             # 决策结果
    reasons: List[str]                   # 证据链
    spacetime_score: Optional[Dict[str, Any]]  # 时空评分
    outcome: Optional[str] = None        # 事后结果 (盈亏/对错, 可后续回填)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEvent":
        if "timestamp" in d and isinstance(d["timestamp"], str):
            d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        return cls(**d)


class AntiAmnesiaTracker:
    """事件溯源与防失忆追踪器

    核心能力:
      1. 记录 (record): 将每次决策的完整推理路径写入账本
      2. 回放 (replay): 按时间线回放所有决策
      3. 回忆 (recall): 根据当前上下文匹配相似历史决策 (防失忆)
      4. 持久化 (save/load): JSON 存储, 跨会话保留记忆
    """

    def __init__(self):
        self._memories: List[MemoryEvent] = []

    def record(
        self,
        ctx: JLevelContext,
        decision: Decision,
        metadata: Optional[Dict] = None,
    ) -> MemoryEvent:
        """记录一次决策事件 (写入记忆)"""
        event = MemoryEvent(
            event_id=f"MEM-{uuid.uuid4().hex[:8].upper()}",
            timestamp=decision.timestamp,
            symbol=ctx.symbol,
            context_snapshot=ctx.to_dict(),
            decision=decision.to_dict(),
            reasons=decision.reasons.copy(),
            spacetime_score=decision.spacetime_score.to_dict() if decision.spacetime_score else None,
            metadata=metadata or {},
        )
        self._memories.append(event)
        return event

    def replay(self) -> List[MemoryEvent]:
        """回放所有记忆"""
        return list(self._memories)

    def recall(
        self,
        symbol: Optional[str] = None,
        state_j2: Optional[State] = None,
        state_j: Optional[State] = None,
        action: Optional[str] = None,
        limit: int = 5,
    ) -> List[MemoryEvent]:
        """回忆相似情境的历史决策 (防失忆核心)

        根据当前上下文条件, 检索匹配的历史记忆。
        用于回答: "上次遇到类似情况时, 我做了什么决定?"

        Args:
            symbol:   按标的过滤
            state_j2: 按周线状态过滤
            state_j:  按日线状态过滤
            action:   按动作过滤
            limit:    最多返回条数
        """
        results = []
        for m in reversed(self._memories):
            if symbol and m.symbol != symbol:
                continue
            ctx = m.context_snapshot
            if state_j2 and ctx.get("state_j2") != state_j2.value:
                continue
            if state_j and ctx.get("state_j") != state_j.value:
                continue
            if action and m.decision.get("action") != action:
                continue
            results.append(m)
            if len(results) >= limit:
                break
        return results

    def recall_similar(
        self,
        ctx: JLevelContext,
        limit: int = 3,
    ) -> List[MemoryEvent]:
        """回忆与当前上下文最相似的历史决策

        相似度: 标的 + 周线状态 + 日线状态匹配
        """
        return self.recall(
            symbol=ctx.symbol,
            state_j2=ctx.state_j2,
            state_j=ctx.state_j,
            limit=limit,
        )

    def update_outcome(self, event_id: str, outcome: str) -> bool:
        """回填事后结果 (用于复盘评估)"""
        for m in self._memories:
            if m.event_id == event_id:
                m.outcome = outcome
                return True
        return False

    def summary(self) -> Dict[str, Any]:
        """记忆摘要"""
        actions: dict[str, int] = {}
        symbols: dict[str, int] = {}
        for m in self._memories:
            a = m.decision.get("action", "UNKNOWN")
            actions[a] = actions.get(a, 0) + 1
            symbols[m.symbol] = symbols.get(m.symbol, 0) + 1
        return {
            "total_memories": len(self._memories),
            "actions": actions,
            "symbols": symbols,
            "with_outcome": sum(1 for m in self._memories if m.outcome),
        }

    def save(self, filepath: str) -> None:
        """持久化记忆到 JSON"""
        data = {
            "version": "2.1.0",
            "module": "AntiAmnesiaTracker",
            "saved_at": datetime.now().isoformat(),
            "memories": [m.to_dict() for m in self._memories],
        }
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def load(self, filepath: str) -> None:
        """从 JSON 加载记忆"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._memories = [MemoryEvent.from_dict(m) for m in data.get("memories", [])]

    def clear(self) -> None:
        self._memories.clear()

    @property
    def count(self) -> int:
        return len(self._memories)

    def __len__(self) -> int:
        return len(self._memories)


# ================================================================
# 5. 决策路由网关 (DecisionRouterGateway)
# ================================================================


class DecisionRouterGateway:
    """决策路由网关

    基于「状态 × 结构」矩阵 + 级别嵌套, 输出最终决策。

    级别嵌套原则 (来源: 《7 Key Points》):
      J+2 做时空判断, J 判断结构, J-1 判断回抽布林带, J-2 辅助共振
      大级别压制小级别

    主涨段狙击:
      J+2 极强 + J 结构主升 + J-1 回抽布林带确认 + J-2 MA55 共振
    """

    # 决策矩阵: (J+2状态, 方向) → (action, note)
    MATRIX: Dict[Tuple[State, str], Tuple[str, str]] = {
        (State.EXTREME_BULL, "UP"):   ("STRONG_ADD", "忽略顶部背离, 逢回抽55线买入"),
        (State.EXTREME_BULL, "DOWN"): ("HOLD", "无对应下跌结构, 小心被洗走"),
        (State.BULL, "UP"):           ("ADD_ON_PULLBACK", "忽略小级别波动, 波段买入"),
        (State.BULL, "DOWN"):         ("REDUCE_CAUTIOUSLY", "卖出需谨慎, d1卖d2接"),
        (State.MID_BULL, "UP"):       ("SCOUT_WITH_STOP", "中枢底部试仓, 严格止损"),
        (State.MID_BULL, "DOWN"):     ("SCOUT_WITH_STOP", "中枢底部试仓, 严格止损"),
        (State.MID_BEAR, "UP"):       ("SCOUT_WITH_STOP", "中枢底部试仓, 严格止损"),
        (State.MID_BEAR, "DOWN"):     ("SCOUT_WITH_STOP", "中枢底部试仓, 严格止损"),
        (State.BEAR, "UP"):           ("BUY_CAUTIOUSLY", "买入需谨慎, d1买d2卖"),
        (State.BEAR, "DOWN"):         ("SELL_ON_BOUNCE", "波段卖出机会"),
        (State.EXTREME_BEAR, "UP"):   ("HOLD", "无对应上涨结构, 小心被骗线"),
        (State.EXTREME_BEAR, "DOWN"): ("STRONG_REDUCE", "忽略底部背离, 逢回抽55线卖出"),
        (State.NEUTRAL, "UP"):        ("HOLD", "混沌态, 信号不明确, 观望"),
        (State.NEUTRAL, "DOWN"):      ("HOLD", "混沌态, 信号不明确, 观望"),
    }

    def route(
        self,
        ctx: JLevelContext,
        spacetime_score: Optional[SpacetimeScore] = None,
        structure_type: str = "D",
        direction: str = "UP",
    ) -> Decision:
        """执行决策路由

        Args:
            ctx:              级别嵌套上下文
            spacetime_score:  时空评分 (抄底必查)
            structure_type:   J 级别结构类型 (A/B/C/D)
            direction:        J 级别结构方向 (UP/DOWN)
        """
        reasons: list[str] = []
        j2_state = ctx.state_j2
        reasons.append(f"[J+2] 状态={j2_state.value} (大级别时空判定)")
        reasons.append(f"[J] 状态={ctx.state_j.value}, 结构={structure_type}, 方向={direction}")
        reasons.append(f"[J-1] 背离={ctx.j_minus_1_has_divergence}, MA55回抽={ctx.j_has_completed_ma55_pullback}")

        # 决策矩阵查找 (以 J+2 状态 + J 结构方向为主)
        matrix_key = (j2_state, direction)
        action, note = self.MATRIX.get(matrix_key, ("HOLD", "无匹配, 观望"))
        reasons.append(f"决策矩阵: {j2_state.value}×{direction} → {action}")
        reasons.append(f"操作备注: {note}")

        # 主涨段狙击检查 (蓝图 v2.1 §3.3: STRONG_ADD 要求 Space Score > 0.75)
        main_surge = self._check_main_surge(ctx, direction)
        if main_surge:
            reasons.append("[主涨段狙击] J+2极强 + J上涨 + J-1无背离 + MA55回抽确认")
            # 蓝图 v2.1: STRONG_ADD 要求 Space Score > 0.75
            space_ok = (
                spacetime_score is not None and spacetime_score.space_score > 0.75
            ) or spacetime_score is None  # 无时空数据时不阻塞
            if space_ok and action != "STRONG_ADD":
                action = "STRONG_ADD"
                reasons.append("→ 升级为 STRONG_ADD (Space Score > 0.75)")
            elif not space_ok and action == "STRONG_ADD":
                action = "ADD_ON_PULLBACK"
                reasons.append("Space Score <= 0.75, 降级为 ADD_ON_PULLBACK")

        # TAKE_PROFIT_T 检查 (蓝图 v2.1 §3.3: 突破遇阻 + 小级别背离 + 动能衰减)
        if self._check_take_profit_t(ctx, direction):
            reasons.append("[做T止盈] 突破遇阻 + J-1背离 + 动能衰减")
            if action in ("STRONG_ADD", "ADD_ON_PULLBACK"):
                action = "TAKE_PROFIT_T"
                reasons.append("→ 转为 TAKE_PROFIT_T")

        # EXIT/WAIT 检查 (蓝图 v2.1 §3.3: 结构破坏或时空未达标)
        if self._check_exit_wait(ctx, direction, spacetime_score):
            reasons.append("[退出/观望] 结构破坏或时空未达标")
            if action in ("STRONG_ADD", "ADD_ON_PULLBACK", "SCOUT_WITH_STOP"):
                action = "WAIT"
                reasons.append("→ 转为 WAIT")

        # 时空充分性检查 (抄底必查)
        if spacetime_score and action in ("STRONG_ADD", "SCOUT_WITH_STOP", "BUY_CAUTIOUSLY"):
            reasons.append(
                f"[时空] 综合={spacetime_score.total_score:.2f} "
                f"(时间={spacetime_score.time_score:.2f}, 空间={spacetime_score.space_score:.2f})"
            )
            if spacetime_score.total_score < 0.5:
                if action == "STRONG_ADD":
                    action = "ADD_ON_PULLBACK"
                    reasons.append("时空不充分, 降级为 ADD_ON_PULLBACK")

        # 置信度
        confidence = self._calc_confidence(j2_state, ctx, spacetime_score, main_surge)
        reasons.append(f"[置信度] {confidence:.2f}")

        # 风险等级
        risk_level = self._calc_risk(j2_state, action, spacetime_score)
        reasons.append(f"[风险] {risk_level:.2f}")

        return Decision(
            action=action,
            confidence=confidence,
            reasons=reasons,
            spacetime_score=spacetime_score,
            risk_level=risk_level,
            timestamp=datetime.now(),
        )

    def _check_main_surge(self, ctx: JLevelContext, direction: str) -> bool:
        """主涨段狙击条件检查"""
        return (
            ctx.state_j2 == State.EXTREME_BULL
            and direction == "UP"
            and not ctx.j_minus_1_has_divergence
            and ctx.j_has_completed_ma55_pullback
        )

    def _check_take_profit_t(self, ctx: JLevelContext, direction: str) -> bool:
        """TAKE_PROFIT_T 条件检查 (蓝图 v2.1 §3.3)

        条件: 突破遇阻 + 小级别背离 + 动能衰减
        - 突破遇阻: 价格在 MA55 附近 (遇阻)
        - 小级别背离: J-1 存在背离
        - 动能衰减: J+2 非极强 (动能不再加速)
        """
        near_ma55 = abs(ctx.price - ctx.j_ma55) / max(ctx.j_ma55, 1) < 0.02 if ctx.j_ma55 > 0 else False
        has_divergence = ctx.j_minus_1_has_divergence
        momentum_fading = ctx.state_j2 != State.EXTREME_BULL
        return near_ma55 and has_divergence and momentum_fading

    def _check_exit_wait(
        self,
        ctx: JLevelContext,
        direction: str,
        spacetime: Optional[SpacetimeScore],
    ) -> bool:
        """EXIT/WAIT 条件检查 (蓝图 v2.1 §3.3)

        条件: 结构破坏 (顶背离 + 跌破 MA55) 或 时空调整未达标
        """
        # 结构破坏: J-1 背离 + 价格跌破 MA55
        structure_broken = (
            ctx.j_minus_1_has_divergence
            and ctx.j_ma55 > 0
            and ctx.price < ctx.j_ma55
        )
        # 时空未达标: 有评分但综合 < 0.5
        spacetime_failed = (
            spacetime is not None and spacetime.total_score < 0.5
        )
        return structure_broken or spacetime_failed

    def _calc_confidence(
        self,
        j2_state: State,
        ctx: JLevelContext,
        spacetime: Optional[SpacetimeScore],
        main_surge: bool,
    ) -> float:
        """计算置信度"""
        base = StateMachineEngine.STATE_CONFIDENCE.get(j2_state, 0.3)
        if ctx.j_has_completed_ma55_pullback:
            base = min(1.0, base + 0.1)
        if not ctx.j_minus_1_has_divergence:
            base = min(1.0, base + 0.05)
        if spacetime and spacetime.total_score >= 0.7:
            base = min(1.0, base + 0.1)
        if main_surge:
            base = min(1.0, base + 0.15)
        return round(base, 4)

    def _calc_risk(
        self,
        j2_state: State,
        action: str,
        spacetime: Optional[SpacetimeScore],
    ) -> float:
        """计算风险等级"""
        base = 0.3 if j2_state.is_bullish else 0.6
        if j2_state == State.NEUTRAL:
            base = 0.5
        if spacetime and spacetime.total_score < 0.5:
            base = min(1.0, base + 0.2)
        if action in ("SCOUT_WITH_STOP", "BUY_CAUTIOUSLY") and not j2_state.is_bullish:
            base = min(1.0, base + 0.15)
        if j2_state.is_extreme:
            if j2_state.is_bullish and action in ("STRONG_ADD", "ADD_ON_PULLBACK"):
                base = max(0.1, base - 0.2)
            elif not j2_state.is_bullish and action == "STRONG_REDUCE":
                base = max(0.1, base - 0.2)
        return round(base, 4)


# ================================================================
# 6. 引擎主体 (TrinityEngine)
# ================================================================


class TrinityEngine:
    """Trinity 核心引擎主体

    串联: StructureParser → StateMachineEngine → calculate_spacetime
          → DecisionRouterGateway → AntiAmnesiaTracker → AttributionEngine

    用法:
        engine = TrinityEngine()
        decision = engine.analyze(ctx, structure_score=0.85, ...)
    """

    def __init__(self):
        self.structure_parser = StructureParser()
        self.state_machine = StateMachineEngine()
        self.router = DecisionRouterGateway()
        self.memory = AntiAmnesiaTracker()
        self.attribution = AttributionEngine()

    def analyze(
        self,
        ctx: JLevelContext,
        structure_score: float = 0.5,
        structure_type: str = "D",
        direction: str = "UP",
        duration_curr: int = 0,
        duration_ref: int = 0,
    ) -> Decision:
        """执行完整分析流程, 输出决策并写入记忆

        Args:
            ctx:             级别嵌套上下文
            structure_score: 空间结构得分
            structure_type:  结构类型 (A/B/C/D)
            direction:       结构方向 (UP/DOWN)
            duration_curr:   当前下跌段时长 (周)
            duration_ref:    参考下跌段时长 (周)
        """
        # 1. 时空评分
        spacetime = None
        if duration_curr > 0 or duration_ref > 0:
            spacetime = calculate_spacetime(ctx, structure_score, duration_curr, duration_ref)

        # 2. 决策路由
        decision = self.router.route(
            ctx, spacetime_score=spacetime,
            structure_type=structure_type, direction=direction,
        )

        # 3. 记忆写入 (防失忆)
        self.memory.record(ctx, decision, metadata={
            "structure_type": structure_type,
            "direction": direction,
            "duration_curr": duration_curr,
            "duration_ref": duration_ref,
        })

        # 4. 归因收集 (蓝图 v2.1 §4.2: 记录"为什么买")
        self.attribution.collect(
            ctx, spacetime, structure_score=structure_score, direction=direction,
        )

        return decision

    def recall_similar(self, ctx: JLevelContext) -> List[MemoryEvent]:
        """回忆相似情境 (防失忆)"""
        return self.memory.recall_similar(ctx)

    def save_memory(self, filepath: str) -> None:
        """持久化记忆"""
        self.memory.save(filepath)

    def load_memory(self, filepath: str) -> None:
        """加载记忆"""
        self.memory.load(filepath)


# ================================================================
# 7. 桥接适配器 (与现有系统的兼容层)
# ================================================================


def to_legacy_macro_state(state: State):
    """将引擎 State 转换为现有系统的 MacroState

    用于新旧系统共存期间的兼容。
    """
    from trinity.context import MacroState

    mapping = {
        State.EXTREME_BULL: MacroState.EXTREME_STRONG,
        State.BULL: MacroState.STRONG,
        State.MID_BULL: MacroState.MODERATE_STRONG,
        State.MID_BEAR: MacroState.MODERATE_WEAK,
        State.BEAR: MacroState.WEAK,
        State.EXTREME_BEAR: MacroState.EXTREME_WEAK,
        State.NEUTRAL: MacroState.MODERATE_STRONG,  # 混沌降级为中偏强
    }
    return mapping.get(state, MacroState.MODERATE_STRONG)
