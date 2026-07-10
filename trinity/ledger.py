"""Trinity OS v2.1 - 事件溯源账本

采用 Event Sourcing, 记录所有决策的 Evidence (证据链),
解决"为什么当时做这个决定"的复盘难题。

不是仅仅记录买卖动作, 而是记录完整的推理路径。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from trinity.context import Decision, JLevelContext, TradingLevel


@dataclass
class DecisionEvent:
    """决策事件 - 账本中的单条记录

    每个事件记录一次完整的决策推理, 包含:
      - 时间戳
      - 标的
      - 各级别上下文快照
      - 决策结果
      - 完整证据链
    """
    event_id: str
    timestamp: float
    symbol: str
    contexts: dict[str, dict]        # 各级别上下文快照
    decision: dict                    # 决策结果
    evidence: list[str]              # 完整证据链
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionEvent":
        return cls(**d)


@dataclass
class OutcomeEvent:
    """收益事件 — 决策后果 (蓝图 v2.1 §铁律 #2 不可变溯源)

    与 DecisionEvent 解耦: 决策事件一经记录即不可变 (Immutable Provenance),
    收益结果通过 OutcomeEvent 以 linked_event_id 绑定, 而非修改原决策。

    这是"延迟双指针" (铁律 #1) 的清算端:
      entry_index 锚定同一市场序列, 收益从该序列 entry_index 之后切片计算。
    """
    linked_event_id: str          # 绑定的决策事件 ID
    entry_index: int              # 入场 K 线索引 (同一序列锚点)
    symbol: str = ""
    result: dict = field(default_factory=dict)   # OutcomeResult.to_dict()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OutcomeEvent":
        return cls(**d)


class EventSourcingTracker:
    """事件溯源追踪器

    用法:
        tracker = EventSourcingTracker()
        tracker.record(ctx_j2, ctx_j, decision)
        events = tracker.replay()
        tracker.save("ledger.json")
    """

    def __init__(self):
        self._events: list[DecisionEvent] = []
        self._outcomes: list[OutcomeEvent] = []
        self._counter: int = 0

    def record(
        self,
        contexts: dict[TradingLevel, JLevelContext],
        decision: Decision,
        metadata: Optional[dict] = None,
    ) -> DecisionEvent:
        """记录一次决策事件

        Args:
            contexts: 各级别上下文 {TradingLevel: JLevelContext}
            decision: 决策结果
            metadata: 额外元数据 (如数据源、运行模式等)
        """
        self._counter += 1
        event_id = f"EVT-{self._counter:06d}"
        timestamp = time.time()

        # 序列化上下文
        ctx_dict = {}
        for level, ctx in contexts.items():
            ctx_dict[level.value] = ctx.to_dict()

        event = DecisionEvent(
            event_id=event_id,
            timestamp=timestamp,
            symbol=decision.symbol,
            contexts=ctx_dict,
            decision=decision.to_dict(),
            evidence=decision.evidence.copy(),
            metadata=metadata or {},
        )
        self._events.append(event)
        return event

    def replay(self) -> list[DecisionEvent]:
        """回放所有事件 (用于复盘)"""
        return list(self._events)

    def replay_for_symbol(self, symbol: str) -> list[DecisionEvent]:
        """回放指定标的的事件"""
        return [e for e in self._events if e.symbol == symbol]

    def query(
        self,
        symbol: Optional[str] = None,
        action: Optional[str] = None,
        min_confidence: float = 0.0,
    ) -> list[DecisionEvent]:
        """查询事件

        Args:
            symbol:       按标的过滤
            action:       按动作类型过滤
            min_confidence: 最低置信度
        """
        result = []
        for e in self._events:
            if symbol and e.symbol != symbol:
                continue
            if action and e.decision.get("action") != action:
                continue
            if e.decision.get("confidence", 0) < min_confidence:
                continue
            result.append(e)
        return result

    def summary(self) -> dict:
        """账本摘要统计"""
        actions: dict[str, int] = {}
        symbols: dict[str, int] = {}
        confidences: list[float] = []
        for e in self._events:
            action = e.decision.get("action", "UNKNOWN")
            actions[action] = actions.get(action, 0) + 1
            symbols[e.symbol] = symbols.get(e.symbol, 0) + 1
            confidences.append(e.decision.get("confidence", 0))

        avg_conf = sum(confidences) / len(confidences) if confidences else 0
        return {
            "total_events": len(self._events),
            "total_outcomes": len(self._outcomes),
            "actions": actions,
            "symbols": symbols,
            "avg_confidence": round(avg_conf, 4),
            "event_ids": [e.event_id for e in self._events],
        }

    def save(self, filepath: str) -> None:
        """保存账本到 JSON 文件"""
        data = {
            "version": "2.1.0",
            "saved_at": time.time(),
            "events": [e.to_dict() for e in self._events],
            "outcomes": [o.to_dict() for o in self._outcomes],
        }
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, filepath: str) -> None:
        """从 JSON 文件加载账本"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._events = [DecisionEvent.from_dict(e) for e in data.get("events", [])]
        self._outcomes = [
            OutcomeEvent.from_dict(o) for o in data.get("outcomes", [])
        ]
        # 恢复计数器
        if self._events:
            last_id = self._events[-1].event_id
            try:
                self._counter = int(last_id.split("-")[-1])
            except (ValueError, IndexError):
                self._counter = len(self._events)

    def clear(self) -> None:
        """清空账本"""
        self._events.clear()
        self._outcomes.clear()
        self._counter = 0

    # ========== 收益事件绑定 (铁律 #1/#2) ==========

    def bind_outcome(self, event_id: str, outcome_event: OutcomeEvent) -> OutcomeEvent:
        """将收益结果绑定到决策事件 (不可变溯源)

        决策事件本身不可修改; 收益通过独立的 OutcomeEvent 以
        linked_event_id 关联, 满足"延迟双指针"的清算语义。

        Args:
            event_id:      决策事件 ID (DecisionEvent.event_id)
            outcome_event: 收益事件 (其 linked_event_id 应等于 event_id)
        """
        if outcome_event.linked_event_id != event_id:
            # 防御性对齐: 以被绑定事件为准, 保证溯源一致
            outcome_event = OutcomeEvent(
                linked_event_id=event_id,
                entry_index=outcome_event.entry_index,
                symbol=outcome_event.symbol,
                result=outcome_event.result,
            )
        self._outcomes.append(outcome_event)
        return outcome_event

    def outcomes(self) -> list[OutcomeEvent]:
        """返回全部收益事件"""
        return list(self._outcomes)

    def outcome_for(self, event_id: str) -> Optional[OutcomeEvent]:
        """按决策事件 ID 查询绑定的收益事件"""
        for o in self._outcomes:
            if o.linked_event_id == event_id:
                return o
        return None

    @property
    def count(self) -> int:
        return len(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)


# ========== 账本校验 ==========

def validate_ledger(filepath: str) -> tuple[bool, list[str]]:
    """校验账本文件完整性

    检查项:
      1. 文件可读且为合法 JSON
      2. 版本信息存在
      3. 每个事件包含必要字段
      4. 事件 ID 唯一
      5. 证据链非空
      6. 决策动作合法
    """
    errors: list[str] = []

    if not os.path.exists(filepath):
        return False, [f"账本文件不存在: {filepath}"]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"JSON 解析失败: {e}"]

    version = data.get("version")
    if not version:
        errors.append("缺少版本信息")

    events = data.get("events", [])
    if not events:
        errors.append("账本为空 (无事件)")

    seen_ids: set[str] = set()
    valid_actions = {
        "STRONG_ADD", "ADD_ON_PULLBACK", "SCOUT_WITH_STOP", "BUY_CAUTIOUSLY",
        "SELL_ON_BOUNCE", "STRONG_REDUCE", "REDUCE_CAUTIOUSLY", "HOLD",
        "TAKE_PROFIT_T", "STOP_LOSS",
    }

    for i, e in enumerate(events):
        # 必要字段
        for field_name in ("event_id", "timestamp", "symbol", "decision", "evidence"):
            if field_name not in e:
                errors.append(f"事件 #{i}: 缺少字段 '{field_name}'")

        # ID 唯一
        eid = e.get("event_id", "")
        if eid in seen_ids:
            errors.append(f"事件 #{i}: ID 重复 '{eid}'")
        seen_ids.add(eid)

        # 证据链非空
        if not e.get("evidence"):
            errors.append(f"事件 #{i} ({eid}): 证据链为空")

        # 决策动作合法
        action = e.get("decision", {}).get("action", "")
        if action and action not in valid_actions:
            errors.append(f"事件 #{i} ({eid}): 未知动作 '{action}'")

        # 置信度范围
        conf = e.get("decision", {}).get("confidence", -1)
        if not (0 <= conf <= 1):
            errors.append(f"事件 #{i} ({eid}): 置信度越界 {conf}")

    return len(errors) == 0, errors
