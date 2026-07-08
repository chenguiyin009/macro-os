---
tags: [macro-os, divergence]
---

# Divergence Engine - Fracture Map

## Score Components

| Component | Weight | Condition | Source |
|-----------|--------|-----------|--------|
| Rate vs Credit | +0.40 | TIPS up and HY down | Pine M2 + M3 |
| Gold vs Credit | +0.30 | Gold up and HY down | Pine M3 |
| Dollar Mismatch | +0.20 | DXY up and Gold up | Pine M3 |
| VIX Filter | -0.10 | VIX < 15 and score > 0.5 | Pine M3 |
| Multi-Front Resonance | +0.20 | >= 2 active fractures | Engine |

## Score Safety

```python
raw_score = base_score + resonance_bonus
final_score = min(max(raw_score, 0.0), 1.0)
```

The divergence score is treated as a normalized state variable across L2/L3/L4.
It is always clamped into `[0.0, 1.0]` before phase mapping.

## Phase Thresholds

| Phase | Score Range | L2 Guardrail | Behavior |
|-------|-------------|--------------|----------|
| NONE | < 0.20 | 100% | Normal operations |
| EARLY | 0.20 - 0.40 | 98% | Watch mode, no forced cut |
| MID | 0.40 - 0.60 | 72% | Controlled exposure |
| LATE | 0.60 - 0.85 | 35% | Active de-risking |
| CRISIS | >= 0.85 | 0% | Full defense |

## Vol-Adjusted Threshold (v4.5)

When VIX < 16, the CRISIS threshold rises from `0.85` to `0.92`.
Low-vol environments require stronger evidence for a crisis declaration.

## PhaseHysteresisSmoother (v4.6)

```python
Upgrade:   raw_sev > current_sev -> instant
Downgrade: raw_sev < current_sev -> cooldown(2) + confirmation window(3)
Special:   MID -> EARLY requires score < 0.25
```

## NonLinearExposureDampener

```python
Max_Exposure = 1.0 / (1.0 + exp(k * (score - x0)))
# k = 8.0
# x0 = 0.5
# guardrails: EARLY=0.98, MID=0.72, LATE=0.35
# Final = min(continuous, guardrail)
```

Design intent:

- The Sigmoid curve is the primary controller through the `0.4` to `0.85` transition band.
- Phase guardrails remain as upper bounds, not as the default output on most bars.
- The earlier `x0` shifts the down-slope forward so MID/LATE no longer produce a step-function cliff.

## Fracture Types

| Fracture | Detection | Risk |
|----------|-----------|------|
| CREDIT_LED | TIPS up + HY down | Credit channel stress |
| GOLD_DECOUPLING | Gold up + HY down | Safe haven vs credit |
| USD_MISMATCH | DXY up + Gold up | Dollar/gold disconnection |
| VOL_MISMATCH_FILTER | VIX < 15 and score > 0.5 | False panic suppression |
| MULTI_FRONT_RESONANCE | >= 2 fractures active | Systemic amplification |
