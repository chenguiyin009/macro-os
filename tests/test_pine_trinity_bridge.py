"""Pine 信号 × TrinityAdapter 端到端接线测试。

覆盖：
- PineConclusionSchema → PineSignal 标准化翻译（关键词 / 风险分 / UNKNOWN）。
- 宏观冲击注入（RISK_OFF → macro_commodity_shock=0.15，触发网关 MacroCircuitBreaker）。
- combine 叠加规则（前端熔断否决入场 / 放行 / 不可信透传）。
- run_pine_trinity_loop 端到端编排（含 Fail-Safe 顶层防护）。
- PineTrinityNode 把决策 staged 到 pipeline context。
- 双层集成：宏观注入确实抑制金字塔加仓 + overlay 确认。
- live CDP 用例：TradingView Desktop 开启时真实跑通（关闭时自动 skip）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import pytest

from core.adapters.pine_trinity_bridge import (
    PineSignal,
    PineTrinityDecision,
    combine,
    derive_pine_confidence,
    enrich_bar_with_pine,
    pine_to_macro_shock,
    run_pine_trinity_loop,
    translate_pine_signal,
)
from core.adapters.risk_action import RiskAction, RiskActionType
from core.adapters.trinity_adapter import TrinityAdapter
from core.pipeline import PipelineContext, PineTrinityNode
from core.schemas import PineConclusionSchema


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SCRIPT = REPO_ROOT / "relay" / "pine-bridge.mjs"
LIVE_SCRIPT = "24H Macro Liquidity Danger Dashboard FULL"


def _cdp_alive() -> bool:
    try:
        out = subprocess.run(
            ["node", str(BRIDGE_SCRIPT), "--dry"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return False


# ----- 测试桩：可注入任意 Pine 结论的假 adapter -----
class _FakeTVAdapter:
    def __init__(self, conclusion: PineConclusionSchema | None) -> None:
        self._conclusion = conclusion

    def fetch_pine_conclusions(self, symbol=None, script_name=None, cdp_url="http://127.0.0.1:9222"):
        return self._conclusion


def _entry_bar() -> dict:
    """满足网关初始入场条件的行情（spacetime>0, price>d3_low）。"""
    return {
        "close": 100.0,
        "d3_low": 95.0,
        "atr": 2.0,
        "spacetime_score": 0.9,
        "j1_confirmed": True,
        "open": 99.0,
        "vix": 22.0,
        "brent_shock": 0.0,
    }


# ===================== 翻译层 =====================
def test_translate_signal_keywords() -> None:
    assert translate_pine_signal(PineConclusionSchema(signal="risk_off")) == PineSignal.RISK_OFF
    assert translate_pine_signal(PineConclusionSchema(signal="RISK OFF")) == PineSignal.RISK_OFF
    assert translate_pine_signal(PineConclusionSchema(label="Global Risk Alert")) == PineSignal.RISK_OFF
    assert translate_pine_signal(PineConclusionSchema(signal="buy")) == PineSignal.RISK_ON
    assert translate_pine_signal(PineConclusionSchema(label="Bullish")) == PineSignal.RISK_ON


def test_translate_signal_value_risk_score() -> None:
    # 默认：value>1 视为风险升高 → RISK_OFF；<=0 → RISK_ON；中间 → NEUTRAL
    assert translate_pine_signal(PineConclusionSchema(value=3.0)) == PineSignal.RISK_OFF
    assert translate_pine_signal(PineConclusionSchema(value=0.0)) == PineSignal.RISK_ON
    assert translate_pine_signal(PineConclusionSchema(value=0.5)) == PineSignal.NEUTRAL


def test_translate_signal_unknown() -> None:
    assert translate_pine_signal(None) == PineSignal.UNKNOWN
    assert translate_pine_signal(PineConclusionSchema()) == PineSignal.UNKNOWN


# ===================== 置信度派生（确定性状态读数） =====================
def test_derive_confidence_explicit_passthrough() -> None:
    # 显式置信度优先，原样返回（兼容既有调用方）。
    assert derive_pine_confidence(PineConclusionSchema(value=3.0, confidence=0.4)) == 0.4


def test_derive_confidence_risk_off_flag() -> None:
    # 宏观脚本用整型状态值 + 状态布尔标志呈现；Caution=1 是确定性风险读数 → 高置信。
    conclusion = PineConclusionSchema(
        value=3,
        payload={
            "values": [
                {"id": "p0", "title": "Background Color", "value": "3", "visible": True},
                {"id": "p3", "title": "Caution", "value": "1", "visible": True},
            ]
        },
    )
    assert derive_pine_confidence(conclusion) == 0.85


def test_derive_confidence_risk_on_flag() -> None:
    conclusion = PineConclusionSchema(
        payload={
            "values": [
                {"id": "p4", "title": "Risk-on liquidity", "value": "1", "visible": True},
            ]
        },
    )
    assert derive_pine_confidence(conclusion) == 0.80


def test_derive_confidence_from_state_value() -> None:
    # 无标志但整型状态值 → 按严重度映射。
    assert derive_pine_confidence(PineConclusionSchema(value=4.0)) == 0.95
    assert derive_pine_confidence(PineConclusionSchema(value=0.0)) == 0.80


def test_derive_confidence_none_conclusion() -> None:
    assert derive_pine_confidence(None) is None


def test_derive_confidence_empty_leaves_untrusted() -> None:
    # 完全无信号 → None，combine 应判定 untrusted（不 override）。
    assert derive_pine_confidence(PineConclusionSchema()) is None


def test_pine_to_macro_shock() -> None:
    assert pine_to_macro_shock(PineSignal.RISK_OFF) == 0.15
    assert pine_to_macro_shock(PineSignal.RISK_ON) == 0.0
    assert pine_to_macro_shock(PineSignal.NEUTRAL) == 0.0
    assert pine_to_macro_shock(PineSignal.UNKNOWN) == 0.0


def test_enrich_bar_injects_shock() -> None:
    bar = {"close": 100.0, "brent_shock": 0.0}
    off = enrich_bar_with_pine(bar, PineConclusionSchema(signal="risk_off"))
    assert off["brent_shock"] == 0.15
    assert off["_pine_signal"] == "RISK_OFF"

    on = enrich_bar_with_pine(bar, PineConclusionSchema(signal="buy"))
    assert on["brent_shock"] == 0.0
    assert on["_pine_signal"] == "RISK_ON"


# ===================== combine 叠加规则 =====================
def _base(action: RiskActionType) -> RiskAction:
    return RiskAction(action_type=action, reason="gateway")


def test_combine_risk_off_blocks_entry() -> None:
    d = combine(_base(RiskActionType.INITIAL_ENTRY), PineSignal.RISK_OFF, confidence=0.9)
    assert d.override is True
    assert d.final_action_type == RiskActionType.HOLD
    assert d.reason == "pine_risk_off_override"
    assert d.confidence_trusted is True


def test_combine_risk_off_confirmed_when_holding() -> None:
    d = combine(_base(RiskActionType.HOLD), PineSignal.RISK_OFF, confidence=0.9)
    assert d.override is False
    assert d.final_action_type == RiskActionType.HOLD
    assert d.reason == "pine_risk_off_confirmed"


def test_combine_risk_on_permissive() -> None:
    d = combine(_base(RiskActionType.INITIAL_ENTRY), PineSignal.RISK_ON, confidence=0.9)
    assert d.override is False
    assert d.final_action_type == RiskActionType.INITIAL_ENTRY
    assert d.reason == "pine_risk_on_permissive"


def test_combine_untrusted_passthrough() -> None:
    # UNKNOWN
    d1 = combine(_base(RiskActionType.INITIAL_ENTRY), PineSignal.UNKNOWN, confidence=0.9)
    assert d1.override is False
    assert d1.final_action_type == RiskActionType.INITIAL_ENTRY
    # 低置信
    d2 = combine(_base(RiskActionType.INITIAL_ENTRY), PineSignal.RISK_OFF, confidence=0.2)
    assert d2.override is False
    assert d2.final_action_type == RiskActionType.INITIAL_ENTRY
    assert d2.confidence_trusted is False


# ===================== 端到端编排 =====================
def test_run_loop_mock_risk_off_blocks_entry() -> None:
    adapter = _FakeTVAdapter(PineConclusionSchema(signal="risk_off", confidence=0.9, label="Global Risk Off"))
    d = run_pine_trinity_loop(adapter, _entry_bar())
    assert isinstance(d, PineTrinityDecision)
    assert d.pine_signal == PineSignal.RISK_OFF
    assert d.override is True
    assert d.final_action_type == RiskActionType.HOLD
    assert d.enriched_macro_shock == 0.15


def test_run_loop_mock_risk_on_allows_entry() -> None:
    adapter = _FakeTVAdapter(PineConclusionSchema(signal="buy", confidence=0.9))
    d = run_pine_trinity_loop(adapter, _entry_bar())
    assert d.pine_signal == PineSignal.RISK_ON
    assert d.override is False
    assert d.final_action_type == RiskActionType.INITIAL_ENTRY


def test_run_loop_fail_safe_on_exception() -> None:
    class _BoomAdapter:
        def fetch_pine_conclusions(self, *a, **k):
            raise RuntimeError("bridge exploded")

    d = run_pine_trinity_loop(_BoomAdapter(), _entry_bar())
    assert d.final_action_type == RiskActionType.HOLD
    assert d.reason == "pine_trinity_exception"
    assert d.pine_signal == PineSignal.UNKNOWN


def test_macro_injection_suppresses_pyramid() -> None:
    """双层验证：宏观注入抑制金字塔加仓，overlay 确认 HOLD。"""
    ta = TrinityAdapter()  # 共享网关状态
    entry_adapter = _FakeTVAdapter(PineConclusionSchema(signal="buy", confidence=0.9))
    off_adapter = _FakeTVAdapter(PineConclusionSchema(signal="risk_off", confidence=0.95))

    # Tick 1: RISK_ON → 网关初始入场（建立 1 个头寸）
    d1 = run_pine_trinity_loop(entry_adapter, _entry_bar(), trinity_adapter=ta)
    assert d1.final_action_type == RiskActionType.INITIAL_ENTRY
    size_after_entry = ta.risk_gateway.get_status()["total_remaining_size"]
    assert size_after_entry > 0

    # Tick 2: RISK_OFF → 宏观冲击 0.15 触发 MacroCircuitBreaker，
    # 网关不会金字塔加仓；overlay 确认（base 已非入场）。
    d2 = run_pine_trinity_loop(off_adapter, _entry_bar(), trinity_adapter=ta)
    assert d2.enriched_macro_shock == 0.15
    assert d2.override is False
    assert d2.reason == "pine_risk_off_confirmed"
    # 关键：加仓被宏观熔断挡住，总持仓规模不变
    assert ta.risk_gateway.get_status()["total_remaining_size"] == size_after_entry


def test_macro_dashboard_caution_overrides_entry() -> None:
    """真实宏观脚本形态：value=3 + Caution=1、无 confidence 字段。

    派生置信度应让前端熔断门控信任该确定性风险读数，并否决网关的初始入场 → HOLD。
    这是 live CDP 拉到 '24H Macro Liquidity Danger Dashboard' 时的真实语义闭环。
    """
    macro_conclusion = PineConclusionSchema(
        source_script="24H Macro Liquidity Danger Dashboard FULL",
        symbol="TVC:GOLD",
        value=3,
        payload={
            "values": [
                {"id": "p0", "title": "Background Color", "value": "3", "visible": True},
                {"id": "p3", "title": "Caution", "value": "1", "visible": True},
                {"id": "p4", "title": "Risk-on liquidity", "value": "0", "visible": True},
            ]
        },
    )
    adapter = _FakeTVAdapter(macro_conclusion)
    d = run_pine_trinity_loop(adapter, _entry_bar())
    assert d.pine_signal == PineSignal.RISK_OFF
    assert d.pine_confidence == 0.85
    assert d.confidence_trusted is True
    assert d.override is True
    assert d.final_action_type == RiskActionType.HOLD
    assert d.reason == "pine_risk_off_override"


# ===================== Pipeline 节点 =====================
def test_pine_trinity_node_stages_decision() -> None:
    adapter = _FakeTVAdapter(PineConclusionSchema(signal="risk_off", confidence=0.9))
    node = PineTrinityNode(adapter, bar_data=_entry_bar())
    ctx = PipelineContext()
    ok = node.execute(ctx)
    assert ok is True
    sig = ctx.data["signals"]["pine_trinity"]
    assert sig["final_action"] == "HOLD"
    assert sig["pine_signal"] == "RISK_OFF"
    assert sig["override"] is True


@pytest.mark.skipif(not _cdp_alive(), reason="TradingView Desktop CDP not reachable on 127.0.0.1:9222")
def test_run_loop_live_cdp() -> None:
    from adapters.tradingview import TradingViewAdapter

    adapter = TradingViewAdapter(mcp_command="node", timeout_seconds=40)
    d = run_pine_trinity_loop(
        adapter,
        _entry_bar(),
        script_name=LIVE_SCRIPT,
        symbol="TVC:GOLD",
    )
    assert isinstance(d, PineTrinityDecision)
    assert isinstance(d.final_action_type, RiskActionType)
    assert d.reason  # 总有归因理由
