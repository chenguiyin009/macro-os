"""ADR-001 Option B: absolute physical red line via phase neutralization.

When a red line fires, orchestrator must pass empty phase_for_kernel so HARD_VETO
governs even if structural phase_raw is EARLY/LATE/MID. Event/CIO keep phase_raw.
Same-day sticky lock prevents VIX hatch chatter.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.decision_kernel import decide
from core.schemas import AuthorityLevel, DataSource, FeatureSchema, RegimeType
from runtime.orchestrator import Orchestrator


CONFIG = {
    "decision": {
        "long_confidence_min": 0.60,
        "short_confidence_min": 0.65,
        "no_trade_confidence_max": 0.35,
        "reduce_threshold": 0.30,
    }
}


def test_kernel_without_neutralize_early_plus_squeeze_is_safety() -> None:
    """Documents pre-ADR bug class: EARLY + hostile regime never reaches HARD_VETO."""
    kd = decide(
        {"vix": 45.0},
        "LIQUIDITY_SQUEEZE",
        "RISK_ON",
        0.7,
        0.8,
        CONFIG,
        divergence_phase="EARLY",
        previous_risk_budget=0.5,
    )
    assert kd.authority == AuthorityLevel.SAFETY_GATE
    assert kd.risk_budget > 0.0


def test_kernel_with_neutralized_phase_is_hard_veto() -> None:
    kd = decide(
        {"vix": 45.0},
        "LIQUIDITY_SQUEEZE",
        "RISK_ON",
        0.7,
        0.8,
        CONFIG,
        divergence_phase="",
        previous_risk_budget=0.5,
    )
    assert kd.authority == AuthorityLevel.HARD_VETO
    assert kd.risk_budget == 0.0
    assert kd.defense_budget == 1.0
    assert list(kd.audit_trail.keys()) == [
        "step_1_safety_gate",
        "step_2_hard_veto",
        "step_3_soft_policy",
        "step_4_global_velocity_limit",
    ]


def test_fold_kernel_inputs_absolute_clears_phase() -> None:
    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=MagicMock(),
        feishu=MagicMock(),
        futu=MagicMock(),
        config={"ENABLE_RISK_GATEWAY": False},
    )
    red = SimpleNamespace(
        triggered=True,
        forced_hard_regime="LIQUIDITY_SQUEEZE",
        reason_code="PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH",
        triggered_lines=["vix_escape_hatch"],
    )
    hard, phase_k, conf, meta = orch._fold_kernel_inputs(
        {"vix": 45.0}, "RISK_ON", "LATE", red, confirmation_status="DIVERGED"
    )
    assert hard == "LIQUIDITY_SQUEEZE"
    assert phase_k == ""
    assert conf == ""
    assert meta["phase_raw"] == "LATE"
    assert meta["phase_for_kernel"] == ""
    assert meta["absolute_override"] is True


def test_fold_kernel_inputs_safe_keeps_phase() -> None:
    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=MagicMock(),
        feishu=MagicMock(),
        futu=MagicMock(),
        config={"ENABLE_RISK_GATEWAY": False},
    )
    red = SimpleNamespace(
        triggered=False,
        forced_hard_regime=None,
        reason_code="",
        triggered_lines=[],
    )
    hard, phase_k, conf, meta = orch._fold_kernel_inputs(
        {"vix": 15.0}, "RISK_ON", "LATE", red, confirmation_status="DIVERGED"
    )
    assert hard == "RISK_ON"
    assert phase_k == "LATE"
    assert conf == "DIVERGED"
    assert meta["absolute_override"] is False


def _make_orch(monkeypatch, phase: str = "EARLY"):
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
    monkeypatch.setattr(orch_module, "map_phase", lambda score: phase)

    class FakeShadowEngine:
        def update_daily(self, *a, **k):
            pass

        def generate_counterfactual_report(self):
            return ""

    monkeypatch.setattr(orch_module, "ShadowEngine", FakeShadowEngine, raising=False)

    tv = MagicMock()
    vault = MagicMock()
    vault.count_events.return_value = 0
    vault.append = MagicMock(return_value=True)
    feishu = MagicMock()
    futu = MagicMock()
    futu.fetch_positions.return_value = SimpleNamespace(to_dict=lambda: {"QQQ": 0.2, "CASH": 0.8})
    return Orchestrator(
        tradingview=tv,
        vault=vault,
        feishu=feishu,
        futu=futu,
        config={"ENABLE_RISK_GATEWAY": False},
    )


def test_dry_run_early_plus_vix_is_absolute_hard_veto(monkeypatch) -> None:
    orch = _make_orch(monkeypatch, phase="EARLY")
    orch.tv.fetch.return_value = FeatureSchema(
        source=DataSource.MOCK,
        fetched_at=datetime.now(),
        vix=45.0,
        hy_credit_spread=200.0,
        tips_yield=0.2,
        dxy=98.0,
    )
    kd, payload = orch.dry_run()
    assert kd.authority == AuthorityLevel.HARD_VETO
    assert kd.risk_budget == 0.0
    assert payload["divergence_phase"] == "EARLY"
    assert payload["divergence_phase_for_kernel"] == ""
    assert payload["red_line"]["absolute_override"] is True
    assert payload["red_line"]["phase_raw"] == "EARLY"
    assert payload["red_line"]["phase_for_kernel"] == ""
    assert "red_line" not in kd.audit_trail


def test_dry_run_late_recovery_plus_vix_is_hard_veto(monkeypatch) -> None:
    orch = _make_orch(monkeypatch, phase="LATE")
    orch.tv.fetch.return_value = FeatureSchema(
        source=DataSource.MOCK,
        fetched_at=datetime.now(),
        vix=45.0,
        hy_credit_spread=200.0,
        recovery_signal=True,
    )
    kd, payload = orch.dry_run()
    assert kd.authority == AuthorityLevel.HARD_VETO
    assert kd.risk_budget == 0.0
    assert payload["red_line"]["phase_raw"] == "LATE"


def test_dry_run_late_without_red_line_stays_safety(monkeypatch) -> None:
    orch = _make_orch(monkeypatch, phase="LATE")
    orch.tv.fetch.return_value = FeatureSchema(
        source=DataSource.MOCK,
        fetched_at=datetime.now(),
        vix=20.0,
        hy_credit_spread=200.0,
        recovery_signal=True,
    )
    kd, payload = orch.dry_run()
    assert kd.authority == AuthorityLevel.SAFETY_GATE
    assert payload["divergence_phase"] == "LATE"
    assert payload["divergence_phase_for_kernel"] == "LATE"
    assert payload["red_line"]["absolute_override"] is False


def test_same_day_sticky_lock_after_red_line(monkeypatch) -> None:
    orch = _make_orch(monkeypatch, phase="EARLY")
    orch.tv.fetch.return_value = FeatureSchema(
        source=DataSource.MOCK,
        fetched_at=datetime.now(),
        vix=45.0,
    )
    kd1, p1 = orch.dry_run()
    assert kd1.authority == AuthorityLevel.HARD_VETO
    assert p1["red_line"]["sticky_day_lock"] is False

    # VIX cools under hatch; same-day lock must keep absolute fold
    orch.tv.fetch.return_value = FeatureSchema(
        source=DataSource.MOCK,
        fetched_at=datetime.now(),
        vix=20.0,
    )
    kd2, p2 = orch.dry_run()
    assert kd2.authority == AuthorityLevel.HARD_VETO
    assert kd2.risk_budget == 0.0
    assert p2["red_line"]["triggered"] is True
    assert p2["red_line"]["sticky_day_lock"] is True


def test_cio_report_mentions_phase_raw_under_absolute_red_line() -> None:
    from core.agents.cio_agent import CIOAgent

    report = CIOAgent().generate(
        regime_probs={},
        allocation={"QQQ": 0.0, "CASH": 1.0},
        diff_report=None,
        shadow_report="",
        features_summary=FeatureSchema(vix=45.0),
        macro_narrative="当前结构相位 phase_raw=LATE，但受绝对物理红线压制",
        red_line_meta={
            "absolute_override": True,
            "phase_raw": "LATE",
            "phase_for_kernel": "",
            "reason_code": "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH",
            "sticky_day_lock": False,
        },
    )
    assert "phase_raw=LATE" in report
    assert "HARD_VETO" in report
    assert "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH" in report
