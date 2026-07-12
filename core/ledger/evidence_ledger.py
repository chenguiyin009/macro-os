"""Trinity OS v2.2.1 — 证据溯源中枢 (EvidenceLedger)。

WHY THIS FILE EXISTS
--------------------
v2.2.1 风控子系统产生的每一条 RiskAction（入场/加仓/减仓/强平/HOLD）都需要
结构化记录，用于：
  * 事后审计（哪条规则触发了什么动作？）；
  * 归因分析（亏损来自跳空穿透还是正常止损？）；
  * 与外部 Evidence Ledger 对接（`scripts/validate_ledger.py` 消费端）。

本模块是一个内存证据链（可后续扩展为持久化），由 TrinityAdapter 自动注入，
每次 `get_risk_action` 成功后调用 `record_risk_action` 归因。

纯增量、零耦合：仅依赖 core.adapters.risk_action（RiskAction dataclass）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

from core.adapters.risk_action import RiskAction

logger = logging.getLogger(__name__)


class EvidenceLedger:
    """Trinity OS 证据溯源中枢（增强版），支持风控决策的结构化归因记录。"""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def record_risk_action(
        self,
        action: RiskAction,
        context: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """记录一次风控决策及其归因信息。

        Args:
            action: RiskGateway 输出的强类型风控动作。
            context: 调用方附加上下文（如 symbol / account_id / bar_index）。
            timestamp: 可选时间戳，默认 now()。
        Returns:
            结构化记录 dict（可直接序列化）。
        """
        if context is None:
            context = {}

        record: Dict[str, Any] = {
            "timestamp": timestamp or datetime.now(),
            "action_type": action.action_type.name,
            "reason": action.reason,
            "size": action.size,
            "execution_price": action.execution_price,
            "breached_stop": action.breached_stop,
            "gap_price": action.gap_price,
            "metadata": action.metadata or {},
            "context": context,
        }

        self.records.append(record)
        logger.info(
            "[EvidenceLedger] 记录风控动作: %s | reason=%s",
            action.action_type.name,
            action.reason,
        )
        return record

    def get_recent_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取最近 limit 条记录。"""
        return self.records[-limit:]

    def get_records_by_action_type(self, action_type: str) -> List[Dict[str, Any]]:
        """按动作类型筛选记录。"""
        return [r for r in self.records if r["action_type"] == action_type]

    def clear(self) -> None:
        """清空所有记录（测试/重置用）。"""
        self.records.clear()

    def __len__(self) -> int:
        return len(self.records)
