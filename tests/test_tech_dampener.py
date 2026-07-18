"""C-grade Tech Drawdown Dampener (v5.1, 2026-07-18).

Locks the microstructural cap behavior in core.decision_kernel.decide():
  - dormant when L1/L2 does not supply features['tech_drawdown']
  - C-grade tiers: <= -0.07 -> 0.65, <= -0.10 -> 0.50, <= -0.13 -> 0.35
  - always min(budget, cap): never raises a budget, never overrides HARD_VETO
  - annotated via audit_trail['step_2c_tech_dampener'], reason_code untouched
"""
from __future__ import annotations

from core.decision_kernel import decide


def _run(tech_dd, hard_regime="RISK_ON", risk_score=0.8, previous_risk_budget=0.9):
    features = {"tech_drawdown": tech_dd} if tech_dd is not None else {}
    return decide(
        features=features,
        hard_regime=hard_regime,
        soft_regime_label=hard_regime,
        risk_score=risk_score,
        confidence=0.8,
        previous_risk_budget=previous_risk_budget,
    )


def test_dormant_when_absent():
    kd = _run(None)
    assert kd.risk_budget == 0.80
    assert "step_2c_tech_dampener" not in kd.audit_trail


def test_no_cap_above_threshold():
    kd = _run(-0.05)
    assert kd.risk_budget == 0.80
    assert "step_2c_tech_dampener" not in kd.audit_trail


def test_cap_tier1_neg07():
    kd = _run(-0.08)
    assert kd.risk_budget == 0.65
    note = kd.audit_trail["step_2c_tech_dampener"]
    assert note["active"] is True
    assert note["cap"] == 0.65
    assert note["post_cap_budget"] == 0.65


def test_cap_tier2_neg10():
    kd = _run(-0.11)
    assert kd.risk_budget == 0.50
    assert kd.audit_trail["step_2c_tech_dampener"]["cap"] == 0.50


def test_cap_tier3_neg13():
    kd = _run(-0.14)
    assert kd.risk_budget == 0.35
    assert kd.audit_trail["step_2c_tech_dampener"]["cap"] == 0.35


def test_subordinate_to_hard_veto():
    # Deep tech drawdown must NOT override a liquidity-squeeze hard veto.
    kd = decide(
        features={"tech_drawdown": -0.20, "vix": 45.0},
        hard_regime="LIQUIDITY_SQUEEZE",
        soft_regime_label="LIQUIDITY_SQUEEZE",
        risk_score=0.5,
        confidence=0.5,
        previous_risk_budget=0.5,
    )
    assert kd.risk_budget == 0.0
    assert "step_2c_tech_dampener" not in kd.audit_trail


def test_calm_market_unchanged():
    # tech_drawdown at 0 (no stress) leaves the soft-policy budget intact.
    kd = _run(0.0)
    assert kd.risk_budget == 0.80
    assert "step_2c_tech_dampener" not in kd.audit_trail
