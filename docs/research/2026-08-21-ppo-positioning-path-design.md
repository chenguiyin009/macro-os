# Positioning Path Overlay (PPO) — Detailed Design

**Date:** 2026-08-21  
**Branch:** `codex/rates-cap`  
**Status:** Implemented  
**Stack:** D-enhancement + frozen C-tier + rates_stress shadow

---

## 0. Goals

Encode the real-rate / NQ positioning checklist as an observation-layer path gate.

Default: **flag_only** (advice + JSON, no combined min leg).  
Optional: **soft_cap** (DETERIOR 0.55 / BAD_EASE 0.45) behind backtest gate.

### Non-goals / forbidden regressions

- rates hard into combined min by default
- default G full min
- gold denominator hard votes
- auto risk-on when 10Y real dips alone

---

## 1. Architecture

```
denom D+hyst ──┐
tech C-tier  ──┼──► daily_macro_consolidated ──► combined_budget
rates shadow ──┤              │
NQ/QQQ/ES    ──┤              └── positioning_path (this module)
credit/gold  ──┘
```

---

## 2. Paths and gates

| path | zh | gate |
|------|-----|------|
| REPAIR | 定位修复 | ALLOW_DISCUSS |
| DETERIOR | 定位恶化 | BLOCK_ADD |
| BAD_EASE | 糟糕宽松 | BLOCK_ADD |
| MIXED | 混合/未确认 | NEUTRAL |
| UNKNOWN | 数据不足 | NEUTRAL |

Priority: BAD_EASE > DETERIOR > REPAIR > MIXED

---

## 3. Rules (defaults)

```yaml
positioning_path:
  enabled: true
  mode: flag_only
  z_dead: 0.5
  z_enter: 1.0
  nq_break_ret_20: -0.05
  nq_lead_eps: 0.0
  soft_cap_deteriorate: 0.55
  soft_cap_bad_ease: 0.45
  confirm_days: 1
  exit_days: 2
```

Atoms:

- REAL_NOT_RISING: tips_z5 < +z_dead
- REAL_FALLING: tips_z5 <= -z_dead
- N30_NOT_SPIKING: n30_z5 < +z_enter
- NQ_STRUCT_BAD: qqq_ret_20 <= nq_break_ret_20
- NQ_STRUCT_OK: qqq_ret_20 >= 0 and not BAD
- NQ_LEADS: qqq_ret_20 >= es_ret_20 + eps (skip if ES missing)
- CREDIT_WIDE: hy_z5 <= -1.0 or denom credit crisis
- GOLD_BID: gold_z5 >= +z_dead
- BE_FALLING: bei_d5 clearly negative (else false)
- SETTLE_OK: default true (calendar plugin later)

Curve skew: ten_easy_thirty_tight | both_tight | both_easy | ten_tight_thirty_easy | mixed  
REPAIR veto if both_tight or rates extreme engaged on 30Y.

---

## 4. Combined budget

- flag_only: no min() contribution
- soft_cap: append ("定位", cap) when held path is DETERIOR/BAD_EASE

---

## 5. Backtest success criteria (pre-registered)

Variants: BASE, PPO_flag, PPO_soft, CTRL_E, CTRL_G  
Windows: 2022 p2t+full; 2023-08..latest full + drawdown union  
Assets: QQQ, SPY; lag-1; 5bps friction

1. DETERIOR days: fwd 5d/20d QQQ mean <= non-DETERIOR  
2. PPO_soft bear: excess/MDD not worse than BASE  
3. PPO_soft full: Sharpe >= BASE-0.05 and total >= BASE-3pp  
Else production stays flag_only.

---

## 6. Files

- core/positioning_path.py
- config/daily_macro_consolidated.yaml
- scripts/daily_macro_consolidated.py
- scripts/backtest_ppo_overlay.py
- tests/test_positioning_path.py
- docs/research/ppo_backtest_qqq_spy.md
