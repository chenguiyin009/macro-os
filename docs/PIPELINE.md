---
tags: [macro-os, pipeline]
---

# Pipeline Reference

## Execution Order (HARD LOCKED)

```text
MCP / TradingView ??? PINE TABLE (10-call Macro Liquidity Composite)
                           ?
                           ?
                    FEATURE LAYER
                    (macro_mapper.py)
                           ?
                           ?
                    MACRO WORLD MODEL (v4.4)
                    ???????????????????????
                    ? compute_macro_state ? TIPS x DXY ? Quadrant
                    ? compute_confirmation? Gold x Credit ? DIVERGED
                    ???????????????????????
                              ?
                              ?
                    DIVERGENCE ENGINE (v4.5+v4.6)
                    ????????????????????????????????
                    ? compute_divergence_score      ? 4-axis crack detection
                    ? DivergencePhaseEngine         ? Multi-Front Resonance
                    ? PhaseHysteresisSmoother       ? Instant upgrade, delayed degrade
                    ????????????????????????????????
                              ?
                              ?
                    CONSTITUTIONAL KERNEL (v4.3.1)
                    ????????????????????????????????
                    ? 1. SAFETY_GATE               ? Divergence ? CRISIS ? RISK_REDUCE
                    ? 2. HARD_VETO                 ? TRANSITION/SQUEEZE/TIGHT ? REDUCE
                    ? 3. SOFT_POLICY               ? RISK_ON ? AGGRESSIVE/NEUTRAL/DEFENSIVE
                    ????????????????????????????????
                              ?
                              ?
                    EXPOSURE DAMPER (v4.6)
                    NonLinearExposureDampener
                    Sigmoid(k=12, x0=0.6)
                              ?
                              ?
                    ASSET SIZER (v4.6)
                    FractureAwareSizer ? watchlist.yaml
                    Moat/Logic/Catalyst ? targeted cuts
```

## Data Sources

| Source | Method | Data |
|--------|--------|------|
| TV MCP | quote_get | DXY, VIX, GOLD prices |
| TV MCP | data_get_ohlcv | QQQ close + volume |
| TV MCP | data_get_study_values | SOFR Path, HY Stress, VIX Stress, Real Yield |
| TV MCP | data_get_pine_tables | Macro Liquidity Composite (10-call) |
| Estimated | _feat_quality | HY spread when Pine unavailable |

## Key Constraints

- **No lookahead**: TemporalBuffer enforces strict(t) < event.t
- **No randomness**: Fully deterministic replay
- **No LLM in kernel**: Advisory-only
- **Macro First**: MacroState computed before any kernel logic

## Step 5: Global Velocity Limit (v5)

The Global Velocity Limit acts as the final constitutional clamp after all budget authorities have settled. It prevents quantum leaps in risk exposure from one session to the next.

**Rule:** inal_risk_budget = min(candidate_risk_budget, previous_risk_budget + MAX_DAILY_RISK_LIFT)

When triggered, the eason_code field is set to GLOBAL_RAMP_ACTIVE.
