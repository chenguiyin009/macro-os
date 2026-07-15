# ADR-001: Physical Red Line vs SAFETY_GATE Precedence

- **Status:** Accepted (Option B) — implementing on branch feat/adr-001-absolute-red-line
- **Approved:** 2026-07-15 by Macro OS architecture review
- **Implementation notes absorbed:** CIO phase_raw honesty; same-day sticky lock; no kernel reorder
- **Date:** 2026-07-15
- **Owner:** Macro OS v5.0
- **Deciders:** risk owner + architecture owner
- **Related:** `docs/2026-07-03-decision-kernel-v5-design.md` §2.1 / §3,
  `core/decision_kernel.py`, `core/macro/physical_red_lines.py`,
  `runtime/orchestrator.py`

---

## 1. Context

### 1.1 What exists today (LIVE on main @ 61d263e)

1. **Kernel authority order (immutable inside `decide()`):**
   1. `SAFETY_GATE` (divergence phase: CRISIS / LATE / MID / EARLY)
   2. `HARD_VETO` (non-`RISK_ON` hard regime)
   3. `SOFT_POLICY` (`RISK_ON` only)
   4. `GLOBAL_VELOCITY_LIMIT` (upward ramp clamp)

2. **Pre-kernel physical red-line fold (orchestrator):**
   - Pure evaluator: `evaluate_physical_red_lines(features, red_lines)`
   - On hit: `hard_regime = LIQUIDITY_SQUEEZE` (or forced regime)
   - Kernel stays pure (no YAML / no eval / no I/O)
   - Observability: Event `red_line`, Feishu banner, `health().last_red_line_meta`
   - Kernel four-step `audit_trail` keys are **not** extended

3. **Critical interaction (current code behavior):**

```text
if phase in {CRISIS, LATE, MID, EARLY}:
    return SAFETY_GATE path   # may emit risk_budget in (0, 1]
elif hard_regime != RISK_ON:
    return HARD_VETO          # risk_budget = 0, defense = 1
else:
    return SOFT_POLICY (+ velocity)
```

Therefore: when a physical red line fires **and** a divergence phase is active,
`SAFETY_GATE` wins. Examples:

| VIX | phase | hard_regime after fold | authority today | risk_budget today |
|-----|-------|------------------------|-----------------|-------------------|
| 45  | `` (none) | LIQUIDITY_SQUEEZE | HARD_VETO | 0.00 |
| 45  | EARLY | LIQUIDITY_SQUEEZE | SAFETY_GATE | up to 1.00 then velocity |
| 45  | LATE + recovery | LIQUIDITY_SQUEEZE | SAFETY_GATE | up to 0.50–0.65 |
| 45  | CRISIS | LIQUIDITY_SQUEEZE | SAFETY_GATE | 0.00 |

So “physical red line” is **not absolute** under active divergence (except when
SAFETY already pins budget at 0).

### 1.2 Product tension

- **Red-line narrative:** “物理法 / 夺权” — market structure stress should force
  de-risk regardless of recovery ramps.
- **SAFETY narrative:** divergence phase is the structural fracture authority and
  is ordered first in the constitution; recovery protocol must not be silently
  bypassed without an explicit decision.

These two stories conflict on LATE/MID/EARLY + red-line co-fire.

---

## 2. Decision drivers

| Driver | Preference |
|--------|------------|
| Investor safety under VIX/HY/PCE extremes | Prefer hard floor at 0 when red line fires |
| Constitutional simplicity (single order in kernel) | Prefer not reordering SAFETY before HARD inside `decide()` |
| Kernel purity | Prefer fix in **orchestrator inputs**, not kernel I/O |
| Auditability | Final authority + reason must be explainable in Event |
| Compatibility | Existing phase-only tests and four-step audit must stay green |

---

## 3. Options

### Option A — Keep SAFETY-first (status quo, document only)

- **Behavior:** unchanged.
- **Pros:** matches §3 order; no code risk; recovery path remains coherent.
- **Cons:** marketing/ops language “绝对物理红线” is false for EARLY/LATE/MID;
  red-line can “fire” in meta while budget still 0.5–1.0.
- **When acceptable:** red lines are **regime labels**, not physical law.

### Option B — Absolute red line via orchestrator input neutralization (**recommended**)

- **Behavior when `red_verdict.triggered`:**
  1. Keep fold: `hard_regime = forced_hard_regime` (usually `LIQUIDITY_SQUEEZE`).
  2. **Neutralize phase for kernel call only:**
     `divergence_phase_for_kernel = ""` (and do not pass DIVERGED→MID legacy
     via `confirmation_status` unless intentionally required).
  3. Kernel then hits HARD_VETO: `risk=0`, `defense=1`,
     `authority=HARD_VETO`.
  4. Side-channel meta records:
     - `phase_raw` (what divergence engine produced)
     - `phase_for_kernel` (what was passed)
     - `absolute_red_line: true`
- **Pros:**
  - Achieves absolute de-risk without changing kernel authority order.
  - Kernel remains pure; change is one policy at the fold boundary.
  - Clear Event story: “red line absolute override neutralized phase”.
- **Cons:**
  - SAFETY recovery ceilings no longer apply on the same bar as a red-line hit
    (usually desirable if red line is physical law).
  - Must carefully preserve CRISIS already-zero path (behavior equal).
- **Kernel code:** no change required for MVP.

### Option C — Reorder kernel so HARD_VETO evaluates before SAFETY_GATE

- **Behavior:** change `decide()` control flow.
- **Pros:** “absolute” lives inside constitution text.
- **Cons:**
  - Breaks documented immutable order and many audit status assumptions.
  - Couples structural divergence to regime veto rewrite.
  - High regression surface.
- **Reject for v5.x** unless a full constitution revision is planned.

### Option D — Hybrid by severity / phase

- e.g. absolute only for VIX/HY; PCE soft; or absolute only when phase ≠ CRISIS.
- **Pros:** nuanced.
- **Cons:** hard to explain; multiplies test matrix; invites config sprawl.
- **Defer** until Option B runs in production for a soak period.

---

## 4. Recommended decision

### 4.1 Choose **Option B: Absolute physical red line (orchestrator neutralization)**

**Normative rule:**

> If `evaluate_physical_red_lines` returns `triggered=True`, the orchestrator
> MUST pass a hostile `hard_regime` **and** MUST pass an empty/NONE divergence
> phase into `decide()`, so that the kernel HARD_VETO path governs and
> `risk_budget=0`, `defense_budget=1`.

**Non-goals:**

- Do not put YAML/eval inside kernel.
- Do not add a 5th `audit_trail` top-level key.
- Do not claim danger-score is part of this absolute fold (remains `policy_engine`).

### 4.2 Why B over A

| Scenario | A (status quo) | B (recommended) |
|----------|----------------|-----------------|
| VIX=45, phase empty | HARD_VETO 0 | HARD_VETO 0 |
| VIX=45, EARLY | SAFETY can allow high risk | HARD_VETO 0 |
| VIX=45, LATE recovery | SAFETY may allow 0.5–0.65 | HARD_VETO 0 |
| VIX=20, LATE | SAFETY as today | SAFETY as today |
| VIX=45, CRISIS | 0 either way | 0 either way |

Absolute red line only changes the co-fire rows that currently look “soft.”

### 4.3 Precise orchestrator algorithm (design)

```text
phase_raw = map_phase(div_state.score)   # existing
red = evaluate_physical_red_lines(features, red_lines)

hard_regime = red.forced_hard_regime or macro_quadrant

if red.triggered:
    phase_for_kernel = ""                 # neutralize SAFETY entry
    confirmation_for_kernel = ""          # avoid legacy DIVERGED→MID
else:
    phase_for_kernel = phase_raw
    confirmation_for_kernel = confirmation_status  # if any

kd = decide(..., hard_regime=hard_regime,
            divergence_phase=phase_for_kernel,
            confirmation_status=confirmation_for_kernel, ...)

meta = {
  triggered, reason_code, forced_hard_regime, triggered_lines,
  phase_raw, phase_for_kernel,
  absolute_override: red.triggered,
}
```

**Idempotence:** If already CRISIS and red fires, budget stays 0; authority may
flip from SAFETY_GATE to HARD_VETO — acceptable and preferable for reason_code
clarity (`PHYSICAL_RED_LINE_*` side channel + kernel `VETO_REGIME_SQUEEZE_ACTIVE`).

### 4.4 Explicit non-absolute channels (stay as-is)

| Channel | Absolute? | Notes |
|---------|-----------|-------|
| physical_red_lines (VIX/HY/PCE%) | **Yes (after ADR)** | Option B |
| danger_score (0–100) | No (policy allocation) | Not folded into hard_regime in v5 |
| GlenRedLines (legacy fraction PCE) | No | Portfolio construction multiplier |
| RiskGateway FATAL_GAP | Micro execution | Outranks for single-name force flatten; does not rewrite macro budget constitution |

---

## 5. Consequences

### 5.1 Positive

- Ops language can honestly say “physical red line forces full defense budget.”
- No constitution reorder; kernel purity preserved.
- Event meta explains override (`phase_raw` vs `phase_for_kernel`).

### 5.2 Negative / mitigations

| Risk | Mitigation |
|------|------------|
| Hides active divergence from kernel | Store `phase_raw` on Event always |
| Over-trigger if thresholds wrong | Keep unit guards (HY bp ≥ 100, VIX=40, PCE%=3.5) |
| EARLY watch mode no longer coexists with VIX spike | Intended: spike wins |
| Docs currently say SAFETY outranks fold | This ADR supersedes §2.1 pending paragraph |

### 5.3 Implementation blast radius (after approval)

| Area | Change |
|------|--------|
| `runtime/orchestrator.py` | neutralize phase when red triggered; enrich meta |
| `tests/test_orchestrator_*` | LATE/EARLY + VIX=45 → HARD_VETO 0 |
| `docs/PIPELINE.md`, design §2.1 | flip default to absolute Option B |
| `decision_kernel.py` | **no change** for MVP |

---

## 6. Acceptance tests (design contract)

1. `vix=45, phase=""` → HARD_VETO, risk=0, meta.triggered=true  
2. `vix=45, phase="EARLY"` → **HARD_VETO, risk=0** (not SAFETY) under Option B  
3. `vix=45, phase="LATE", recovery_active=true` → HARD_VETO, risk=0  
4. `vix=20, phase="LATE"` → SAFETY_GATE, risk per recovery rules  
5. `vix=45, phase="CRISIS"` → risk=0 (authority HARD_VETO or SAFETY both ok if budget 0; prefer HARD_VETO after neutralize)  
6. audit_trail keys remain exactly four steps  
7. Event payload contains `phase_raw` + `phase_for_kernel` when override

---

## 7. Decision record

| Field | Value |
|-------|-------|
| Proposed default | **Option B — Absolute red line via phase neutralization** |
| Rejected | Option C (kernel reorder) for v5.x |
| Deferred | Option D hybrid severity matrix |
| Requires explicit ACK | Risk owner: absolute vs soft red-line product meaning |
| Status until ACK | **Accepted** — implement Option B |

---

## 8. Review checklist for approvers

- [x] Confirm product intent: red line is physical law (0 budget), not just a label  
- [x] Accept that LATE recovery cannot re-risk on a red-line bar  
- [x] Accept CRISIS co-fire may show HARD_VETO authority instead of SAFETY_GATE  
- [x] Confirm danger remains outside this absolute fold  
- [x] Confirm no kernel reorder (Option C off the table for now)
