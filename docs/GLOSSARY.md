---
tags: [macro-os, glossary]
---

# Glossary

## Enums

### RegimeType

| Value | Meaning | Detection |
|-------|---------|-----------|
| RISK_ON | Risk appetite normal | TIPS low + DXY weak |
| TIGHT_LIQUIDITY | Liquidity tightening | TIPS high + DXY strong |
| LIQUIDITY_SQUEEZE | Crisis mode | VIX > 25 OR HY > 400 |
| TRANSITION | Mixed signals | None of the above |

### DecisionAction

| Value | Meaning | When |
|-------|---------|------|
| AGGRESSIVE | Full risk positioning | RISK_ON + confidence > 0.7 |
| DEFENSIVE | Reduce risk | RISK_ON + confidence < 0.3 |
| NEUTRAL | Watch mode | RISK_ON + confidence 0.3-0.7, or EARLY divergenge |
| LONG | Directional long | Legacy scoring |
| SHORT | Directional short | Legacy scoring |
| REDUCE | De-risk | HARD_VETO or LATE/MID divergenge |
| RISK_REDUCE | Full reduction | TRANSITION or CRISIS divergence |
| NO_TRADE | No action | Confidence too low |

### AuthorityLevel

| Value | Source | Override |
|-------|--------|----------|
| SAFETY_GATE | Divergence phase | Highest |
| HARD_VETO | Hard regime | Second |
| SOFT_POLICY | Scoring engine | Normal ops |

### Quadrant (Macro)

| Value | TIPS | DXY |
|-------|------|-----|
| RISK_ON | below 0.5 | below 100 |
| TIGHT_LIQUIDITY | above 0.8 | above 103 |
| DIVERGENCE | one above, one below | ? |
| TRANSITION | near neutral | near neutral |

### ConfirmationStatus (Macro)

| Value | Meaning |
|-------|---------|
| ALIGNED | Gold + Credit confirm the quadrant |
| DIVERGED | Gold or Credit conflicts with quadrant |
| NEUTRAL | Not in a confirmable quadrant |

### Divergence Phase

| Phase | Score | Action |
|-------|-------|--------|
| NONE | < 0.20 | Normal operation |
| EARLY | 0.20 - 0.40 | Watch mode |
| MID | 0.40 - 0.60 | Controlled reduction |
| LATE | 0.60 - 0.85 | Active de-risking |
| CRISIS | >= 0.85 | Full defense |

### SystemState

| State | Meaning |
|-------|---------|
| ACTIVE | All systems nominal |
| DEFENSIVE | Reduced risk posture |
| DEGRADED | Shadow divergence sustained, no directional trading |

### Fracture Types

| Type | Meaning |
|------|---------|
| CREDIT_LED | Rate tightening causes credit stress |
| GOLD_DECOUPLING | Gold rallies while credit weakens |
| USD_MISMATCH | Dollar strengthens while gold rallies |
| VOL_MISMATCH_FILTER | False panic suppression |
| MULTI_FRONT_RESONANCE | Multiple fractures active simultaneously |

### BudgetState

| State | Budget | Conditions |
|-------|--------|------------|
| LATE_DIVERGENCE | 50%/75%/100% | Default for LATE/MID/EARLY |
| CONFIRMED_BREAKDOWN | 0% | LATE + volume divergence + crack age >= 5 |
| MACRO_HEALING | gradual recovery | Score < 0.50 for >= 3 bars |

## v5: Velocity & Defense

- **GLOBAL_VELOCITY_LIMIT**: Caps upward risk-budget changes to MAX_DAILY_RISK_LIFT (0.10). Applies as the final step in the decision pipeline.
- **GLOBAL_RAMP_ACTIVE**: The eason_code emitted when the Global Velocity Limit is triggered.
- **defense_budget**: The portion of the total budget allocated to non-risk (defensive) positioning. Always equals 1.0 - final_risk_budget. A first-class field on KernelDecision.
- **audit_trail**: Ordered dict of all four decision steps (safety_gate, hard_veto, soft_policy, global_velocity_limit), each with a status (TRIGGERED/BYPASSED/SUPPRESSED).
