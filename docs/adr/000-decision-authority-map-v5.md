# Decision Authority Map (v5.0)

- **Status:** **Accepted** (architecture review 2026-07-15; Q3/Q4 amendments applied)
- **Date:** 2026-07-15
- **Depends on:** ADR-001 Option B (absolute physical red line) — Accepted
- **Code ref:** research path `runtime/orchestrator.py` + `core/decision_kernel.decide()`

---

## 1. Purpose

Answer one question for every subsystem:

> Who may reduce risk, who may only shape weights inside a budget, and who may
> only advise?

Without this map, new features tend to open a third “HARD_VETO-like” channel.

---

## 2. Layered authority (target / LIVE research spine)

```text
                    ┌─────────────────────────────────────┐
                    │  ADVISORY (no write on budget)        │
                    │  CIO report / Shadow / LLM text       │
                    └─────────────────────────────────────┘
                                      │ read-only views
┌─────────────────────────────────────▼─────────────────────────────────────┐
│  DATA → FeatureSchema (unit-normalized)                                  │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L1 MACRO WORLD MODEL                                                     │
│  Output: hard_regime_raw (quadrant), confirmation                         │
│  May NOT: emit budgets                                                    │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L2 DIVERGENCE                                                            │
│  Output: phase_raw (CRISIS/LATE/MID/EARLY/"")                             │
│  May NOT: emit budgets                                                    │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L2.5 PRE-KERNEL PHYSICAL RED LINES (orchestrator)                        │
│  Input: features + thresholds.constitution.red_lines                      │
│  Output: hard_regime_folded, red_line meta                                │
│  Policy (ADR-001 Option B): if triggered → neutralize phase_for_kernel    │
│  May NOT: call I/O inside pure evaluator; may NOT touch audit_trail keys  │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L3 CONSTITUTIONAL KERNEL  decide()   ★ sole macro budget choke point ★   │
│  Order: SAFETY → HARD_VETO → SOFT → GLOBAL_VELOCITY                       │
│  Output: authority, risk_budget, defense_budget, reason_code, audit_trail │
│  May NOT: select assets, read YAML, import advisory modules               │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L3b MICRO RISK GATEWAY (optional AND)                                    │
│  May: block entry, partial exit, FATAL_GAP liquidate                      │
│  May NOT: raise risk_budget above kernel output                           │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L4 WEIGHTING / PORTFOLIO (optional on research path)                     │
│  Sizer / damper / sector allocator                                        │
│  May: redistribute inside risk_budget + defense_budget                    │
│  May NOT: increase total risk beyond kernel risk_budget                   │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  LEGACY — PolicyEngine (explicit badge; not on research critical path)    │
│  Do not treat as second constitution. Migrate remaining knobs to SSOT/L4. │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
                    Vault Event + Feishu (observability)
```

---

## 3. Authority matrix

| Actor | Can set risk_budget=0 | Can set 0<risk≤cap | Can pick symbols | Can place orders | Notes |
|-------|----------------------:|-------------------:|-----------------:|-----------------:|-------|
| Macro mapper | No | No | No | No | regime label only |
| Divergence | No | No | No | No | phase only |
| Physical red lines + orch fold | Indirect (via hard_regime + phase neutralize) | No | No | No | ADR-001 B |
| `decide()` | Yes | Yes | No | No | **only macro budget law** |
| RiskGateway | Flatten positions | Reduce size | No* | Yes (exec path) | AND tighten only |
| FractureAwareSizer / damper | No | Within budget | Yes (weights) | No | optional on path |
| PolicyEngine (**legacy**) | Historical alloc caps only | Historical | No | No | **not kernel; do not re-wire** |
| GlenRedLines | Multiplier | Multiplier | No | No | legacy units; not budget law |
| CIO / Shadow / LLM | No | No | No | No | advisory |
| Hermes / broker adapters | No | No | No | Yes | execute approved |

\*Gateway operates on market microstructure for the active book, not macro universe selection.

---

## 4. Naming disambiguation

| Term | Meaning | Bad overload |
|------|---------|--------------|
| `AuthorityLevel.HARD_VETO` | Kernel authority enum | Do not log policy danger as the same enum without context |
| `PHYSICAL_RED_LINE_*` | Side-channel reason from fold | Not a 5th audit step |
| `SAFETY_*` / `RECOVERY_*` | Kernel phase path | Not red-line meta |
| `ALLOC_*` (preferred for legacy policy logs) | Allocation-layer caps | Prefer instead of reusing HARD_VETO alone |
| `FATAL_GAP_LIQUIDATION` | Micro gateway | Precedence for single-name flatten |

---

## 5. Incident precedence (execution time)

When multiple alarms fire on the same cycle:

1. **FATAL_GAP / micro force-flatten** (account survival / gap risk)
2. **Kernel budget = 0** (SAFETY CRISIS or HARD_VETO after absolute red line)
3. **Kernel reduced budget** (LATE/MID SAFETY, soft defensive)
4. **Gateway HOLD / partial exit** (tighten only)
5. **Advisory warnings** (CIO/shadow text)

Rule: lower layers may only **tighten** residual risk, never expand past kernel.

---

## 6. Session state (ephemeral until Event hydration)

| State | Location today | Policy |
|-------|----------------|--------|
| `previous_risk_budget` | `orchestrator.state` | **Session-ephemeral** until vault hydration ships |
| `days_in_recovery` | `orchestrator.state` | Same |
| `red_line_day_lock` | `orchestrator.state` | Same-day sticky absolute fold (ADR-001 review note C) |
| Sector allocator disk state | `load_state()` | Non-constitutional |
| Shadow engine | in-memory / files | Advisory only |

### Cold-start default (review Q3 — **Accepted amendment**)

> Velocity/recovery session fields are **ephemeral process state**.
>
> On process restart, **`previous_risk_budget` defaults to `0.0`** (not 0.50)
> and `days_in_recovery` defaults to `0`, unless a future task implements Event
> replay hydration.
>
> Rationale: if the book was fully de-risked (`risk_budget=0`) and the process
> OOM-restarts into `0.50`, `GLOBAL_VELOCITY_LIMIT` cannot fully prevent a
> dangerous re-risk jump on the first bar relative to true T-1 reality. Prefer
> missing a ramp to naked exposure after crash recovery.
>
> After the first successful `decide()`, orchestrator continues to store the
> last approved `risk_budget` in memory for velocity/recovery within the session.

**Follow-up (not this PR):** hydrate `previous_risk_budget` / `days_in_recovery`
from the latest Vault Event on boot.

---

## 7. Module wiring honesty

| Module | Documented role | Research `run_pipeline` today | Status |
|--------|-----------------|-------------------------------|--------|
| Exposure dampener / FractureAwareSizer | PIPELINE steps | Not on critical path | **Optional / offline** until wired |
| **PolicyEngine** | Was “allocation constitution” | **Not called** from orchestrator | **Explicit legacy badge** — do not schedule as second choke point; migrate knobs then delete |
| Trinity | Bottom stock execution | Optional Phase-3 gateway | Separate package; AND-only vs macro budget |
| SectorAllocator | Portfolio | Instantiated; limited use | Clarify when fully wired |

### PolicyEngine disposition (review Q4 — **Accepted**)

1. Mark `core/policy_engine.py` as **LEGACY** in module docstring (done).
2. Do **not** wire it into `run_pipeline` as a parallel constitution.
3. Migrate any still-useful constants (e.g. crisis equity caps) into
   `thresholds.yaml` / `hard_constraints.yaml` SSOT or L4 sizers in a later PR.
4. Keep file only for compat tests until removal PR.

---

## 8. Architecture review outcomes (2026-07-15)

| # | Question | Decision |
|---|----------|----------|
| 1 | ADR-001 Option B absolute red line? | **Approved** (Canvas + this map L2.5) |
| 2 | Sole budget choke point = `decide()` + gateway AND-only? | **Approved** |
| 3 | Ephemeral session state until hydration? | **Accepted with amendment**: cold-start `previous_risk_budget=0.0` |
| 4 | PolicyEngine wire-in vs legacy badge? | **Explicit legacy badge**; no wire-in; migrate then deprecate |

---

## 9. Normative rules for new features

1. No new path may set macro `risk_budget` except `decide()` (or tests of `decide()`).
2. Gateway / Trinity may only tighten.
3. Advisory modules never write budgets or orders.
4. New “veto” language must map to the naming table in §4.
5. Absolute physical red lines only via L2.5 fold + ADR-001, not kernel I/O.
