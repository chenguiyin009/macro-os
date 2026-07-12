"""Evidence Ledger 归因记录单元测试（C2 规格）。

锁定：
  * record_risk_action 基本记录 + context 透传；
  * 多条记录与按类型筛选；
  * TrinityAdapter 自动归因（正常路径 + Fail-Safe 路径）；
  * Ledger clear / len 边界。
"""
from __future__ import annotations

import pytest

from core.adapters.risk_action import RiskAction, RiskActionType
from core.adapters.trinity_adapter import TrinityAdapter
from core.ledger.evidence_ledger import EvidenceLedger
from core.risk_gateway_v2_2_1 import MarketState, RiskGateway


def test_record_risk_action_basic():
    ledger = EvidenceLedger()
    action = RiskAction(
        action_type=RiskActionType.FATAL_GAP_LIQUIDATION,
        reason="hard_stop_gap_breach",
        size=1200,
        breached_stop=95.0,
        gap_price=87.5,
    )
    record = ledger.record_risk_action(action, context={"symbol": "IF2509"})
    assert record["action_type"] == "FATAL_GAP_LIQUIDATION"
    assert record["reason"] == "hard_stop_gap_breach"
    assert record["size"] == 1200
    assert record["context"]["symbol"] == "IF2509"
    assert len(ledger) == 1


def test_record_multiple_actions():
    ledger = EvidenceLedger()
    action1 = RiskAction(action_type=RiskActionType.INITIAL_ENTRY, size=500)
    action2 = RiskAction(action_type=RiskActionType.PYRAMID_ADD, size=300)

    ledger.record_risk_action(action1)
    ledger.record_risk_action(action2)

    assert len(ledger) == 2
    assert len(ledger.get_records_by_action_type("INITIAL_ENTRY")) == 1
    assert len(ledger.get_records_by_action_type("PYRAMID_ADD")) == 1


def test_get_recent_records_limit():
    ledger = EvidenceLedger()
    for i in range(10):
        ledger.record_risk_action(RiskAction(action_type=RiskActionType.HOLD))
    assert len(ledger.get_recent_records(3)) == 3
    assert len(ledger.get_recent_records(20)) == 10


def test_ledger_clear():
    ledger = EvidenceLedger()
    ledger.record_risk_action(RiskAction(action_type=RiskActionType.HOLD))
    assert len(ledger) == 1
    ledger.clear()
    assert len(ledger) == 0


def test_adapter_auto_logs_to_ledger():
    """正常路径：TrinityAdapter 成功调用后自动归因到 Ledger。"""
    adapter = TrinityAdapter(RiskGateway(), EvidenceLedger())
    ms = MarketState(current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.87, j1_confirmed=False)
    action = adapter.get_risk_action(ms, context={"symbol": "TEST"})
    assert action.action_type == RiskActionType.INITIAL_ENTRY
    assert len(adapter.ledger) == 1
    record = adapter.ledger.records[0]
    assert record["action_type"] == "INITIAL_ENTRY"
    assert record["context"]["symbol"] == "TEST"


def test_adapter_fail_safe_logs_hold_to_ledger():
    """Fail-Safe 路径：网关异常时记录 HOLD 到 Ledger。"""
    class BrokenGateway:
        def process_tick(self, *args, **kwargs):
            raise RuntimeError("simulated crash")

    adapter = TrinityAdapter(BrokenGateway(), EvidenceLedger())
    ms = MarketState(current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.9, j1_confirmed=False)
    action = adapter.get_risk_action(ms, context={"symbol": "CRASH"})
    assert action.action_type == RiskActionType.HOLD
    assert "adapter_exception" in action.reason
    assert len(adapter.ledger) == 1
    record = adapter.ledger.records[0]
    assert record["action_type"] == "HOLD"
    assert record["context"]["symbol"] == "CRASH"
