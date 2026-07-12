"""Trinity OS v2.2.1 — 风控动作强类型契约 (RiskAction)。

WHY THIS FILE EXISTS
--------------------
`RiskGateway.process_tick` 返回 `Dict[str, Any]`——灵活但无类型安全、不便归因。
本模块定义 `RiskActionType` 枚举 + `RiskAction` dataclass，作为网关输出的统一强类型
容器，供 TrinityAdapter 转换后交付上层（DecisionKernel / Orchestrator / Ledger）。

纯增量、零耦合：仅依赖 dataclasses + enum，不 import 任何 Trinity 模块。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional


class RiskActionType(Enum):
    """风控动作类型枚举（强类型，便于 Evidence Ledger 归因）"""
    HOLD = auto()
    INITIAL_ENTRY = auto()
    PYRAMID_ADD = auto()
    TRAILING_EXIT = auto()
    FATAL_GAP_LIQUIDATION = auto()


@dataclass
class RiskAction:
    """风控动作统一返回结构。

    由 TrinityAdapter 将 RiskGateway 的 dict 日志转换为本结构，
    确保上层消费端（DecisionKernel / Orchestrator）面对的是类型安全的契约。
    """
    action_type: RiskActionType
    reason: str = ""
    size: float = 0.0
    execution_price: Optional[float] = None
    breached_stop: Optional[float] = None
    gap_price: Optional[float] = None
    position: Optional[Any] = None          # 可存放 Position 对象
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外信息（便于归因）
