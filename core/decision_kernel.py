"""Macro OS v5.0 - Constitutional kernel with graduated risk budget mapping.

Kernel reads MacroState confirmation_status but NEVER writes it.
Authority hierarchy:
1. SAFETY_GATE (divergence): macro divergence -> force RISK_REDUCE
2. GRADUATED (non-crisis regimes): TRANSITION / TIGHT_LIQUIDITY get non-zero budget
3. HARD_VETO (crisis regimes): LIQUIDITY_SQUEEZE / CASH_LIQUIDATION -> 0% budget
4. SOFT_POLICY: RISK_ON only (AGGRESSIVE/DEFENSIVE/NEUTRAL)

ADR-001 absolute physical red lines override ALL of the above.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.schemas import (
    AuthorityLevel, Decision, DecisionAction, KernelDecision,
    RegimeType,
)

# Constants
RECOVERY_OBSERVATION_DAYS = 3
MAX_DAILY_RISK_LIFT = 0.10

# P0-2 (v5.1): crisis graduated re-entry thresholds.
# Each tuple: (vix_max, hy_spread_max, released_budget).
# Only consulted while effective_hard_regime == LIQUIDITY_SQUEEZE (red line still
# active). Releases a small budget as VIX/HY ease WITHIN the squeeze envelope, so
# the system scales in instead of hard zero-then-jump. CASH_LIQUIDATION stays 0.0.
CRISIS_GRADUATED_THRESHOLDS = [
    (35.0, 420.0, 0.05),
    (28.0, 380.0, 0.10),
    (22.0, 340.0, 0.20),
]


# C-grade microstructural dampener (v5.1, 2026-07-18).
# Soft structural cap driven by a tech-sector drawdown feature. Reads the generic
# `tech_drawdown` supplied by L1/L2 (e.g. SOXX 20d peak-to-trough, -0.08 == -8%).
# It is MICROSTRUCTURAL and fully subordinate to the macro HARD_VETO / zero-budget
# crisis paths: it only ever *lowers* a positive budget via min(), never raises and
# never overrides a veto. When L1/L2 does not supply `tech_drawdown` (defaults 0.0),
# the cap is inactive (1.0) and kernel behavior is unchanged.
TECH_DRAWDOWN_CAPS: Tuple[Tuple[float, float], ...] = (
    (-0.13, 0.35),
    (-0.10, 0.50),
    (-0.07, 0.65),
)


def _tech_dampener_cap(tech_dd: float) -> float:
    """Structural cap implied by a tech-sector drawdown, or 1.0 when inactive."""
    for threshold, cap in TECH_DRAWDOWN_CAPS:
        if tech_dd <= threshold:
            return cap
    return 1.0


def _apply_tech_dampener(
    risk_budget: float,
    authority: "AuthorityLevel",
    tech_dd: float,
    dd_cap: float,
) -> Tuple[float, Dict[str, Any]]:
    """Cap a positive, non-veto budget by the tech-drawdown structural cap.

    Returns ``(capped_budget, audit_note)``. ``audit_note["active"]`` is True only
    when the cap actually lowered the budget (so callers attach it conditionally).
    """
    active = (authority != AuthorityLevel.HARD_VETO) and (dd_cap < risk_budget)
    capped = min(risk_budget, dd_cap)
    note: Dict[str, Any] = {
        "active": active,
        "tech_drawdown": round(tech_dd, 4),
        "cap": dd_cap,
        "pre_cap_budget": round(risk_budget, 4),
        "post_cap_budget": round(capped, 4),
    }
    return capped, note


def decide(
    features: Dict[str, Any],
    hard_regime: str,
    soft_regime_label: str,
    risk_score: float,
    confidence: float,
    config: Optional[Dict[str, Any]] = None,
    confirmation_status: str = "",
    divergence_phase: str = "",
    divergence_score: float = 0.0,
    recovery_active: bool = False,
    budget_override: float = -1.0,
    sticky_day_lock: bool = False,
    proposed_risk: float = 0.0,
    previous_risk_budget: float = 0.5,
    days_in_recovery: int = 0,
) -> KernelDecision:
    _ = features, soft_regime_label, confidence
    risk_score = max(0.0, min(1.0, risk_score))
    # C-grade microstructural dampener: read generic tech-sector drawdown (dormant
    # unless L1/L2 supplies `tech_drawdown`). Subordinate to HARD_VETO by construction.
    tech_dd = features.get("tech_drawdown", 0.0) if isinstance(features, dict) else 0.0
    dd_cap = _tech_dampener_cap(tech_dd)
    effective_phase = divergence_phase
    if not effective_phase and confirmation_status == "DIVERGED":
        effective_phase = "MID"

    # Budget override: when >= 0, external state machine controls budget
    bud = lambda b: budget_override if budget_override >= 0 else b

    if effective_phase == "CRISIS":
        candidate_budget = bud(0.0)
        final_risk_budget = round(candidate_budget, 4)
        final_defense_budget = round(1.0 - final_risk_budget, 4)
        soft_policy_action = DecisionAction.RISK_REDUCE
        audit_trail = {
            "step_1_safety_gate": {
                "status": "TRIGGERED",
                "phase": effective_phase,
                "base_limit": candidate_budget,
            },
            "step_2_hard_veto": {
                "status": "BYPASSED" if hard_regime == RegimeType.RISK_ON.value else "TRIGGERED",
                "regime": hard_regime,
                "clamped_risk_budget": candidate_budget,
            },
            "step_3_soft_policy": {
                "status": "SUPPRESSED"
                    if hard_regime != RegimeType.RISK_ON.value
                    else "TRIGGERED" if effective_phase in ("", "NONE")
                    else "BYPASSED",
                "policy_action": soft_policy_action.value,
                "policy_budget": candidate_budget,
            },
            "step_4_global_velocity_limit": {
                "status": "BYPASSED",
                "previous_risk_budget": previous_risk_budget,
                "candidate_risk_budget": candidate_budget,
                "capped_risk_budget": final_risk_budget,
            },
        }
        return KernelDecision(authority=AuthorityLevel.SAFETY_GATE,
            decision=Decision(regime=RegimeType.TRANSITION, risk_score=risk_score, confidence=0.0, action=DecisionAction.RISK_REDUCE, reason="SAFETY GATE: CRISIS divergence - full defense"),
            hard_regime=hard_regime, soft_regime_label=soft_regime_label, risk_budget=final_risk_budget, defense_budget=final_defense_budget, veto_reason="CRISIS divergence phase - budget 0%", reason_code="SAFETY_CRISIS", audit_trail=audit_trail)
    if effective_phase == "LATE":
        candidate_budget, rec_reason = _apply_recovery_limit("LATE", recovery_active, days_in_recovery, previous_risk_budget)
        candidate_budget = round(candidate_budget, 4)
        final_defense_budget = round(1.0 - candidate_budget, 4)
        veto_msg = "LATE divergence phase - budget " + str(int(candidate_budget*100)) + "pct"
        soft_policy_action = DecisionAction.REDUCE
        audit_trail = {
            "step_1_safety_gate": {
                "status": "TRIGGERED",
                "phase": effective_phase,
                "base_limit": candidate_budget,
                "reason_code": rec_reason,
            },
            "step_2_hard_veto": {
                "status": "BYPASSED" if hard_regime == RegimeType.RISK_ON.value else "TRIGGERED",
                "regime": hard_regime,
                "clamped_risk_budget": candidate_budget,
            },
            "step_3_soft_policy": {
                "status": "SUPPRESSED"
                    if hard_regime != RegimeType.RISK_ON.value
                    else "TRIGGERED" if effective_phase in ("", "NONE")
                    else "BYPASSED",
                "policy_action": soft_policy_action.value,
                "policy_budget": candidate_budget,
            },
            "step_4_global_velocity_limit": {
                "status": "BYPASSED",
                "previous_risk_budget": previous_risk_budget,
                "candidate_risk_budget": candidate_budget,
                "capped_risk_budget": candidate_budget,
            },
        }
        candidate_budget, _dd_note = _apply_tech_dampener(candidate_budget, AuthorityLevel.SAFETY_GATE, tech_dd, dd_cap)
        if _dd_note["active"]:
            audit_trail["step_2c_tech_dampener"] = _dd_note
        return KernelDecision(authority=AuthorityLevel.SAFETY_GATE,
            decision=Decision(regime=RegimeType.TRANSITION, risk_score=risk_score, confidence=0.5, action=DecisionAction.REDUCE, reason="SAFETY GATE: LATE divergence - de-risk"),
            hard_regime=hard_regime, soft_regime_label=soft_regime_label, risk_budget=candidate_budget, defense_budget=final_defense_budget, veto_reason=veto_msg, reason_code=rec_reason, audit_trail=audit_trail)
    if effective_phase == "MID":
        candidate_budget, rec_reason = _apply_recovery_limit("MID", recovery_active, days_in_recovery, previous_risk_budget)
        candidate_budget = round(candidate_budget, 4)
        final_defense_budget = round(1.0 - candidate_budget, 4)
        veto_msg = "MID divergence phase - budget " + str(int(candidate_budget*100)) + "pct"
        soft_policy_action = DecisionAction.REDUCE
        audit_trail = {
            "step_1_safety_gate": {
                "status": "TRIGGERED",
                "phase": effective_phase,
                "base_limit": candidate_budget,
                "reason_code": rec_reason,
            },
            "step_2_hard_veto": {
                "status": "BYPASSED" if hard_regime == RegimeType.RISK_ON.value else "TRIGGERED",
                "regime": hard_regime,
                "clamped_risk_budget": candidate_budget,
            },
            "step_3_soft_policy": {
                "status": "SUPPRESSED"
                    if hard_regime != RegimeType.RISK_ON.value
                    else "TRIGGERED" if effective_phase in ("", "NONE")
                    else "BYPASSED",
                "policy_action": soft_policy_action.value,
                "policy_budget": candidate_budget,
            },
            "step_4_global_velocity_limit": {
                "status": "BYPASSED",
                "previous_risk_budget": previous_risk_budget,
                "candidate_risk_budget": candidate_budget,
                "capped_risk_budget": candidate_budget,
            },
        }
        candidate_budget, _dd_note = _apply_tech_dampener(candidate_budget, AuthorityLevel.SAFETY_GATE, tech_dd, dd_cap)
        if _dd_note["active"]:
            audit_trail["step_2c_tech_dampener"] = _dd_note
        return KernelDecision(authority=AuthorityLevel.SAFETY_GATE,
            decision=Decision(regime=RegimeType.TRANSITION, risk_score=risk_score, confidence=0.75, action=DecisionAction.REDUCE, reason="SAFETY GATE: MID divergence - controlled exposure"),
            hard_regime=hard_regime, soft_regime_label=soft_regime_label, risk_budget=candidate_budget, defense_budget=final_defense_budget, veto_reason=veto_msg, reason_code=rec_reason, audit_trail=audit_trail)
    if effective_phase == "EARLY":
        candidate_budget = 1.0 if budget_override < 0 else budget_override
        final_risk_budget, velocity_triggered = _apply_global_velocity_limit(candidate_budget, previous_risk_budget)
        final_risk_budget = round(final_risk_budget, 4)
        final_defense_budget = round(1.0 - final_risk_budget, 4)
        final_reason_code = "GLOBAL_RAMP_ACTIVE" if velocity_triggered else "SAFETY_EARLY"
        soft_policy_action = DecisionAction.NEUTRAL
        audit_trail = {
            "step_1_safety_gate": {
                "status": "TRIGGERED",
                "phase": effective_phase,
                "base_limit": candidate_budget,
                "reason_code": final_reason_code,
            },
            "step_2_hard_veto": {
                "status": "BYPASSED" if hard_regime == RegimeType.RISK_ON.value else "TRIGGERED",
                "regime": hard_regime,
                "clamped_risk_budget": candidate_budget,
            },
            "step_3_soft_policy": {
                "status": "SUPPRESSED"
                    if hard_regime != RegimeType.RISK_ON.value
                    else "TRIGGERED" if effective_phase in ("", "NONE")
                    else "BYPASSED",
                "policy_action": soft_policy_action.value,
                "policy_budget": candidate_budget,
            },
            "step_4_global_velocity_limit": {
                "status": "TRIGGERED" if velocity_triggered else "BYPASSED",
                "previous_risk_budget": previous_risk_budget,
                "candidate_risk_budget": candidate_budget,
                "capped_risk_budget": final_risk_budget,
                "reason_code": final_reason_code,
            },
        }
        final_risk_budget, _dd_note = _apply_tech_dampener(final_risk_budget, AuthorityLevel.SAFETY_GATE, tech_dd, dd_cap)
        if _dd_note["active"]:
            audit_trail["step_2c_tech_dampener"] = _dd_note
        return KernelDecision(authority=AuthorityLevel.SAFETY_GATE,
            decision=Decision(regime=RegimeType.TRANSITION, risk_score=risk_score, confidence=1.0, action=DecisionAction.NEUTRAL, reason="SAFETY GATE: EARLY divergence - watch mode"),
            hard_regime=hard_regime, soft_regime_label=soft_regime_label, risk_budget=final_risk_budget, defense_budget=final_defense_budget, veto_reason="EARLY divergence phase - watch mode", reason_code=final_reason_code, audit_trail=audit_trail)

    # 2. GRADUATED REGIME BUDGET: non-RISK_ON regimes get graduated budgets
    #    instead of binary HARD_VETO (v5.0 constitutional correction)
    if hard_regime != RegimeType.RISK_ON.value:
        authority, action, base_budget, reason, reason_code = _graduated_regime_decision(hard_regime)

        # HARD_VETO regimes (squeeze, cash_liquidation) get immediate 0.0
        if authority == AuthorityLevel.HARD_VETO:
            # P0-2 (v5.1): gradual re-entry during SQUEEZE easing tail.
            # Only LIQUIDITY_SQUEEZE qualifies; CASH_LIQUIDATION stays pinned at 0.0.
            # ADR-001 guard: while the same-day sticky red-line lock is active, the
            # gradient is DISABLED so an intraday VIX dip cannot re-open exposure.
            if hard_regime == RegimeType.LIQUIDITY_SQUEEZE.value and not sticky_day_lock:
                vix_g = features.get("vix", 30.0)
                hy_g = features.get("hy_credit_spread", 400.0)
                grad = _crisis_graduated_budget(vix_g, hy_g)
                if grad > 0.0:
                    audit_trail = {
                        "step_1_safety_gate": {
                            "status": "SKIPPED_DUE_TO_VETO",
                            "reason": "hard regime veto preempts the divergence safety gate",
                        },
                        "step_2_hard_veto": {
                            "status": "TRIGGERED",
                            "regime": hard_regime,
                            "clamped_risk_budget": 0.0,
                            "action": action.value,
                            "crisis_gradient": "GRADUATED_RELEASE",
                            "graduated_budget": grad,
                            "vix": vix_g,
                            "hy_spread": hy_g,
                        },
                        "step_3_soft_policy": {
                            "status": "SKIPPED_DUE_TO_VETO",
                            "reason": "soft policy only applies to RISK_ON; blocked by hard veto",
                        },
                        "step_4_global_velocity_limit": {
                            "status": "SKIPPED_DUE_TO_VETO",
                            "reason": "risk budget released via crisis gradient, not velocity limit",
                        },
                    }
                    return KernelDecision(
                        authority=AuthorityLevel.HARD_VETO,
                        decision=Decision(
                            regime=RegimeType(hard_regime),
                            risk_score=risk_score,
                            confidence=1.0,
                            action=action,
                            reason=reason + f" [crisis gradient release {grad}]",
                        ),
                        hard_regime=hard_regime,
                        soft_regime_label=soft_regime_label,
                        risk_budget=grad,
                        veto_reason=reason,
                        reason_code=reason_code,
                        defense_budget=round(1.0 - grad, 4),
                        audit_trail=audit_trail,
                    )
            audit_trail = {
                "step_1_safety_gate": {
                    "status": "SKIPPED_DUE_TO_VETO",
                    "reason": "hard regime veto preempts the divergence safety gate",
                },
                "step_2_hard_veto": {
                    "status": "TRIGGERED",
                    "regime": hard_regime,
                    "clamped_risk_budget": 0.0,
                    "action": action.value,
                },
                "step_3_soft_policy": {
                    "status": "SKIPPED_DUE_TO_VETO",
                    "reason": "soft policy only applies to RISK_ON; blocked by hard veto",
                },
                "step_4_global_velocity_limit": {
                    "status": "SKIPPED_DUE_TO_VETO",
                    "reason": "risk budget already pinned at the 0.0 floor",
                },
            }
            return KernelDecision(
                authority=AuthorityLevel.HARD_VETO,
                decision=Decision(
                    regime=RegimeType(hard_regime),
                    risk_score=risk_score,
                    confidence=1.0,
                    action=action,
                    reason=reason,
                ),
                hard_regime=hard_regime,
                soft_regime_label=soft_regime_label,
                risk_budget=0.0,
                veto_reason=reason,
                reason_code=reason_code,
                defense_budget=1.0,
                audit_trail=audit_trail,
            )

        # GRADUATED regimes (TRANSITION, TIGHT_LIQUIDITY) get non-zero budget
        # with velocity limiting, same 4-step audit contract as SOFT_POLICY
        candidate_budget = bud(base_budget)
        final_budget, vel_triggered = _apply_global_velocity_limit(
            candidate_budget, previous_risk_budget
        )
        final_budget = round(final_budget, 4)
        final_defense = round(1.0 - final_budget, 4)
        final_rc = "GLOBAL_RAMP_ACTIVE" if vel_triggered else reason_code

        audit_trail = {
            "step_1_safety_gate": {
                "status": "BYPASSED",
                "phase": effective_phase,
            },
            "step_2_hard_veto": {
                "status": "BYPASSED",
                "regime": hard_regime,
                "clamped_risk_budget": candidate_budget,
                "graduated": True,
            },
            "step_3_soft_policy": {
                "status": "SUPPRESSED",
                "reason": "graduated regime budget preempts soft policy",
            },
            "step_4_global_velocity_limit": {
                "status": "TRIGGERED" if vel_triggered else "BYPASSED",
                "previous_risk_budget": previous_risk_budget,
                "candidate_risk_budget": candidate_budget,
                "capped_risk_budget": final_budget,
                "reason_code": final_rc,
            },
        }
        final_budget, _dd_note = _apply_tech_dampener(final_budget, authority, tech_dd, dd_cap)
        if _dd_note["active"]:
            audit_trail["step_2c_tech_dampener"] = _dd_note
        return KernelDecision(
            authority=authority,
            decision=Decision(
                regime=RegimeType(hard_regime),
                risk_score=risk_score,
                confidence=0.75,
                action=action,
                reason=reason,
            ),
            hard_regime=hard_regime,
            soft_regime_label=soft_regime_label,
            risk_budget=final_budget,
            defense_budget=final_defense,
            veto_reason=reason,
            reason_code=final_rc,
            audit_trail=audit_trail,
        )

    # 3. SOFT POLICY: only for RISK_ON
    cfg = config or {}
    action = _soft_policy_action(risk_score)
    budget = 0.8 if action == DecisionAction.AGGRESSIVE else 0.5
    candidate_budget_soft = budget
    final_budget_soft, vel_triggered = _apply_global_velocity_limit(candidate_budget_soft, previous_risk_budget)
    final_budget_soft = round(final_budget_soft, 4)
    final_defense_soft = round(1.0 - final_budget_soft, 4)
    final_reason_soft = "GLOBAL_RAMP_ACTIVE" if vel_triggered else "SOFT_POLICY_NORMAL"
    audit_trail_soft = {
        "step_1_safety_gate": {
            "status": "BYPASSED",
            "phase": effective_phase,
        },
        "step_2_hard_veto": {
            "status": "BYPASSED",
            "regime": hard_regime,
            "clamped_risk_budget": candidate_budget_soft,
        },
        "step_3_soft_policy": {
            "status": "TRIGGERED",
            "policy_action": action.value,
            "policy_budget": candidate_budget_soft,
        },
        "step_4_global_velocity_limit": {
            "status": "TRIGGERED" if vel_triggered else "BYPASSED",
            "previous_risk_budget": previous_risk_budget,
            "candidate_risk_budget": candidate_budget_soft,
            "capped_risk_budget": final_budget_soft,
            "reason_code": final_reason_soft,
        },
    }
    final_budget_soft, _dd_note = _apply_tech_dampener(final_budget_soft, AuthorityLevel.SOFT_POLICY, tech_dd, dd_cap)
    if _dd_note["active"]:
        audit_trail_soft["step_2c_tech_dampener"] = _dd_note
    return KernelDecision(
        authority=AuthorityLevel.SOFT_POLICY,
        decision=Decision(
            regime=RegimeType(hard_regime),
            risk_score=risk_score,
            confidence=risk_score,
            action=action,
            reason="SOFT POLICY: " + action.value + " in risk-on regime",
        ),
        hard_regime=hard_regime,
        soft_regime_label=soft_regime_label,
        risk_budget=final_budget_soft,
        defense_budget=final_defense_soft,
        reason_code=final_reason_soft,
        audit_trail=audit_trail_soft,
    )


def _graduated_regime_decision(hard_regime: str) -> Tuple[AuthorityLevel, DecisionAction, float, str, str]:
    """Map hard regime to graduated authority/budget (v5.0 constitutional correction).

    Returns:
        (authority, action, base_budget, reason, reason_code)

    Mapping:
        TRANSITION          -> SAFETY_GATE, RISK_REDUCE, 0.50
        TIGHT_LIQUIDITY     -> SAFETY_GATE, REDUCE,      0.30
        LIQUIDITY_SQUEEZE   -> HARD_VETO,   REDUCE,      0.00
        CASH_LIQUIDATION    -> HARD_VETO,   LIQUIDATE,   0.00
    """
    if hard_regime == RegimeType.TRANSITION.value:
        return (
            AuthorityLevel.SAFETY_GATE,
            DecisionAction.RISK_REDUCE,
            0.50,
            "SAFETY GATE: TRANSITION graduated budget — neutral defensive",
            "GRADUATED_TRANSITION",
        )
    if hard_regime == RegimeType.TIGHT_LIQUIDITY.value:
        return (
            AuthorityLevel.SAFETY_GATE,
            DecisionAction.REDUCE,
            0.30,
            "SAFETY GATE: TIGHT_LIQUIDITY graduated budget — stress test mode",
            "GRADUATED_TIGHT",
        )
    if hard_regime == RegimeType.LIQUIDITY_SQUEEZE.value:
        return (
            AuthorityLevel.HARD_VETO,
            DecisionAction.REDUCE,
            0.0,
            "HARD VETO: liquidity squeeze detected",
            "VETO_REGIME_SQUEEZE_ACTIVE",
        )
    if hard_regime == RegimeType.CASH_LIQUIDATION.value:
        return (
            AuthorityLevel.HARD_VETO,
            DecisionAction.LIQUIDATE,
            0.0,
            "HARD VETO: systemic cash liquidation",
            "VETO_REGIME_CASH_ACTIVE",
        )
    return (
        AuthorityLevel.HARD_VETO,
        DecisionAction.NO_TRADE,
        0.0,
        "HARD VETO: undefined regime",
        "VETO_REGIME_UNDEFINED",
    )



def _apply_global_velocity_limit(candidate_risk_budget: float, previous_risk_budget: float) -> tuple:
    if candidate_risk_budget > previous_risk_budget:
        capped = min(candidate_risk_budget, previous_risk_budget + MAX_DAILY_RISK_LIFT)
        return capped, capped != candidate_risk_budget
    return candidate_risk_budget, False


def _apply_recovery_limit(phase: str, recovery_active: bool, days_in_recovery: int, previous_risk_budget: float) -> tuple:
    base_limit = 0.50 if phase == "LATE" else 0.75
    if not recovery_active:
        return base_limit, "SAFETY_%s" % phase
    target_recovery_limit = 0.65 if phase == "LATE" else 0.85
    if days_in_recovery < RECOVERY_OBSERVATION_DAYS:
        return min(base_limit, previous_risk_budget), "RECOVERY_TIME_LOCK_ACTIVE"
    max_allowed_today = previous_risk_budget + MAX_DAILY_RISK_LIFT
    return min(target_recovery_limit, max_allowed_today), "RECOVERY_RAMP_ACTIVE"


def _soft_policy_action(risk_score: float) -> DecisionAction:
    if risk_score >= 0.7:
        return DecisionAction.AGGRESSIVE
    elif risk_score <= 0.3:
        return DecisionAction.DEFENSIVE
    return DecisionAction.NEUTRAL


def risk_budget_for_kernel(kd: KernelDecision) -> float:
    return kd.risk_budget


def _crisis_graduated_budget(vix: float, hy_spread: float) -> float:
    """Gradual re-entry budget while still in LIQUIDITY_SQUEEZE.

    The graduated thresholds are NESTED (phase 3 ⊂ phase 2 ⊂ phase 1), so a day
    that qualifies for a tighter phase also qualifies for looser ones. We therefore
    return the HIGHEST budget among all qualifying phases (most-eased = most budget),
    not the first match. Returns 0.0 if the core crisis is still active.
    """
    released = 0.0
    for vth, hth, b in CRISIS_GRADUATED_THRESHOLDS:
        if vix <= vth and hy_spread <= hth:
            released = max(released, b)
    return released

