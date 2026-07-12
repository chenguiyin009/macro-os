"""Phase 3 风控集成层单元测试。

锁定：
  * initialize_risk_integration 创建全局单例；
  * should_execute_risk_action 正常路径（传入行情 → RiskAction）；
  * should_execute_risk_action Fail-Safe（未初始化 → HOLD）；
  * should_execute_risk_action 异常降级（适配器崩溃 → HOLD）；
  * get_trinity_adapter / get_evidence_ledger 非 None；
  * 配置开关关闭时 Orchestrator 不初始化风控（集成测试）。
"""
from __future__ import annotations

import pytest

from core.adapters.risk_action import RiskActionType
from core.integration.risk_integration import (
    initialize_risk_integration,
    get_trinity_adapter,
    get_evidence_ledger,
    should_execute_risk_action,
)


def test_initialize_creates_singletons():
    adapter = initialize_risk_integration()
    assert get_trinity_adapter() is adapter
    assert get_evidence_ledger() is not None
    assert len(get_evidence_ledger()) == 0  # 初始化后无记录


def test_should_execute_risk_action_normal():
    initialize_risk_integration()
    bar = {
        "close": 102.5,
        "open": 101.0,
        "atr": 2.8,
        "spacetime_score": 0.91,
        "j1_confirmed": True,
        "vix": 28.5,
        "symbol": "IF2509",
    }
    action = should_execute_risk_action(bar)
    assert action.action_type in (
        RiskActionType.INITIAL_ENTRY,
        RiskActionType.HOLD,
    )
    # 归因记录应已写入 Ledger
    assert len(get_evidence_ledger()) >= 1


def test_should_execute_risk_action_fail_safe_uninitialized(monkeypatch):
    """未初始化时降级为 HOLD，不崩溃。"""
    import core.integration.risk_integration as ri

    monkeypatch.setattr(ri, "_trinity_adapter", None)
    monkeypatch.setattr(ri, "_evidence_ledger", None)

    bar = {"close": 100.0, "atr": 2.0, "spacetime_score": 0.9, "j1_confirmed": True}
    action = should_execute_risk_action(bar)
    assert action.action_type == RiskActionType.HOLD
    assert "adapter_not_initialized" in action.reason


def test_should_execute_risk_action_fail_safe_exception(monkeypatch):
    """适配器内部异常 → 降级 HOLD。"""
    initialize_risk_integration()
    adapter = get_trinity_adapter()

    def broken(*args, **kwargs):
        raise RuntimeError("simulated integration crash")

    monkeypatch.setattr(adapter, "to_market_state", broken)
    bar = {"close": 100.0, "atr": 2.0, "spacetime_score": 0.9, "j1_confirmed": True}
    action = should_execute_risk_action(bar)
    assert action.action_type == RiskActionType.HOLD
    assert "integration_exception" in action.reason


def test_orchestrator_respects_disable_flag():
    """配置 ENABLE_RISK_GATEWAY=False 时不初始化风控。"""
    from unittest.mock import MagicMock
    from runtime.orchestrator import Orchestrator

    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=MagicMock(),
        feishu=MagicMock(),
        config={"ENABLE_RISK_GATEWAY": False},
    )
    assert orch.enable_risk_gateway is False


def test_orchestrator_default_enables_gateway():
    """默认配置（无显式 ENABLE_RISK_GATEWAY）时启用风控。"""
    from unittest.mock import MagicMock
    from runtime.orchestrator import Orchestrator

    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=MagicMock(),
        feishu=MagicMock(),
    )
    assert orch.enable_risk_gateway is True


def test_orchestrator_health_includes_risk_flag():
    """health() 应包含 risk_gateway_enabled 字段。"""
    from unittest.mock import MagicMock
    from runtime.orchestrator import Orchestrator

    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=MagicMock(),
        feishu=MagicMock(),
        config={"ENABLE_RISK_GATEWAY": False},
    )
    h = orch.health()
    assert "risk_gateway_enabled" in h
    assert h["risk_gateway_enabled"] is False
