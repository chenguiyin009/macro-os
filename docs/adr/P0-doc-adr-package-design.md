# P0 Doc + ADR Package — Design for Review

- **Status:** Design only (awaiting approval before bulk doc edits / code)  
- **Date:** 2026-07-15  
- **Branch intent:** docs-only PR after ACK  
- **Code freeze for this package:** no kernel behavior change until ADR-001 accepted

---

## 1. Goal

Close the P0 architecture gaps identified in the v5 review:

1. **Single decision on red line vs SAFETY_GATE** (ADR-001)  
2. **One authority map** everyone can cite (ADR-000 / map doc)  
3. **One pipeline truth** in ARCHITECTURE + PIPELINE (stop multi-history diagrams)  
4. **Honest module wiring** (optional vs live)  
5. **Session state policy** written down  

This package is **documentation + decision**, with a thin optional follow-up code
PR only for ADR-001 Option B (phase neutralization) after ACK.

---

## 2. Deliverables

| ID | Artifact | Path | Action |
|----|----------|------|--------|
| D0 | Authority map (draft) | `docs/adr/000-decision-authority-map-v5.md` | **Written (this package)** |
| D1 | ADR red line vs SAFETY | `docs/adr/001-physical-red-line-vs-safety-gate.md` | **Written (this package)** |
| D2 | Doc index pointer | `docs/_INDEX.md` or `docs/ARCHITECTURE.md` top | Planned edit |
| D3 | ARCHITECTURE rewrite | `docs/ARCHITECTURE.md` | Planned: single L1–L4 diagram, remove duplicated L4 blocks |
| D4 | PIPELINE sync | `docs/PIPELINE.md` | Planned: v5 kernel four steps + L2.5 fold + optional tail |
| D5 | DECISION_KERNEL sync | `docs/DECISION_KERNEL.md` | Planned: link ADR-001; fix mojibake if any |
| D6 | Design doc §2.1 | `docs/2026-07-03-decision-kernel-v5-design.md` | Planned: replace “SAFETY outranks” with ADR outcome |
| D7 | CONFIGURATION note | `docs/CONFIGURATION.md` | Planned: red-line SSOT table + units |
| D8 | README architecture blurb | `README.md` | Planned: point to ARCHITECTURE, drop obsolete 1-liner if conflicting |
| D9 | BOUNDARY_MATRIX banner | `docs/BOUNDARY_MATRIX.md` | Planned: “Target / Hermes path” vs “Research path LIVE” |

**Already created for this review:** D0, D1.  
**Not yet modified:** D2–D9 (wait for review feedback to avoid churn).

---

## 3. Proposed single pipeline truth (text diagram for ARCHITECTURE/PIPELINE)

### 3.1 Research path (LIVE — `runtime/orchestrator.py`)

```text
TV / MCP / Mock
  → FeatureSchema + build_features
  → MacroState (quadrant = hard_regime_raw)
  → DivergenceState (phase_raw)
  → Physical red lines (fold → hard_regime_folded)
       [ADR-001: if triggered → phase_for_kernel = empty]
  → decide()  ★ macro budget choke point ★
  → RiskGateway AND (optional)
  → allocation sketch {risk assets: risk_budget, cash/defense: defense_budget}
  → reconciliation diff vs positions
  → Shadow + CIO (advisory)
  → Vault Event (incl. red_line meta) + Feishu
```

### 3.2 Explicitly optional / not on critical path today

```text
NonLinearExposureDampener
FractureAwareSizer
SectorAllocator full targeting
PolicyEngine.execute_constitution
GlenRedLines portfolio multipliers
Trinity full stock SM (except optional gateway bridge)
```

These remain valuable modules but must not appear as unconditional PE boxes
without a “OPTIONAL” label.

### 3.3 Execution path (Trinity / Hermes) — separate swimlane

```text
KernelDecision budgets + symbol universe
  → Trinity structure / spacetime (stock level)
  → Gateway microstructure
  → Broker / Hermes
```

Macro does not select tickers inside `decide()`; Trinity does not widen macro budget.

---

## 4. ADR-001 recommendation summary (for quick vote)

| Choice | Summary | Vote |
|--------|---------|------|
| **B (recommended)** | Absolute red line: orch neutralizes phase so HARD_VETO forces risk=0 | [ ] |
| A | Keep SAFETY-first; red line is soft when phase active | [ ] |
| C | Reorder kernel HARD before SAFETY | [ ] Reject v5 |
| D | Hybrid severity matrix | [ ] Defer |

**Default proposal:** B.

Critical acceptance row:

`vix>=40` + `phase=LATE|EARLY|MID` → final `risk_budget == 0` and side-channel
shows `absolute_override=true`.

---

## 5. Doc edit principles (when implementing D2–D9)

1. **One diagram** in ARCHITECTURE; PIPELINE details the same flow.  
2. **No third simplified README pipeline** that contradicts — README links out.  
3. Version labels: “Research pipeline v5.0”; stop mixing v4.3.1 boxes without notes.  
4. Every “HARD_VETO” string outside kernel gets a qualifier (kernel vs alloc vs log).  
5. Session state: ephemeral wording (Authority Map §6).  
6. Design doc Status: after ADR merge, set §2.1 to Accepted/Live with Option B.  
7. Chinese + English: keep existing bilingual mix; new ADR body can stay EN with
   CN summary section if preferred in review (this draft is CN-friendly tables + EN ADR body).

---

## 6. Follow-up code PR (only if ADR-001 = B)

**Out of scope for pure doc PR; listed for sequencing.**

| Step | Work |
|------|------|
| 1 | Orchestrator: phase neutralize + meta fields |
| 2 | Tests: EARLY/LATE + VIX matrix absolute |
| 3 | dry_run + run_pipeline both |
| 4 | No `decision_kernel.py` change |
| 5 | Small PIPELINE/ADR status flip to Accepted |

---

## 7. Implementation order after approval

```text
Phase Doc (no behavior change)
  1. ACK ADR-001 (+ optional nits on map)
  2. PR: docs only (D0–D9 already drafted / filled)
  3. Merge docs PR

Phase Code (behavior change, only if B)
  4. PR: orchestrator absolute fold + tests
  5. Merge; tag “red-line absolute live”
```

If ADR chooses **A**, skip Phase Code; only docs explain soft red-line honestly.

---

## 8. Review focus questions (please answer)

1. **ADR-001:** A / **B** / C / D ?  
2. Any red-line keys that must stay non-absolute (e.g. only VIX absolute, PCE soft)?  
3. Session state: accept **ephemeral** wording for P0, or require Event hydration in same milestone?  
4. PolicyEngine: **legacy badge** or **wire into run_pipeline** in next milestone?  
5. Doc language: keep ADRs English, or rewrite primary ADR in Chinese?

---

## 9. Files written for this design review

```text
docs/adr/001-physical-red-line-vs-safety-gate.md   # ADR-001 full
docs/adr/000-decision-authority-map-v5.md          # authority map
docs/adr/README.md                                 # index (companion)
docs/superpowers is under tradingview root plans; this package lives in macro-os/docs/adr
```

No production code paths were changed in this design drop.
