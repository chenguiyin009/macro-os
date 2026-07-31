"""Phase 2 Cross-Asset Theme AND-gate (v5.1, 2026-07-21).

Locks the AND-gate soft-cap behavior in core.decision_kernel.decide():
  - dormant when L1/L2 does not supply features['theme_pressure_level'] (default 0)
  - gate opens ONLY when level>=2 AND (structural weak OR macro subopt)
  - structural weak = SOXX(tech_drawdown) OR QQQ(qqq_drawdown) 20d dd <= -0.07
  - macro subopt = hard_regime in {TRANSITION, TIGHT_LIQUIDITY}
  - level 2 (risk_off) -> cap 0.65 ; level 3 (pressure_override) -> cap 0.50
  - always min(budget, cap): never raises a budget, never overrides HARD_VETO
  - annotated via audit_trail['step_2d_theme_pressure'], reason_code untouched
Calibrated as AND_s07_L2c65_L3c50 (scripts/backtest_theme_andgate.py).
"""
from __future__ import annotations

from core.decision_kernel import (
    AuthorityLevel,
    _apply_theme_pressure,
    decide,
)


# ---------------------------------------------------------------------------
# Pure-function unit tests (gate logic isolated from regime budgets)
# ---------------------------------------------------------------------------
def test_pure_dormant_level_low():
    capped, note = _apply_theme_pressure(0.80, AuthorityLevel.SOFT_POLICY, 1, 0.0, 0.0, "RISK_ON")
    assert note["gate_open"] is False
    assert capped == 0.80


def test_pure_gate_risk_off_struct_weak():
    capped, note = _apply_theme_pressure(0.80, AuthorityLevel.SOFT_POLICY, 2, -0.08, 0.0, "RISK_ON")
    assert note["gate_open"] is True
    assert note["struct_weak"] is True
    assert capped == 0.65
    assert note["active"] is True


def test_pure_gate_pressure_override_struct_weak():
    capped, note = _apply_theme_pressure(0.80, AuthorityLevel.SOFT_POLICY, 3, -0.08, 0.0, "RISK_ON")
    assert note["gate_open"] is True
    assert capped == 0.50


def test_pure_gate_qqq_leg():
    # SOXX flat, but QQQ broke -> structural weakness via the QQQ leg.
    capped, note = _apply_theme_pressure(0.80, AuthorityLevel.SOFT_POLICY, 2, 0.0, -0.09, "RISK_ON")
    assert note["struct_weak"] is True
    assert capped == 0.65


def test_pure_gate_macro_subopt_only():
    # No structural weakness, but macro suboptimality (TRANSITION) opens the gate.
    capped, note = _apply_theme_pressure(0.80, AuthorityLevel.SAFETY_GATE, 2, 0.0, 0.0, "TRANSITION")
    assert note["gate_open"] is True
    assert note["macro_subopt"] is True
    assert capped == 0.65


def test_pure_gate_closed_when_level_ok_and_calm():
    # level 2 but calm macro AND no structural weakness -> gate stays shut.
    capped, note = _apply_theme_pressure(0.80, AuthorityLevel.SOFT_POLICY, 2, 0.0, 0.0, "RISK_ON")
    assert note["gate_open"] is False
    assert capped == 0.80


def test_pure_subordinate_to_hard_veto():
    # Even with a deep break, a HARD_VETO authority must not mark the gate active.
    capped, note = _apply_theme_pressure(0.80, AuthorityLevel.HARD_VETO, 3, -0.20, -0.20, "RISK_ON")
    assert note["active"] is False


# ---------------------------------------------------------------------------
# Kernel integration tests (real decide() path)
# ---------------------------------------------------------------------------
def _run(level, soxx_dd=None, qqq_dd=None, hard_regime="RISK_ON", risk_score=0.8, previous=0.9):
    features = {}
    if level is not None:
        features["theme_pressure_level"] = level
    if soxx_dd is not None:
        features["tech_drawdown"] = soxx_dd
    if qqq_dd is not None:
        features["qqq_drawdown"] = qqq_dd
    return decide(
        features=features,
        hard_regime=hard_regime,
        soft_regime_label=hard_regime,
        risk_score=risk_score,
        confidence=0.8,
        previous_risk_budget=previous,
    )


def test_kernel_dormant_when_absent():
    kd = _run(None)
    assert kd.risk_budget == 0.80
    assert "step_2d_theme_pressure" not in kd.audit_trail


def test_kernel_no_gate_when_level_low():
    # level 1 (mixed) must NOT cap. Use soxx_dd=0.0 to isolate the AND-gate from
    # the tech dampener (which would otherwise cap a -0.20 SOXX break to 0.35).
    kd = _run(1, soxx_dd=0.0)
    assert kd.risk_budget == 0.80
    assert "step_2d_theme_pressure" not in kd.audit_trail


def test_kernel_gate_risk_off_struct_weak():
    # L2 + structural weakness opens the AND-gate -> 0.65. Use the QQQ leg
    # (soxx_dd=0.0) so the SOXX-driven tech dampener does NOT pre-empt the cap
    # with the same 0.65 (which would make the AND-gate redundant and note-less).
    kd = _run(2, soxx_dd=0.0, qqq_dd=-0.08)
    assert kd.risk_budget == 0.65
    note = kd.audit_trail["step_2d_theme_pressure"]
    assert note["active"] is True
    assert note["cap"] == 0.65
    assert note["post_cap_budget"] == 0.65


def test_kernel_gate_pressure_override_struct_weak():
    kd = _run(3, soxx_dd=-0.08)
    assert kd.risk_budget == 0.50
    assert kd.audit_trail["step_2d_theme_pressure"]["cap"] == 0.50


def test_kernel_gate_qqq_leg_only():
    kd = _run(2, soxx_dd=0.0, qqq_dd=-0.09)
    assert kd.risk_budget == 0.65
    note = kd.audit_trail["step_2d_theme_pressure"]
    assert note["struct_weak"] is True
    assert note["cap"] == 0.65


def test_kernel_no_gate_calm_with_high_level():
    # level 2 but calm macro AND no structural weakness -> gate shut, budget intact.
    kd = _run(2, soxx_dd=0.0, qqq_dd=0.0, hard_regime="RISK_ON")
    assert kd.risk_budget == 0.80
    assert "step_2d_theme_pressure" not in kd.audit_trail


def test_kernel_subordinate_to_hard_veto():
    # A HARD_VETO regime (CASH_LIQUIDATION) pins budget at 0.0 regardless of how
    # deep the break or how high the theme level. The AND-gate is never applied on
    # the veto path, so its audit note must be absent.
    kd = decide(
        features={"theme_pressure_level": 3, "tech_drawdown": -0.20},
        hard_regime="CASH_LIQUIDATION",
        soft_regime_label="CASH_LIQUIDATION",
        risk_score=0.5,
        confidence=0.5,
        previous_risk_budget=0.5,
    )
    assert kd.risk_budget == 0.0
    assert "step_2d_theme_pressure" not in kd.audit_trail


def test_kernel_audit_note_present_only_when_active():
    # SOXX break but level 0 -> no AND-gate note (only the tech dampener may fire).
    kd = _run(0, soxx_dd=-0.08)
    assert kd.risk_budget == 0.65  # tech dampener still caps
    assert "step_2d_theme_pressure" not in kd.audit_trail
