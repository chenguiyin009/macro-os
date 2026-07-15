# Decision Authority Map (v5.0 Design Draft)

- **Status:** Draft for P0 architecture review  
- **Date:** 2026-07-15  
- **Depends on:** ADR-001 (red line vs SAFETY)  
- **Code ref:** `main` @ post-#7 (`61d263e` lineage)

---

## 1. Purpose

Answer one question for every subsystem:

> Who may reduce risk, who may only shape weights inside a budget, and who may
> only advise?

Without this map, new features tend to open a third “HARD_VETO-like” channel.

---

## 2. Layered authority (target)

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
│  Sizer / damper / sector allocator / Glen multipliers                     │
│  May: redistribute inside risk_budget + defense_budget                    │
│  May NOT: increase total risk beyond kernel risk_budget                   │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L4b POLICY ENGINE (allocation constitution — secondary)                  │
│  danger crisis, turnover, cash buffer                                     │
│  Status: implement or mark legacy; must not invent parallel kernel budget │
│  Naming: use ALLOC_* reason codes, not AuthorityLevel.HARD_VETO overload  │
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
| Physical red lines + orch fold | Indirect (via hard_regime [+ phase neutralize]) | No | No | No | ADR-001 |
| `decide()` | Yes | Yes | No | No | **only budget law** |
| RiskGateway | Flatten positions | Reduce size | No* | Yes (exec path) | AND tighten only |
| FractureAwareSizer / damper | No | Within budget | Yes (weights) | No | optional on path |
| PolicyEngine | Cap equity (alloc) | Cap equity | No | No | not kernel |
| GlenRedLines | Multiplier | Multiplier | No | No | legacy units |
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
| `ALLOC_CRISIS_CAP` (proposed) | PolicyEngine danger path | Prefer instead of reusing HARD_VETO string alone |
| `FATAL_GAP_LIQUIDATION` | Micro gateway | Precedence for single-name flatten |

---

## 5. Incident precedence (execution time)

When multiple alarms fire on the same cycle:

1. **FATAL_GAP / micro force-flatten** ( Survival of account / gap risk )  
2. **Kernel budget = 0** (SAFETY CRISIS or HARD_VETO after absolute red line)  
3. **Kernel reduced budget** (LATE/MID SAFETY, soft defensive)  
4. **Gateway HOLD / partial exit** (tighten only)  
5. **Advisory warnings** (CIO/shadow text)

Rule: lower layers may only **tighten** residual risk, never expand past kernel.

---

## 6. State that is allowed outside the event log

| State | Today | Target design |
|-------|-------|---------------|
| `previous_risk_budget` | `orchestrator.state` process memory | Document as **session-ephemeral** OR restore from last Event |
| `days_in_recovery` | process memory | Same |
| Sector allocator disk state | `load_state()` | Explicitly non-constitutional |
| Shadow engine | in-memory / files | Advisory only |

P0 doc package should pick one sentence for session state (proposal below in plan).

**Proposal for P0 docs (no code yet):**

> Velocity/recovery session fields are **ephemeral process state**. On process
> restart, `previous_risk_budget` defaults to 0.50 and `days_in_recovery` to 0
> unless a future task implements Event replay hydration.

---

## 7. Open wiring honesty

| Module | Documented role | Research `run_pipeline` today | P0 doc action |
|--------|-----------------|-------------------------------|---------------|
| Exposure dampener / FractureAwareSizer | PIPELINE steps | Not clearly on critical path | Mark **optional / offline** until wired |
| PolicyEngine | Allocation constitution | Not called from orchestrator | Mark **secondary / legacy-or-pending** |
| Trinity | Bottom stock execution | Optional Phase-3 gateway | Keep separate package boundary |
| SectorAllocator | Portfolio | Instantiated; usage limited | Clarify in ARCHITECTURE |

---

## 8. Review questions

1. Approve ADR-001 Option B absolute red line?  
2. Accept sole budget choke point = `decide()` with gateway AND-only?  
3. Accept ephemeral session state wording until hydration ships?  
4. PolicyEngine: schedule wire-in vs explicit legacy badge?
