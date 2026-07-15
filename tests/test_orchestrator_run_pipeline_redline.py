"""PR-1 主路径可观测性：run_pipeline 必须把物理红线触发原因暴露到编排层可观测通道

- Event 顶层 payload `red_line`（vault / event 消费方可直接读取）
- 飞书消息横幅（避免线上只能从日志猜）
- health() 的 `last_red_line_meta` 快照

注意：内核的 `KernelDecision.audit_trail` 四步契约（step_1..step_4）**刻意不被触碰**，
红线信息只走编排层通道，避免污染内核契约（见 review P0 #2）。

此前只有 dry_run 被测试；本文件锁定主路径（run_pipeline）fold。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.schemas import AuthorityLevel, Decision, DecisionAction, FeatureSchema, KernelDecision, RegimeType


class FakeVault:
    def __init__(self) -> None:
        self.appended = []

    def append(self, event):
        self.appended.append(event)
        return True

    def count_events(self) -> int:
        return 0


class FakeFeishu:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, title, text):
        self.sent.append((title, text))

    def send_alert(self, msg):
        self.sent.append(("ALERT", msg))

    def health(self):
        return "ok"


class FakeCio:
    def generate_daily_plan(self, *args, **kwargs):
        return "daily-plan"


class FakeSectorAllocator:
    def load_state(self) -> None:
        self.loaded = True


class _TVWithVix:
    def __init__(self, vix: float) -> None:
        self._vix = vix

    def fetch(self):
        return FeatureSchema(vix=self._vix)

    def health(self):
        return "ok"


class FakeShadowEngine:
    def update_daily(self, market_data, baseline_weights) -> None:
        pass

    def generate_counterfactual_report(self) -> str:
        return "shadow report"


class FakeFutu:
    def fetch_positions(self):
        return SimpleNamespace(to_dict=lambda: {"QQQ": 0.25, "CASH": 0.75})


def _build_orchestrator(monkeypatch, vix: float):
    from runtime import orchestrator as orch_module

    monkeypatch.setattr(orch_module, "ShadowEngine", FakeShadowEngine, raising=False)
    monkeypatch.setattr(
        orch_module,
        "compute_macro_state",
        lambda features: SimpleNamespace(quadrant=RegimeType.RISK_ON.value),
    )

    class FakeDivergencePhaseEngine:
        def __init__(self, use_pine_data=False) -> None:
            pass

        def compute_state(self, features, vix):
            return SimpleNamespace(score=0.1)

    monkeypatch.setattr(orch_module, "DivergencePhaseEngine", FakeDivergencePhaseEngine)
    monkeypatch.setattr(orch_module, "map_phase", lambda score: "EARLY")

    def fake_kernel(**kwargs):
        hard_regime = kwargs.get("hard_regime")
        if hard_regime != RegimeType.RISK_ON.value:
            return KernelDecision(
                authority=AuthorityLevel.HARD_VETO,
                decision=Decision(action=DecisionAction.REDUCE, regime=RegimeType.LIQUIDITY_SQUEEZE),
                hard_regime=hard_regime,
                risk_budget=0.0,
                defense_budget=1.0,
            )
        return KernelDecision(
            authority=AuthorityLevel.SOFT_POLICY,
            decision=Decision(action=DecisionAction.NEUTRAL, regime=RegimeType.RISK_ON),
            hard_regime=RegimeType.RISK_ON.value,
            risk_budget=0.6,
            defense_budget=0.4,
        )

    monkeypatch.setattr(orch_module, "kernel_decide", fake_kernel)
    monkeypatch.setattr(
        orch_module, "compute_actionable_diff", lambda t, a: {"QQQ": 0.0, "CASH": 0.0}
    )

    # 关闭风控网关，隔离红线 fold 的可观测性验证（网关路径另行覆盖）
    orchestrator = orch_module.Orchestrator(
        tradingview=_TVWithVix(vix),
        vault=FakeVault(),
        feishu=FakeFeishu(),
        futu=FakeFutu(),
        cio_agent=FakeCio(),
        sector_allocator=FakeSectorAllocator(),
        config={"ENABLE_RISK_GATEWAY": False},
    )
    return orchestrator


def test_run_pipeline_red_line_in_event_payload_and_feishu(monkeypatch) -> None:
    orch = _build_orchestrator(monkeypatch, vix=41.0)
    decision = orch.run_pipeline()

    assert decision is not None
    assert decision.authority == AuthorityLevel.HARD_VETO

    # 1) Event 顶层 payload 挂了红线快照
    event = orch.vault.appended[0]
    red = event.payload["red_line"]
    assert red["triggered"] is True
    assert red["reason_code"] == "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH"
    assert red["forced_hard_regime"] == "LIQUIDITY_SQUEEZE"

    # 2) 飞书消息含红线横幅
    _title, text = orch.feishu.sent[0]
    assert "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH" in text

    # 3) health 快照可见
    assert orch.health()["last_red_line_meta"]["triggered"] is True

    # 内核四步 audit_trail 契约不被红线触碰（P0 #2）：不出现 red_line 附加键
    assert "red_line" not in decision.audit_trail


def test_run_pipeline_no_red_line_when_safe(monkeypatch) -> None:
    orch = _build_orchestrator(monkeypatch, vix=20.0)
    decision = orch.run_pipeline()

    assert decision is not None
    assert decision.authority == AuthorityLevel.SOFT_POLICY

    event = orch.vault.appended[0]
    red = event.payload["red_line"]
    assert red["triggered"] is False  # 顶层键始终存在，仅标记未触发
    assert "red_line" not in decision.audit_trail  # 未触发则不在 audit_trail
    assert orch.health()["last_red_line_meta"]["triggered"] is False
