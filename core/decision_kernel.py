"""Macro OS v4.4 - Constitutional kernel with Macro-first SAFETY_GATE.

Kernel reads MacroState confirmation_status but NEVER writes it.
VETO hierarchy:
1. SAFETY_GATE: DIVERGED macro -> force RISK_REDUCE
2. HARD_VETO: all non-RISK_ON regimes
3. SOFT_POLICY: RISK_ON only (AGGRESSIVE/DEFENSIVE/NEUTRAL)
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
    proposed_risk: float = 0.0,
    previous_risk_budget: float = 0.5,
    days_in_recovery: int = 0,
) -> KernelDecision:
    _ = features, soft_regime_label, confidence
    risk_score = max(0.0, min(1.0, risk_score))
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
        return KernelDecision(authority=AuthorityLevel.SAFETY_GATE,
            decision=Decision(regime=RegimeType.TRANSITION, risk_score=risk_score, confidence=1.0, action=DecisionAction.NEUTRAL, reason="SAFETY GATE: EARLY divergence - watch mode"),
            hard_regime=hard_regime, soft_regime_label=soft_regime_label, risk_budget=final_risk_budget, defense_budget=final_defense_budget, veto_reason="EARLY divergence phase - watch mode", reason_code=final_reason_code, audit_trail=audit_trail)

    # 2. HARD VETO: all non-RISK_ON regimes
    if hard_regime != RegimeType.RISK_ON.value:
        action, reason, reason_code = _veto_action(hard_regime)
        # Unified four-step audit contract (same keys as SAFETY_GATE / SOFT_POLICY).
        # Unexecuted steps are marked SKIPPED_DUE_TO_VETO so any consumer reading
        # audit_trail["step_1_safety_gate".."step_4_global_velocity_limit"] stays
        # KeyError-free regardless of the authority that fired.
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


def _veto_action(hard_regime: str) -> Tuple[DecisionAction, str, str]:
    if hard_regime == RegimeType.TRANSITION.value:
        return DecisionAction.RISK_REDUCE, "HARD VETO: TRANSITION mandates risk reduction", "VETO_REGIME_TRANSITION_ACTIVE"
    elif hard_regime == RegimeType.LIQUIDITY_SQUEEZE.value:
        return DecisionAction.REDUCE, "HARD VETO: liquidity squeeze detected", "VETO_REGIME_SQUEEZE_ACTIVE"
    elif hard_regime == RegimeType.TIGHT_LIQUIDITY.value:
        return DecisionAction.REDUCE, "HARD VETO: tight liquidity regime", "VETO_REGIME_TIGHT_ACTIVE"
    return DecisionAction.NO_TRADE, "HARD VETO: undefined regime", "VETO_REGIME_UNDEFINED"



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

