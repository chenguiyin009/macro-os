---
tags: [macro-os, kernel]
---

# Decision Kernel ? Constitutional Execution

## Authority Hierarchy (IMMUTABLE)

```text
1. SAFETY_GATE ? Divergence phase (CRISIS/LATE/MID/EARLY)
   ?  Budget: 0% / 50% / 75% / 100%
   ?  Action: RISK_REDUCE / REDUCE / REDUCE / NEUTRAL
2. HARD_VETO  ? Hard regime (TRANSITION/SQUEEZE/TIGHT)
   ?  Budget: 0%
   ?  Action: RISK_REDUCE / REDUCE
3. SOFT_POLICY ? Regime (RISK_ON only)
   ?  Budget: 80% (AGGRESSIVE) / 50% (NEUTRAL/DEFENSIVE)
   ?  Action: AGGRESSIVE / NEUTRAL / DEFENSIVE
```

## VETO Mapping

| Regime | Authority | Action | Budget |
|--------|-----------|--------|--------|
| LIQUIDITY_SQUEEZE | HARD_VETO | REDUCE | 0% |
| TIGHT_LIQUIDITY | HARD_VETO | REDUCE | 0% |
| TRANSITION | HARD_VETO | RISK_REDUCE | 0% |
| RISK_ON | SOFT_POLICY | scoring-driven | 60-80% |

## Divergence Gating

| Phase | Division | Authority | Budget |
|-------|----------|-----------|--------|
| CRISIS | Full defense | SAFETY_GATE | 0% |
| LATE | De-risk | SAFETY_GATE | 50% |
| MID | Controlled | SAFETY_GATE | 75% |
| EARLY | Watch mode | SAFETY_GATE | 100% |
| NONE | Normal flow | ?pass-through? | ? |

## Recovery Protocol

```python
if recovery_active and phase in (LATE, MID):
    bud = budget_override if budget_override >= 0 else default
    # LATE: 65% (was 50%), MID: 85% (was 75%)
```

## Key Rules

- Kernel imports ZERO advisory modules (counterfactual, attribution, regime_probabilistic)
- Kernel is the SINGLE entry point for all trading decisions
- Orchestrator may NOT bypass the kernel

## v5: Global Velocity Limit

*GLOBAL_VELOCITY_LIMIT* is the final physical clamp applied to any upward change in the approved risk budget. It limits the single-step increase to MAX_DAILY_RISK_LIFT (0.10) above the previous risk budget. When triggered, the kernel emits eason_code = GLOBAL_RAMP_ACTIVE.

The decision pipeline in v5 is:
1. Safety Gate (divergence phases CRISIS/LATE/MID/EARLY)
2. Hard Veto (non-RISK_ON regimes)
3. Soft Policy (RISK_ON only)
4. **Global Velocity Limit** (final constitutional clamp)

The kernel also emits a structured udit_trail with the status of each step.
