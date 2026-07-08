# Decision Kernel v5 Design

Date: 2026-07-03
Owner: Macro OS v5.0
Status: Draft for architecture review

## 1. Purpose

This document defines the v5 Decision Kernel upgrade for Macro OS.

The Decision Kernel remains the constitutional execution layer of the system. Its job is not alpha generation, attribution, scenario analysis, or asset selection. Its job is to enforce the immutable authority hierarchy and emit a deterministic execution budget that downstream layers must obey.

The v5 upgrade adds three behaviors that were missing in the current implementation:

1. Recovery protocol hardening with physical time friction.
2. First-class separation of `risk_budget` and `defense_budget`.
3. A global velocity clamp that prevents one-day risk expansion leaps across all branches.

It also adds a CLI simulation contract so the same kernel logic can be replayed, regression-tested, and batch-called by future automation.

## 2. Immutable Boundaries

The following boundaries are constitutional and must not be violated:

- Kernel stays pure: no file I/O, no network, no database, no framework state.
- Kernel remains deterministic: identical inputs produce identical outputs.
- Kernel remains stateless: historical context is passed in as input fields, never stored internally.
- Kernel does not select assets. It emits budgets only.
- Kernel imports zero advisory modules such as attribution, counterfactual, or probabilistic regime logic.
- Kernel remains the single decision choke point for all execution budgets.

## 3. Authority Hierarchy

The v5 authority order remains immutable:

1. `SAFETY_GATE`
2. `HARD_VETO`
3. `SOFT_POLICY`
4. `GLOBAL_VELOCITY_LIMIT`

Interpretation:

- `SAFETY_GATE` handles structural fracture and divergence phases.
- `HARD_VETO` handles hostile macro regimes.
- `SOFT_POLICY` is only allowed to operate when the macro regime is `RISK_ON` and no higher authority has already constrained the budget.
- `GLOBAL_VELOCITY_LIMIT` is not a policy layer. It is a final physical clamp applied to any upward change in approved risk budget.

Lower layers may shape assets inside approved budgets. Lower layers may never widen a budget after a higher authority has narrowed it.

## 4. Scope of v5 Changes

### Included

- Add recovery time-lock and daily lift cap to `SAFETY_GATE` for `LATE` and `MID` phases.
- Add a global final-step velocity clamp for all upward budget changes.
- Add `defense_budget` as a first-class output on `KernelDecision`.
- Add structured audit trail output for simulator and debugging use.
- Add a CLI script that calls the same kernel logic and emits deterministic JSON.
- Update Decision Kernel documentation, glossary, and pipeline references.
- Preserve existing entry points with compatibility wrappers where practical.

### Excluded

- No defense asset mapping inside the kernel.
- No web UI or local app.
- No YAML externalization of constitutional constants.
- No persistence of kernel state.
- No regime-classification redesign.

## 5. Constitutional Constants

The following constants are part of the physical law of the kernel and stay in Python code, not YAML:

```python
RECOVERY_OBSERVATION_DAYS = 3
MAX_DAILY_RISK_LIFT = 0.10
```

Rationale:

- These are not calibration knobs.
- They define irreversible discipline on recovery behavior.
- Moving them to YAML would allow runtime bypass of the constitution.

## 6. Input Contract

The existing `decide()` entry point remains the public kernel entry point. It is extended with additional inputs while preserving compatibility for current callers.

### Existing inputs retained

- `features: dict`
- `hard_regime: str`
- `soft_regime_label: str`
- `risk_score: float`
- `confidence: float`
- `config: Optional[dict]`
- `confirmation_status: str`
- `divergence_phase: str`
- `divergence_score: float`
- `recovery_active: bool`
- `budget_override: float`

### New v5 inputs

- `proposed_risk: float`
  - Meaning: upstream proposed risk budget before constitutional clamping.
  - Range: `[0.0, 1.0]`
- `days_in_recovery: int`
  - Meaning: number of observed calendar days in recovery state.
  - Range: `>= 0`
- `previous_risk_budget: float`
  - Meaning: last approved final risk budget from T-1.
  - Range: `[0.0, 1.0]`

### Normalization rules

- Any budget input must be clamped into `[0.0, 1.0]` before evaluation.
- Missing `proposed_risk` falls back to the legacy kernel path.
- Missing `previous_risk_budget` falls back to the current branch baseline budget for compatibility.
- Missing `days_in_recovery` is treated as `0`.

## 7. Output Contract

`KernelDecision` becomes a richer first-class output object.

### Existing fields retained

- `authority`
- `decision`
- `hard_regime`
- `soft_regime_label`
- `risk_budget`
- `veto_reason`

### New v5 fields

- `defense_budget: float`
  - Definition: `1.0 - final_risk_budget`
- `reason_code: str`
  - Machine-stable final outcome reason identifier.
- `audit_trail: dict`
  - Structured constitutional trace.

### Compatibility rule

- `risk_budget` remains the canonical field name for current callers.
- `defense_budget` is additive, not replacing any existing field.
- `risk_budget_for_kernel(kd)` remains valid and returns `kd.risk_budget`.
- If the global velocity clamp fires, the final `reason_code` becomes `GLOBAL_RAMP_ACTIVE`, while branch-specific reason codes remain preserved inside `audit_trail`.

## 8. Decision Flow

The kernel must evaluate in this exact order:

### Step 1: SAFETY_GATE

`SAFETY_GATE` always executes first.

If `divergence_phase` is empty and `confirmation_status == "DIVERGED"`, the kernel preserves the existing legacy mapping to `MID`.

Base budget table:

- `CRISIS -> 0.00`
- `LATE -> 0.50`
- `MID -> 0.75`
- `EARLY -> 1.00`
- `NONE -> no clamp at this step`

Base action table:

- `CRISIS -> RISK_REDUCE`
- `LATE -> REDUCE`
- `MID -> REDUCE`
- `EARLY -> NEUTRAL`

If `phase` is `LATE` or `MID`, the recovery protocol below may alter the effective budget ceiling.

If `phase` is `EARLY`, the kernel does not apply the 3-day recovery time-lock. `EARLY` is treated as a valid fast-healing state. However, `EARLY` is still subject to the global velocity clamp in Step 4.

### Step 2: HARD_VETO

If the effective hard regime is not `RISK_ON`, `HARD_VETO` governs final output.

Regime mapping:

- `TRANSITION -> action=RISK_REDUCE, final_risk_budget=0.00, final_defense_budget=1.00`
- `LIQUIDITY_SQUEEZE -> action=REDUCE, final_risk_budget=0.00, final_defense_budget=1.00`
- `TIGHT_LIQUIDITY -> action=REDUCE, final_risk_budget=0.00, final_defense_budget=1.00`

Important nuance:

- The kernel does not choose which defense assets receive the defense budget.
- It only emits `defense_budget=1.00` and leaves mapping to the downstream execution layer.

### Step 3: SOFT_POLICY

`SOFT_POLICY` only runs when no higher authority has already constrained the budget and `hard_regime == RISK_ON`.

The current policy mapping remains simple:

- aggressive risk-on -> `0.80`
- neutral/defensive risk-on -> `0.50`

In v5, the candidate approved risk budget is:

`min(proposed_risk, soft_policy_budget)` when `proposed_risk` is supplied.

If `proposed_risk` is not supplied, legacy behavior remains in force.

This asymmetry is intentional: lower layers may request less risk than the policy ceiling, but may never expand beyond it.

### Step 4: GLOBAL_VELOCITY_LIMIT

Before final output is emitted, the kernel applies a final physical ramp constraint to any upward change in risk budget.

Execution logic:

```python
if candidate_final_risk_budget > previous_risk_budget:
    candidate_final_risk_budget = min(
        candidate_final_risk_budget,
        previous_risk_budget + MAX_DAILY_RISK_LIFT,
    )
    final_reason_code = 'GLOBAL_RAMP_ACTIVE'
```

Interpretation:

- Any upward budget move is physically capped at `MAX_DAILY_RISK_LIFT` per day.
- This clamp applies regardless of whether the budget came from `EARLY`, `LATE`, `MID`, or `SOFT_POLICY`.
- Downward moves are never slowed by the kernel.
- This closes the `CRISIS -> EARLY` one-day leap vulnerability.

## 9. Recovery Protocol v5

This is the phase-scoped recovery upgrade.

It applies only in `SAFETY_GATE` and only for `LATE` and `MID`.

Definitions:

- `base_limit = 0.50` for `LATE`, `0.75` for `MID`
- `target_recovery_limit = 0.65` for `LATE`, `0.85` for `MID`

Execution logic:

```python
if recovery_active:
    target_recovery_limit = 0.65 if phase == 'LATE' else 0.85

    if days_in_recovery < RECOVERY_OBSERVATION_DAYS:
        actual_limit = min(base_limit, previous_risk_budget)
        reason = 'RECOVERY_TIME_LOCK_ACTIVE'
    else:
        max_allowed_today = previous_risk_budget + MAX_DAILY_RISK_LIFT
        actual_limit = min(target_recovery_limit, max_allowed_today)
        reason = 'RECOVERY_RAMP_ACTIVE'
else:
    actual_limit = base_limit
```

Interpretation:

- The time-lock prevents instant re-risking after a crisis or late fracture.
- The daily cap adds physical damping inside the `LATE/MID` recovery branch.
- The recovery branch can never widen past the phase-specific recovery ceiling.
- If the environment worsens, the lower base limit still dominates.
- `EARLY` remains exempt from the time-lock, but never exempt from the global velocity clamp.

## 10. Budget Semantics

The kernel now makes budget semantics explicit.

- `final_risk_budget` funds risk assets such as `QQQ` or `SPY`.
- `final_defense_budget` funds non-risk positioning selected by downstream execution logic.
- `final_defense_budget` must equal `1.0 - final_risk_budget` after all constitutional clamps are applied.

Kernel rounding policy should remain minimal and deterministic. If rounding is applied, the defense budget must be recomputed from the final risk budget, not rounded independently.

## 11. Reason Codes

The following reason codes should be stable and testable:

- `SAFETY_CRISIS`
- `SAFETY_LATE`
- `SAFETY_MID`
- `SAFETY_EARLY`
- `RECOVERY_TIME_LOCK_ACTIVE`
- `RECOVERY_RAMP_ACTIVE`
- `VETO_REGIME_TRANSITION_ACTIVE`
- `VETO_REGIME_SQUEEZE_ACTIVE`
- `VETO_REGIME_TIGHT_ACTIVE`
- `SOFT_POLICY_AGGRESSIVE`
- `SOFT_POLICY_NEUTRAL`
- `SOFT_POLICY_DEFENSIVE`
- `GLOBAL_RAMP_ACTIVE`

The human-readable `reason` and `veto_reason` fields may evolve. `reason_code` should not.

## 12. Audit Trail Schema

The simulator and downstream tests require a structured audit trail.

`audit_trail` must preserve insertion order during serialization. In Python 3.7+, the default dict insertion order is sufficient as long as the steps are written in order.

Required shape:

```json
{
  "step_1_safety_gate": {
    "status": "TRIGGERED|BYPASSED",
    "phase": "LATE",
    "base_limit": 0.50,
    "recovery_active": true,
    "days_in_recovery": 3,
    "previous_risk_budget": 0.55,
    "adjusted_risk_budget": 0.65,
    "reason_code": "RECOVERY_RAMP_ACTIVE"
  },
  "step_2_hard_veto": {
    "status": "TRIGGERED|BYPASSED",
    "regime": "TIGHT_LIQUIDITY",
    "clamped_risk_budget": 0.00,
    "reason_code": "VETO_REGIME_TIGHT_ACTIVE"
  },
  "step_3_soft_policy": {
    "status": "TRIGGERED|BYPASSED",
    "policy_action": "AGGRESSIVE",
    "policy_budget": 0.80,
    "reason_code": "SOFT_POLICY_AGGRESSIVE"
  },
  "step_4_global_velocity_limit": {
    "status": "TRIGGERED|BYPASSED",
    "previous_risk_budget": 0.00,
    "candidate_risk_budget": 0.80,
    "capped_risk_budget": 0.10,
    "reason_code": "GLOBAL_RAMP_ACTIVE"
  }
}
```

Fields may be omitted when they do not apply, but the four step keys must always exist.

## 13. CLI Simulator Contract

A new script will be added:

`python scripts/simulate_kernel.py`

### CLI goals

- Manual operator sandbox
- Deterministic regression fixture generator
- Batch counterfactual replay entry point

### Required inputs

- `--phase`
- `--regime`
- `--soft-regime-label`
- `--risk-score`
- `--confidence`
- `--proposed-risk`
- `--recovery`
- `--days-in-recovery`
- `--previous-risk-budget`

### Example

```bash
python scripts/simulate_kernel.py   --phase LATE   --regime TIGHT_LIQUIDITY   --soft-regime-label RISK_ON   --risk-score 0.80   --confidence 0.80   --proposed-risk 0.80   --recovery true   --days-in-recovery 3   --previous-risk-budget 0.55
```

### Output shape

```json
{
  "timestamp": 1783080000,
  "audit_trail": {
    "step_1_safety_gate": {
      "status": "TRIGGERED",
      "phase": "LATE",
      "base_limit": 0.50,
      "recovery_active": true,
      "days_in_recovery": 3,
      "previous_risk_budget": 0.55,
      "adjusted_risk_budget": 0.65,
      "reason_code": "RECOVERY_RAMP_ACTIVE"
    },
    "step_2_hard_veto": {
      "status": "TRIGGERED",
      "regime": "TIGHT_LIQUIDITY",
      "clamped_risk_budget": 0.00,
      "reason_code": "VETO_REGIME_TIGHT_ACTIVE"
    },
    "step_3_soft_policy": {
      "status": "BYPASSED",
      "reason": "Risk budget already zeroed by HARD_VETO"
    },
    "step_4_global_velocity_limit": {
      "status": "BYPASSED",
      "previous_risk_budget": 0.55,
      "candidate_risk_budget": 0.00,
      "reason": "No upward budget expansion"
    }
  },
  "execution_outcome": {
    "governing_authority": "HARD_VETO",
    "final_risk_budget": 0.00,
    "final_defense_budget": 1.00,
    "action_required": "RISK_REDUCE",
    "reason_code": "VETO_REGIME_TIGHT_ACTIVE"
  }
}
```

The CLI script is an adapter only. It must not reimplement kernel logic.

## 14. Compatibility Strategy

Recommended rollout mode is additive evolution, not destructive rewrite.

### Principles

- Keep `decide()` as the public entry point.
- Internally refactor into smaller pure helper steps if helpful.
- Preserve `risk_budget_for_kernel()`.
- Preserve existing authority enums.
- Add fields to `KernelDecision` without deleting current ones.

### Compatibility behavior

- Old callers that do not pass the new history fields still work.
- Old tests around `risk_budget` continue to pass unless the behavior is intentionally updated.
- New tests should assert `defense_budget`, `reason_code`, and `audit_trail`.

## 15. Documentation Updates Required

These docs must be updated together so the constitutional language stays consistent:

- `macro-os/docs/DECISION_KERNEL.md`
- `macro-os/docs/PIPELINE.md`
- `macro-os/docs/GLOSSARY.md`
- `macro-os/docs/USAGE.md` for simulator entry point

Key documentation updates:

- Replace the old zero-only hard veto language with explicit `risk_budget=0 / defense_budget=1` wording.
- Document the v5 recovery time-lock.
- Document the global velocity clamp.
- Document the simulator contract.
- Document the distinction between budget issuance and defense asset allocation.

## 16. Test Plan

Implementation must follow TDD.

### New kernel tests

1. `LATE` recovery before day 3 stays locked to `min(base_limit, previous_risk_budget)`.
2. `MID` recovery before day 3 stays locked.
3. `LATE` recovery after day 3 respects `MAX_DAILY_RISK_LIFT`.
4. `MID` recovery after day 3 respects `MAX_DAILY_RISK_LIFT`.
5. `CRISIS` ignores recovery inputs.
6. `HARD_VETO` always emits `risk_budget=0` and `defense_budget=1`.
7. `SOFT_POLICY` is bypassed when `HARD_VETO` triggers.
8. `reason_code` is stable for all major branches.
9. `audit_trail` always contains the four ordered step keys.
10. `CRISIS (T-1 risk=0.00) -> EARLY (T candidate risk=0.80)` is clamped to `risk_budget=0.10` by `GLOBAL_VELOCITY_LIMIT`.

### CLI tests

1. Argument parsing works for booleans and floats.
2. Output is valid JSON.
3. Output authority and budgets match direct kernel invocation.
4. Example scenario from the spec is reproducible.

### Regression requirement

All existing kernel and divergence tests must remain green after the compatibility path is applied.

## 17. Risks and Mitigations

### Risk: Silent drift between CLI and kernel

Mitigation:

- CLI must call the kernel directly.
- CLI tests compare JSON output against kernel output fields.

### Risk: Old callers do not supply new inputs

Mitigation:

- Add explicit fallback behavior.
- Keep new inputs optional at the public boundary.

### Risk: Audit trail grows unstable over time

Mitigation:

- Treat `reason_code` and step keys as part of the public contract.
- Preserve insertion order during serialization.
- Allow human-readable reason text to evolve separately.

## 18. Implementation Order

1. Extend schema with additive fields.
2. Add failing tests for recovery lock, defense budget, audit trail, and global velocity clamp.
3. Refactor `decide()` into pure internal helper steps.
4. Implement simulator CLI.
5. Update docs.
6. Run full test suite.

## 19. Acceptance Criteria

The v5 Decision Kernel upgrade is complete when all of the following are true:

- Recovery budget increases require 3 observed days in `LATE` and `MID`.
- Recovery budget increases never exceed 10 percentage points per day.
- Any upward budget change, regardless of branch, is capped by the global velocity limit.
- Hard veto hostile regimes produce `risk_budget=0` and `defense_budget=1`.
- Kernel still performs no asset mapping.
- Simulator output is deterministic JSON and mirrors kernel behavior exactly.
- Existing call sites continue to function through the compatibility path.
- Updated docs describe the same behavior the code enforces.
