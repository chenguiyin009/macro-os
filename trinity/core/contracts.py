"""Trinity OS v2.1 — 蓝图契约别名层 (非破坏性)

蓝图 v2.1 使用统一的语义命名 (TrinityEvent / EvidenceFactor /
OutcomeResult / OutcomeEvent)。为对齐蓝图语义而不破坏现有模块,
本层对既有的真实定义做"再导出 (re-export)":

  - TrinityEvent   = DecisionEvent   (trinity/ledger.py, 账本单条决策记录)
  - EvidenceFactor = EvidenceFactor  (trinity/core/engine.py, 结构化证据因子)
  - OutcomeResult  = OutcomeResult   (trinity/outcome.py, 双窗口收益)
  - OutcomeEvent   = OutcomeEvent    (trinity/ledger.py, 收益事件, linked_event_id)

这样蓝图代码可统一从 `trinity.core.contracts` 导入, 而底层实现路径不变,
现有 254 测试套件无需任何改动。
"""
from __future__ import annotations

from trinity.aggregator import DiagnosticReport, PerformanceAggregator
from trinity.core.engine import EvidenceFactor, State
from trinity.ledger import DecisionEvent, OutcomeEvent
from trinity.outcome import OutcomeResult

__all__ = [
    "TrinityEvent",
    "DecisionEvent",
    "EvidenceFactor",
    "OutcomeResult",
    "OutcomeEvent",
    "DiagnosticReport",
    "PerformanceAggregator",
    "State",
]

# 蓝图语义别名: 决策事件在蓝图中称为 TrinityEvent
TrinityEvent = DecisionEvent
