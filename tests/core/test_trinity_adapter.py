"""TrinityAdapter + RiskAction 单元测试。

锁定：
  * RiskActionType 枚举完整性；
  * TrinityAdapter.to_market_state 透传 macro/open；
  * TrinityAdapter.get_risk_action 各动作类型映射；
  * Fail-Safe 兜底（网关抛异常 → 返回 HOLD，不崩溃）；
  * FATAL_GAP_LIQUIDATION 的大小写兼容。
"""
from __future__ import annotations

import pytest

from core.adapters.risk_action import RiskAction, RiskActionType
from core.adapters.trinity_adapter import TrinityAdapter
from core.risk_gateway_v2_2_1 import MarketState, RiskGateway


def test_risk_action_type_enum_members():
    members = set(RiskActionType)
    expected = {
        RiskActionType.HOLD,
        RiskActionType.INITIAL_ENTRY,
        RiskActionType.PYRAMID_ADD,
        RiskActionType.TRAILING_EXIT,
        RiskActionType.FATAL_GAP_LIQUIDATION,
    }
    assert members == expected


def test_risk_action_defaults():
    ra = RiskAction(action_type=RiskActionType.HOLD)
    assert ra.reason == ""
    assert ra.size == 0.0
    assert ra.execution_price is None
    assert ra.breached_stop is None
    assert ra.gap_price is None
    assert ra.position is None
    assert ra.metadata == {}


def test_to_market_state_basic():
    adapter = TrinityAdapter()
    ms = adapter.to_market_state(
        current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.87, j1_confirmed=False
    )
    assert ms.current_price == 100.0
    assert ms.d3_low == 95.0
    assert ms.macro_vix == 22.0  # default


def test_to_market_state_penetrates_macro_and_open():
    adapter = TrinityAdapter()
    ms = adapter.to_market_state(
        current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.87,
        j1_confirmed=False, macro_vix=35.0, macro_commodity_shock=0.20, open=90.0,
    )
    assert ms.macro_vix == 35.0
    assert ms.macro_commodity_shock == 0.20
    assert ms.open == 90.0


def test_get_risk_action_initial_entry():
    adapter = TrinityAdapter(RiskGateway())
    ms = MarketState(current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.87, j1_confirmed=False)
    ra = adapter.get_risk_action(ms)
    assert ra.action_type == RiskActionType.INITIAL_ENTRY
    assert ra.size > 0
    assert ra.execution_price == 100.0


def test_get_risk_action_hold_on_no_signal():
    adapter = TrinityAdapter(RiskGateway())
    ms = MarketState(current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.0, j1_confirmed=False)
    ra = adapter.get_risk_action(ms)
    assert ra.action_type == RiskActionType.HOLD


def test_get_risk_action_fatal_gap():
    """跳空强平 → FATAL_GAP_LIQUIDATION（含 reason/breached_stop/gap_price 字段）。"""
    gw = RiskGateway()
    gw.process_tick(
        1_000_000.0,
        MarketState(current_price=105.0, d3_low=98.0, atr=2.0, spacetime_score=0.95, j1_confirmed=False),
    )
    adapter = TrinityAdapter(gw)
    ms = MarketState(current_price=90.0, open=90.0, d3_low=98.0, atr=2.0, spacetime_score=0.9, j1_confirmed=False)
    ra = adapter.get_risk_action(ms)
    assert ra.action_type == RiskActionType.FATAL_GAP_LIQUIDATION
    assert ra.reason == "hard_stop_gap_breach"
    assert ra.breached_stop is not None
    assert ra.gap_price == 90.0


def test_fail_safe_returns_hold_on_exception():
    """网关抛异常时，适配器捕获并返回 HOLD，不向上传播。"""
    class BrokenGateway:
        def process_tick(self, *args, **kwargs):
            raise RuntimeError("simulated crash")

    adapter = TrinityAdapter(BrokenGateway())
    ms = MarketState(current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.9, j1_confirmed=False)
    ra = adapter.get_risk_action(ms)
    assert ra.action_type == RiskActionType.HOLD
    assert "adapter_exception" in ra.reason
    assert "simulated crash" in ra.metadata.get("exception", "")
