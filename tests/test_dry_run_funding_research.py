"""Dry-run research quadrant alignment with weekly funding-price narrative."""

from __future__ import annotations

from runtime.main import build_orchestrator


def test_mock_dry_run_research_block_is_q1(monkeypatch) -> None:
    monkeypatch.setenv("MACRO_OS_FRED_ENABLED", "0")
    orch = build_orchestrator()
    orch.config["USE_RESEARCH_QUADRANT_HINT"] = True
    kd, payload = orch.dry_run()
    research = payload["funding_price_research"]
    assert research["quadrant"] == "Q1_STRESS_TEST"
    assert research["hard_regime_hint"] == "TIGHT_LIQUIDITY"
    assert research["hard_regime_hint"] != "LIQUIDITY_SQUEEZE"
    assert kd.hard_regime in {"TIGHT_LIQUIDITY", "TRANSITION", "LIQUIDITY_SQUEEZE", "RISK_ON"}
    assert kd.hard_regime == "TIGHT_LIQUIDITY" or research["confidence"] < 0.55
    assert "red_line" in payload
    assert payload["red_line"]["absolute_override"] is False


def test_research_hint_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MACRO_OS_FRED_ENABLED", "0")
    orch = build_orchestrator()
    orch.config["USE_RESEARCH_QUADRANT_HINT"] = False
    kd, payload = orch.dry_run()
    assert payload["funding_price_research"]["quadrant"] == "Q1_STRESS_TEST"
    assert "hard_regime_hint" in payload["funding_price_research"]
