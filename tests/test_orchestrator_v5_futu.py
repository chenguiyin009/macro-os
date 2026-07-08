from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.schemas import AuthorityLevel, Decision, DecisionAction, FeatureSchema, KernelDecision, RegimeType


class FakeSnapshot:
    def __init__(self, weights: dict[str, float]) -> None:
        self._weights = weights

    def to_dict(self) -> dict[str, float]:
        return dict(self._weights)


class FakeFutu:
    def __init__(self) -> None:
        self.fetch_calls = 0

    def fetch_positions(self) -> FakeSnapshot:
        self.fetch_calls += 1
        return FakeSnapshot({"QQQ": 0.25, "CASH": 0.75})


class FakeTV:
    def fetch(self):
        return FeatureSchema(qqq_close=100.0)


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

    def health(self):
        return "ok"


class FakeCio:
    def __init__(self) -> None:
        self.calls = []

    def generate_daily_plan(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "daily-plan"


class FakeSectorAllocator:
    def load_state(self) -> None:
        self.loaded = True


shadow_instances: list["FakeShadowEngine"] = []


class FakeShadowEngine:
    def __init__(self) -> None:
        self.calls = []
        shadow_instances.append(self)

    def update_daily(self, market_data, baseline_weights) -> None:
        self.calls.append((dict(market_data), dict(baseline_weights)))

    def generate_counterfactual_report(self) -> str:
        return "shadow report"


def test_orchestrator_uses_strict_futu_snapshot_and_dict_diff(monkeypatch) -> None:
    from runtime import orchestrator as orch_module

    assert orch_module.FutuSensor.__module__ == "adapters.futu"
    monkeypatch.setattr(orch_module, "ShadowEngine", FakeShadowEngine, raising=False)

    monkeypatch.setattr(orch_module, "build_features", lambda raw: {"risk_score": 0.5, "recovery_signal": False})
    monkeypatch.setattr(
        orch_module,
        "compute_macro_state",
        lambda features: SimpleNamespace(quadrant=RegimeType.RISK_ON.value),
    )

    class FakeDivergencePhaseEngine:
        def __init__(self, use_pine_data=False) -> None:
            self.use_pine_data = use_pine_data

        def compute_state(self, features, vix):
            return SimpleNamespace(score=0.1)

    monkeypatch.setattr(orch_module, "DivergencePhaseEngine", FakeDivergencePhaseEngine)
    monkeypatch.setattr(orch_module, "map_phase", lambda score: "EARLY")
    monkeypatch.setattr(
        orch_module,
        "kernel_decide",
        lambda **kwargs: KernelDecision(
            authority=AuthorityLevel.SOFT_POLICY,
            decision=Decision(action=DecisionAction.NEUTRAL, regime=RegimeType.RISK_ON),
            hard_regime=RegimeType.RISK_ON.value,
            soft_regime_label=RegimeType.RISK_ON.value,
            risk_budget=0.6,
            defense_budget=0.4,
        ),
    )

    captured = {}

    def fake_diff(target_weights, actual_weights):
        captured["target"] = dict(target_weights)
        captured["actual"] = dict(actual_weights)
        return {"QQQ": 0.10, "CASH": -0.10}

    monkeypatch.setattr(orch_module, "compute_actionable_diff", fake_diff)

    futu = FakeFutu()
    cio = FakeCio()
    orchestrator = orch_module.Orchestrator(
        tradingview=FakeTV(),
        vault=FakeVault(),
        feishu=FakeFeishu(),
        futu=futu,
        cio_agent=cio,
        sector_allocator=FakeSectorAllocator(),
    )

    decision = orchestrator.run_pipeline()

    assert orchestrator.futu is futu
    assert futu.fetch_calls == 1
    assert captured["target"] == {"QQQ": 0.6, "CASH": 0.4}
    assert captured["actual"] == {"QQQ": 0.25, "CASH": 0.75}
    assert shadow_instances[0].calls[0][0]["qqq_close"] == 100.0
    assert shadow_instances[0].calls[0][1] == {"QQQ": 0.6, "CASH": 0.4}
    assert cio.calls[0][0] == ()
    assert cio.calls[0][1]["shadow_report"] == "shadow report"
    assert cio.calls[0][1]["allocation"] == {"QQQ": 0.6, "CASH": 0.4}
    assert decision.risk_budget == pytest.approx(0.6)
