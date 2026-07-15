"""dry_run 红线可观测性：与主路径一致的 meta / payload 通道。"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.schemas import AuthorityLevel, DataSource, FeatureSchema, RegimeType
from runtime.orchestrator import Orchestrator


def _make_orch(monkeypatch, config=None):
    from runtime import orchestrator as orch_module

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
    monkeypatch.setattr(orch_module, "map_phase", lambda score: "")  # empty phase so HARD_VETO path is visible

    class FakeShadowEngine:
        def update_daily(self, *a, **k):
            pass

        def generate_counterfactual_report(self):
            return ""

    monkeypatch.setattr(orch_module, "ShadowEngine", FakeShadowEngine, raising=False)

    tv = MagicMock()
    vault = MagicMock()
    vault.count_events.return_value = 0
    feishu = MagicMock()
    futu = MagicMock()
    futu.fetch_positions.return_value = SimpleNamespace(to_dict=lambda: {})
    cfg = {"ENABLE_RISK_GATEWAY": False}
    if config:
        cfg.update(config)
    return Orchestrator(
        tradingview=tv,
        vault=vault,
        feishu=feishu,
        futu=futu,
        config=cfg,
    )


def test_dry_run_red_line_payload_and_health(monkeypatch) -> None:
    orch = _make_orch(monkeypatch)
    orch.tv.fetch.return_value = FeatureSchema(
        source=DataSource.MOCK,
        fetched_at=datetime.now(),
        vix=45.0,
        hy_credit_spread=300.0,
        tips_yield=0.2,
        dxy=98.0,
        danger_score=10.0,
        risk_score=0.55,
        recovery_signal=False,
    )

    kd, payload = orch.dry_run()
    assert kd.authority == AuthorityLevel.HARD_VETO
    assert payload["red_line"]["triggered"] is True
    assert payload["red_line"]["forced_hard_regime"] == "LIQUIDITY_SQUEEZE"
    assert "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH" == payload["red_line"]["reason_code"]
    assert orch.health()["last_red_line_meta"]["triggered"] is True
    # kernel 四步契约不被旁路污染
    assert "red_line" not in kd.audit_trail


def test_dry_run_safe_path_still_emits_red_line_meta(monkeypatch) -> None:
    orch = _make_orch(monkeypatch)
    orch.tv.fetch.return_value = FeatureSchema(
        source=DataSource.MOCK,
        fetched_at=datetime.now(),
        vix=15.0,
        hy_credit_spread=200.0,
        tips_yield=0.2,
        dxy=98.0,
        risk_score=0.4,
    )
    kd, payload = orch.dry_run()
    assert kd.authority != AuthorityLevel.HARD_VETO
    assert payload["red_line"]["triggered"] is False
    assert orch.health()["last_red_line_meta"]["triggered"] is False

