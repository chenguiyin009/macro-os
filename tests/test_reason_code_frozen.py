"""Reason-code freeze guard (review P1 #5).

`decide()` and `evaluate_physical_red_lines()` may only emit reason codes from the frozen set
documented in docs/2026-07-03-decision-kernel-v5-design.md §11. Any new code must update BOTH
that doc and this test, so the set cannot drift silently.
"""
from __future__ import annotations

from core.decision_kernel import decide
from core.macro.physical_red_lines import evaluate_physical_red_lines

# Mirrors docs/.../§11 exactly. If a test below emits a code not in this set, the freeze is broken.
FROZEN_REASON_CODES = {
    "SAFETY_CRISIS",
    "SAFETY_LATE",
    "SAFETY_MID",
    "SAFETY_EARLY",
    "RECOVERY_TIME_LOCK_ACTIVE",
    "RECOVERY_RAMP_ACTIVE",
    "VETO_REGIME_TRANSITION_ACTIVE",
    "VETO_REGIME_SQUEEZE_ACTIVE",
    "VETO_REGIME_TIGHT_ACTIVE",
    "VETO_REGIME_UNDEFINED",
    "SOFT_POLICY_NORMAL",
    "GLOBAL_RAMP_ACTIVE",
    "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH",
    "PHYSICAL_RED_LINE_HY_CREDIT_SPREAD_BP",
    "PHYSICAL_RED_LINE_CORE_PCE_MAX",
    "PHYSICAL_RED_LINE_BRENT_RED_LINE",
}

# red-line config key -> feature key (inline copy of physical_red_lines._RED_LINE_FEATURE_KEY,
# kept local so this guard does not depend on a private symbol).
_RED_LINE_FEATURE_KEY = {
    "vix_escape_hatch": "vix",
    "hy_credit_spread_bp": "hy_credit_spread",
    "brent_red_line": "brent_shock",
    "core_pce_max": "core_pce",
}


def _decide(**over) -> object:
    kw = dict(
        features={},
        hard_regime="RISK_ON",
        soft_regime_label="RISK_ON",
        risk_score=0.5,
        confidence=0.8,
        divergence_phase="",
        recovery_active=False,
        previous_risk_budget=0.5,
        days_in_recovery=0,
    )
    kw.update(over)
    return decide(**kw)


def test_kernel_reason_codes_within_frozen_set() -> None:
    cases = [
        dict(divergence_phase="CRISIS"),
        dict(divergence_phase="LATE", recovery_active=True, days_in_recovery=1, previous_risk_budget=0.55),
        dict(divergence_phase="LATE", recovery_active=True, days_in_recovery=5, previous_risk_budget=0.55),
        dict(divergence_phase="MID", recovery_active=True, days_in_recovery=1, previous_risk_budget=0.55),
        dict(divergence_phase="MID", recovery_active=True, days_in_recovery=5, previous_risk_budget=0.55),
        dict(divergence_phase="EARLY", previous_risk_budget=0.50),
        dict(hard_regime="TRANSITION"),
        dict(hard_regime="LIQUIDITY_SQUEEZE"),
        dict(hard_regime="TIGHT_LIQUIDITY"),
        dict(hard_regime="RISK_ON", risk_score=0.9, previous_risk_budget=0.50),
        dict(hard_regime="RISK_ON", risk_score=0.1, previous_risk_budget=0.50),
        dict(hard_regime="RISK_ON", risk_score=0.5, previous_risk_budget=0.50),
        dict(hard_regime="RISK_ON", risk_score=0.9, previous_risk_budget=0.30),
    ]
    for c in cases:
        kd = _decide(**c)
        assert kd.reason_code in FROZEN_REASON_CODES, (
            f"undocumented kernel reason_code: {kd.reason_code!r} (case={c})"
        )


def test_physical_red_line_reason_codes_within_frozen_set() -> None:
    for key, feat in _RED_LINE_FEATURE_KEY.items():
        v = evaluate_physical_red_lines({feat: 1e9}, {key: 1.0})
        if v.triggered:
            assert v.reason_code in FROZEN_REASON_CODES, (
                f"undocumented red-line reason_code: {v.reason_code!r}"
            )
