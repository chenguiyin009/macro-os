# Scheme A — Dual Budget Re-Risk Design

**Date:** 2026-08-21  
**Branch:** `codex/rates-cap`  
**Status:** Implementation  
**Depends on:** D-enhancement, frozen C-tier, rates shadow, PPO flag_only

---

## 0. Problem

Existing `combined_budget = min(legs)` is a **defense ceiling**. It answers
"how high am I allowed while stressed?" but does not answer:

> When am I allowed to **raise** equity exposure again after stress?

Backtests showed:

- Chronic low budget kills 2023-26 bull returns vs buy-hold
- 2022 defense works (D/kernel), but exit can strand at near-zero through relief rallies
- PPO/rates hard do **not** stably improve returns

Scheme A separates **defense** from **re-risk**.

---

## 1. Quantities

| Field | Meaning |
|-------|---------|
| `defense_ceiling` | Same as today's combined min (D held + C + theme + NQ [+ optional soft]) |
| `offense_cap` | Structural max when fully permitted (default 0.80, or min with theme/NQ risk_on) |
| `risk_on_permit` | bool — may step toward higher exposure |
| `permit_blockers` | list of reasons permit=false |
| `target_budget` | Path-dependent suggested exposure for the desk |
| `prev_target` | Yesterday target (persisted) |

Identity:

```
defense_ceiling = min(active defense legs)          # existing logic
target_budget   = step_up/down(prev_target, defense, permit, params)
target_budget  <= max(defense_ceiling, stepped value clamped)
```

**Hard rule:** `target_budget <= max(defense_ceiling, prev_target)` is wrong.
Correct hard rules:

1. **Stress bind:** if `defense_ceiling < prev_target`, cut immediately:
   `target = defense_ceiling` (fast de-risk)
2. **Re-risk:** if `defense_ceiling >= prev_target` and `permit`, may raise
   toward `min(defense_ceiling, offense_cap)` by at most `max_step_up` per day
   after `step_confirm_days` of continuous permit
3. **No permit:** `target = min(prev_target, defense_ceiling)` (hold or cut, never raise)

So defense always wins on the downside; upside needs permit + slow walk.

---

## 2. Permit gate (AND)

`risk_on_permit = true` only if all hold:

| # | Condition | Source |
|---|-----------|--------|
| 1 | denom held tier in {unconfirmed, risk_on} | D-enhancement |
| 2 | denom hysteresis not `pending_looser` waiting on crisis residual optional | prefer held already exited tight |
| 3 | not credit crisis (tier != crisis and no 信用传导) | denom |
| 4 | tech not strong stress: tech_ceiling is None or > `tech_min_for_permit` (default 0.50) | C-tier |
| 5 | PPO gate != BLOCK_ADD (if PPO present) | PPO |
| 6 | structure not LH/LL; prefer ret20>=0 or HH/HL | PPO structure / qqq |
| 7 | curve skew != both_tight; 30Y not extreme engaged | rates shadow |
| 8 | (optional) NQ leads ES when both available | equity |

Any fail → permit=false with blocker code.

---

## 3. Step parameters (defaults)

```yaml
re_risk:
  enabled: true
  offense_cap: 0.80
  max_step_up: 0.10
  max_step_down_extra: 1.0    # defense cut is immediate full gap
  step_confirm_days: 3        # consecutive permit days before first step up
  min_hold_after_up_days: 2   # optional chill after an up-step
  tech_min_for_permit: 0.50
  require_not_lh_ll: true
  require_ret20_nonneg: true  # ret20 >= 0 for permit
  block_both_tight: true
  block_ppo_block_add: true
```

Ladder example: 0.35 → 0.45 → 0.55 → 0.65 → 0.75 → 0.80  
Implementation uses continuous steps of `max_step_up`, not discrete ladder only.

---

## 4. Persistence

From previous `daily_macro_*.json` → `combined_risk_budget`:

- `target_budget`
- `permit_streak`
- `last_up_day` / `days_since_up` (optional)

---

## 5. Output contract

```json
"re_risk": {
  "defense_ceiling": 0.55,
  "offense_cap": 0.80,
  "risk_on_permit": false,
  "permit_blockers": ["denom_tier_tight", "lh_ll"],
  "permit_streak": 0,
  "target_budget": 0.55,
  "prev_target": 0.55,
  "step": 0.0,
  "action": "hold_or_cut"
}
```

Desk primary number for "how much equity risk" becomes **`target_budget`**,  
while **`combined_budget`/`defense_ceiling`** remains the hard stress cap audit trail.

Backward compatible: `combined_budget` stays defense min; add fields alongside.

---

## 6. Files

- `docs/research/2026-08-21-scheme-a-dual-budget-design.md` (this)
- `core/re_risk.py` — pure functions
- `config/daily_macro_consolidated.yaml` — `re_risk:` block
- `scripts/daily_macro_consolidated.py` — wire after combined
- `tests/test_re_risk.py`
- `scripts/backtest_re_risk_scheme_a.py` — BASE vs target path

---

## 7. Backtest success criteria (pre-registered)

Compare **DEFENSE_ONLY** (target:=defense each day) vs **SCHEME_A** (step target):

1. 2023-08..latest QQQ/SPY: Scheme A total return **>** defense-only  
2. 2022 p2t: Scheme A MDD **not worse** than defense-only by > 2pp  
3. 2022 relief windows: Scheme A return **>=** defense-only  
4. If (1) fails or (2) fails badly → keep publishing target but set `enabled` step-up false by default

---

## 8. Non-goals

- Not rates hard min
- Not PPO soft_cap default on
- Not one-day jump to 0.80
- Not gold-driven size-up
