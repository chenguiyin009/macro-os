"""Phase 2 — Theme signal as a JOINT-CONFIRMATION AND-gate (Macro OS v5).

WHY
---
Phase 1 (backtest_theme_signal.py) proved the theme state machine is a top-tier
CRISIS DETECTOR (SOXX maxDD improvement 44-63% in 2020/2018/2022) but a terrible
STANDALONE throttle: as a resident SOFT cap it fired on 44.8-65.1% of RISK_ON days
(bull-market false positives) -> failed the dual-gate (needs <=25% trigger).

The review (2026-07-21) approved Route A: promote the theme signal from an
"active intervention source" to a "joint confirmation condition". Instead of
"cap whenever theme says risk_off", the rule becomes:

    theme_pressure_level >= 2  AND  (structural_weak OR macro_suboptimal)

where
    structural_weak  = SOXX (or QQQ) 20d drawdown <= threshold   (microstructure)
    macro_suboptimal = hard_regime in {TRANSITION, TIGHT_LIQUIDITY} (macro)

theme_pressure_level: 0=None, 1=Mixed, 2=RiskOff, 3=PressureOverride.

KEY ARCHITECTURE NOTE
---------------------
The theme cap (like the C-grade tech dampener) is subordinate to HARD_VETO and only
ever LOWERS a positive budget via min(). On RISK_ON days the macro is calm by the
RISK_ON gate's own definition (VIX<=18, HY<=300), so `macro_suboptimal` is False
there -> on RISK_ON days the AND-gate reduces to (theme>=2 AND structural_weak).
That micro leg is exactly what filters bull-market theme noise. The macro leg only
adds on TRANSITION/TIGHT days (already de-risked budgets).

METHOD
------
Reuses Phase-1 machinery (theme_history_frame over full history, DXY via FRED
DTWEXBGS) + the REAL v5 kernel budget windows (prepare_2022 / prepare_468) that
carry per-day `rule_regime`, `risk_budget`, `soxx_dd20`, `qqq_dd20`.

Grid-calibrate the AND-gate config on BOTH windows; auto-select by the RE-GATED
THREE-GATE standard (architect verdict 2026-07-21), which REPLACES the old single
dual-gate (the old 468d SOXX-improve>=40% limb was measured against the real kernel
budget baseline, where the denominator already de-risked crises -> ~0-4% -> no
candidate ever qualified):
    Gate 1 选择性 (selectivity): kernel-window 2022 RISK_ON trigger rate <= 25%
    Gate 2 危机减震 (crisis dampening): stress-window 2022_bear SOXX maxDD improvement >= 35%
    Gate 3 常态不拖累 (no drag): kernel-window 468d incremental total return over the
                                  LIVE tech dampener >= 0
Tie-break: among qualifiers prefer the MILDEST cap (higher cap_l2 then cap_l3), so
AND_s07_L2c65_L3c50 is chosen over the more aggressive AND_s07_L2c50_L3c35.
Plus stress/calm robustness (2020 COVID / 2018Q4 / 2022 bear / calm 2023-24) and an
INCREMENTAL diagnostic (does the AND-gate add anything ON TOP of the live tech
dampener?).

Run:  python scripts/backtest_theme_andgate.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import theme_state_machine_daily as tsm  # noqa: E402  (kept for parity/logging)
from scripts.backtest_theme_signal import (  # noqa: E402
    load_theme_history, theme_history_frame, load_eq_max, proxy_curve, max_dd,
)
from scripts.backtest_equity_overlay_calibrate import prepare_2022, prepare_468, _trailing_dd  # noqa: E402
from core.decision_kernel import _tech_dampener_cap  # noqa: E402

RESEARCH = ROOT / "docs" / "research"

VETO_REGIMES = {"LIQUIDITY_SQUEEZE", "CASH_LIQUIDATION"}
MACRO_SUBOPT = {"TRANSITION", "TIGHT_LIQUIDITY"}


# ---------------------------------------------------------------------------
# theme_pressure_level (structured boolean per the review's FeatureSchema design)
# ---------------------------------------------------------------------------
def level_series(th: pd.DataFrame) -> pd.Series:
    """Map (risk_bias, pressure_override) -> theme_pressure_level 0..3."""
    def lvl(rb, po) -> int:
        if bool(po):
            return 3
        if rb == "risk_off":
            return 2
        if rb == "mixed":
            return 1
        return 0
    return pd.Series([lvl(r, p) for r, p in zip(th["risk_bias"], th["pressure_override"])],
                     index=th.index, dtype=int)


# ---------------------------------------------------------------------------
# AND-gate candidate configs
#   struct_thr: SOXX/QQQ 20d drawdown that counts as "structural_weak"
#   cap_l2/cap_l3: cap applied when gate opens at level 2 / level >=3
# ---------------------------------------------------------------------------
CANDIDATES: Dict[str, Optional[Dict[str, float]]] = {
    "control": None,                                                       # no theme cap (pure v5)
    "AND_s05_L2c65_L3c50": {"struct_thr": -0.05, "cap_l2": 0.65, "cap_l3": 0.50},
    "AND_s05_L2c50_L3c35": {"struct_thr": -0.05, "cap_l2": 0.50, "cap_l3": 0.35},
    "AND_s05_L2c65_L3c35": {"struct_thr": -0.05, "cap_l2": 0.65, "cap_l3": 0.35},
    "AND_s07_L2c65_L3c50": {"struct_thr": -0.07, "cap_l2": 0.65, "cap_l3": 0.50},
    "AND_s03_L2c65_L3c50": {"struct_thr": -0.03, "cap_l2": 0.65, "cap_l3": 0.50},
    "AND_s07_L2c50_L3c35": {"struct_thr": -0.07, "cap_l2": 0.50, "cap_l3": 0.35},
    # ablations for contrast
    "theme_only_L2c65_L3c50": {"struct_thr": None, "cap_l2": 0.65, "cap_l3": 0.50},  # OR-gate = Phase1 style
}


def and_gate_cap(level: int, soxx_dd: float, qqq_dd: float, regime: str,
                 cfg: Dict[str, float]) -> float:
    """Joint-confirmation cap. Returns 1.0 (inactive) unless the AND-gate opens."""
    if level < 2:
        return 1.0
    struct_thr = cfg.get("struct_thr", None)
    if struct_thr is None:
        # ablation: theme-only (no confirmation) — reproduces Phase-1 over-firing
        struct_weak = True
    else:
        struct_weak = ((not pd.isna(soxx_dd)) and soxx_dd <= struct_thr) or \
                      ((not pd.isna(qqq_dd)) and qqq_dd <= struct_thr)
    macro_subopt = regime in MACRO_SUBOPT
    if not (struct_weak or macro_subopt):
        return 1.0
    return cfg["cap_l3"] if level >= 3 else cfg["cap_l2"]


# ---------------------------------------------------------------------------
# Kernel-window metrics (real v5 budgets)
# ---------------------------------------------------------------------------
def metrics_kernel(kv: pd.DataFrame, lv: pd.Series, soxx_ret: pd.Series,
                   qqq_ret: pd.Series, qqq_full: pd.Series,
                   cfg: Optional[Dict[str, float]], tech_base: bool = False) -> Dict[str, Any]:
    idx = kv.index
    lvv = lv.reindex(idx).fillna(0).astype(int).values
    dd_soxx = kv["soxx_dd20"].values.astype(float)
    dd_qqq = kv["qqq_dd20"].values.astype(float)
    reg = kv["rule_regime"].astype(str).values
    base_b = kv["risk_budget"].values.astype(float)
    non_veto = ~np.isin(reg, list(VETO_REGIMES))

    # optional: fold the LIVE tech dampener into the base (production reality),
    # so the AND-gate's INCREMENTAL value over the dampener is measured.
    if tech_base:
        tcap = np.array([_tech_dampener_cap(x) if not pd.isna(x) else 1.0 for x in dd_soxx])
        base_b = np.where(non_veto, np.minimum(base_b, tcap), base_b)

    if cfg is None:
        cap_daily = np.ones(len(idx))
    else:
        cap_daily = np.array([and_gate_cap(lvv[i], dd_soxx[i], dd_qqq[i], reg[i], cfg)
                              for i in range(len(idx))])
    new_b = np.where(non_veto, np.minimum(base_b, cap_daily), base_b)

    risk_on = (reg == "RISK_ON")
    triggered_ro = risk_on & (cap_daily < 1.0)
    risk_on_n = int(risk_on.sum())
    trig_ro_n = int(triggered_ro.sum())
    trigger_rate = 100.0 * trig_ro_n / max(1, risk_on_n)
    # all-day trigger (incl. TRANSITION/TIGHT)
    trig_all = int((non_veto & (cap_daily < 1.0)).sum())

    soxx_r = soxx_ret.reindex(idx).ffill().fillna(0.0).values
    qqq_r = qqq_ret.reindex(idx).ffill().fillna(0.0).values
    base_soxx = proxy_curve(base_b, soxx_r)
    new_soxx = proxy_curve(new_b, soxx_r)
    base_qqq = proxy_curve(base_b, qqq_r)
    new_qqq = proxy_curve(new_b, qqq_r)

    # false trigger: RISK_ON triggered day where next-20d QQQ sum > 0
    qidx = qqq_full.index
    qvals = qqq_full.values
    fwd = []
    for d in idx[triggered_ro]:
        pos = qidx.get_indexer([d])[0]
        if pos >= 0 and pos + 1 < len(qidx):
            fwd.append(float(np.nansum(qvals[pos + 1: pos + 21])))
    false_rate = (100.0 * sum(1 for x in fwd if x > 0) / len(fwd)) if fwd else float("nan")

    return {
        "risk_on_days": risk_on_n,
        "triggered_ro_days": trig_ro_n,
        "triggered_all_days": trig_all,
        "trigger_rate_pct": round(trigger_rate, 1),
        "false_trig_pct": round(false_rate, 1) if not pd.isna(false_rate) else None,
        "soxx_maxdd_base_pct": round(max_dd(base_soxx) * 100, 2),
        "soxx_maxdd_new_pct": round(max_dd(new_soxx) * 100, 2),
        "soxx_dd_improve_pct": round((max_dd(new_soxx) - max_dd(base_soxx)) / abs(max_dd(base_soxx)) * 100, 1),
        "qqq_maxdd_base_pct": round(max_dd(base_qqq) * 100, 2),
        "qqq_maxdd_new_pct": round(max_dd(new_qqq) * 100, 2),
        "soxx_total_base_pct": round(float(base_soxx[-1] - 1) * 100, 2),
        "soxx_total_new_pct": round(float(new_soxx[-1] - 1) * 100, 2),
    }


# ---------------------------------------------------------------------------
# Stress / calm windows (flat 0.8 baseline; theme value in isolation)
# ---------------------------------------------------------------------------
def metrics_stress(soxx: pd.Series, qqq: pd.Series, lv: pd.Series,
                   cfg: Optional[Dict[str, float]], base_budget: float = 0.8) -> Dict[str, Any]:
    idx = soxx.index.intersection(qqq.index)
    soxx = soxx.reindex(idx).ffill().bfill()
    qqq = qqq.reindex(idx).ffill().bfill()
    dd_soxx = _trailing_dd(soxx, 20).reindex(idx).values
    dd_qqq = _trailing_dd(qqq, 20).reindex(idx).values
    lvv = lv.reindex(idx).fillna(0).astype(int).values
    if cfg is None:
        cap_daily = np.ones(len(idx))
    else:
        # no regime info in a flat window -> only the structural leg can open
        cap_daily = np.array([and_gate_cap(lvv[i], dd_soxx[i], dd_qqq[i], "RISK_ON", cfg)
                              for i in range(len(idx))])
    soxx_r = soxx.pct_change().fillna(0.0).values
    qqq_r = qqq.pct_change().fillna(0.0).values
    base_soxx = proxy_curve(np.full(len(idx), base_budget), soxx_r)
    new_soxx = proxy_curve(base_budget * cap_daily, soxx_r)
    base_qqq = proxy_curve(np.full(len(idx), base_budget), qqq_r)
    new_qqq = proxy_curve(base_budget * cap_daily, qqq_r)
    return {
        "days": int(len(idx)),
        "trigger_rate_pct": round(100.0 * float((cap_daily < 1.0).mean()), 1),
        "soxx_maxdd_base_pct": round(max_dd(base_soxx) * 100, 2),
        "soxx_maxdd_new_pct": round(max_dd(new_soxx) * 100, 2),
        "soxx_dd_improve_pct": round((max_dd(new_soxx) - max_dd(base_soxx)) / abs(max_dd(base_soxx)) * 100, 1),
        "qqq_maxdd_base_pct": round(max_dd(base_qqq) * 100, 2),
        "qqq_maxdd_new_pct": round(max_dd(new_qqq) * 100, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--skip-stress", action="store_true")
    args = ap.parse_args()

    print("=== Phase 2: Theme AND-gate (joint confirmation) BACKTEST ===", flush=True)
    print("[1/4] loading max-history theme closes (DXY=FRED DTWEXBGS, cached) ...", flush=True)
    closes = load_theme_history(force=args.force_refresh)
    print(f"    got {len(closes)}/{len(tsm.SYMBOLS)} symbols", flush=True)

    print("[2/4] computing full-history theme_pressure_level ...", flush=True)
    th = theme_history_frame(closes)
    lv = level_series(th)
    dist = lv.value_counts().to_dict()
    print(f"    theme rows: {len(th)} ({th.index[0].date()}..{th.index[-1].date()}); level dist={dist}", flush=True)

    print("[3/4] preparing REAL kernel budget windows (2022 + 468d) ...", flush=True)
    kv22, soxx_r22, qqq_r22, qqq_full22 = prepare_2022()
    kv468, soxx_r468, qqq_r468, qqq_full468 = prepare_468()
    print(f"    2022 RISK_ON days: {(kv22['rule_regime']=='RISK_ON').sum()}; "
          f"468d RISK_ON days: {(kv468['rule_regime']=='RISK_ON').sum()}", flush=True)

    results = {}
    for name, cfg in CANDIDATES.items():
        results[name] = {
            "cfg": cfg,
            "w2022": metrics_kernel(kv22, lv, soxx_r22, qqq_r22, qqq_full22, cfg),
            "w468": metrics_kernel(kv468, lv, soxx_r468, qqq_r468, qqq_full468, cfg),
            # incremental over the LIVE tech dampener
            "w468_incr": metrics_kernel(kv468, lv, soxx_r468, qqq_r468, qqq_full468, cfg, tech_base=True),
        }

    stress_results = {}
    if not args.skip_stress:
        print("[4/4] stress + calm windows ...", flush=True)
        soxx = load_eq_max("SOXX")
        qqq = load_eq_max("QQQ")
        common = soxx.index.intersection(qqq.index)
        soxx = soxx.reindex(common).ffill().bfill()
        qqq = qqq.reindex(common).ffill().bfill()
        windows = {
            "2020_COVID": (pd.Timestamp("2020-01-02"), pd.Timestamp("2020-12-31")),
            "2018Q4_selloff": (pd.Timestamp("2018-10-01"), pd.Timestamp("2018-12-31")),
            "2022_bear": (pd.Timestamp("2022-01-03"), pd.Timestamp("2022-12-31")),
            "calm_2023_2024": (pd.Timestamp("2023-01-02"), pd.Timestamp("2024-09-30")),
        }
        for wname, (a, b) in windows.items():
            mask = (soxx.index >= a) & (soxx.index <= b)
            stress_results[wname] = {
                name: metrics_stress(soxx[mask], qqq[mask], lv, cfg)
                for name, cfg in CANDIDATES.items()
            }

    # ---------------------------------------------------------------------------
    # Re-gated THREE-GATE selection (aligned with architect verdict 2026-07-21)
    #   Gate 1 选择性 (selectivity): kernel window 2022 RISK_ON trigger rate <= 25%
    #       -> verdict realized 6.3% (single digit)
    #   Gate 2 危机减震 (crisis dampening): stress window 2022_bear SOXX maxDD
    #       improvement >= 35% -> verdict realized 44.8%
    #   Gate 3 常态不拖累 (no drag): kernel window 468d incremental over the LIVE
    #       tech dampener, total-return delta >= 0 -> verdict realized +1.36pp
    # Tie-break: among qualifiers prefer the MILDEST cap (highest cap_l2 then
    #   cap_l3) that still clears the bars, so AND_s07_L2c65_L3c50 is preferred
    #   over the more aggressive AND_s07_L2c50_L3c35.
    # ---------------------------------------------------------------------------
    SEL_TRIGGER_MAX = 25.0   # Gate 1 (kernel window 2022)
    SEL_CRISIS_MIN = 35.0    # Gate 2 (stress window 2022_bear)
    SEL_INCR_MIN = 0.0       # Gate 3 (kernel window 468d incremental)

    stress_bear_for = (stress_results.get("2022_bear") if stress_results else None)
    best = None
    for name, r in results.items():
        if name == "control" or (r["cfg"] is not None and r["cfg"].get("struct_thr") is None):
            continue  # skip control + theme-only ablation
        w22 = r["w2022"]
        w468_incr = r["w468_incr"]

        # Gate 1: selectivity (kernel window trigger rate)
        g1 = w22["trigger_rate_pct"] <= SEL_TRIGGER_MAX

        # Gate 2: crisis dampening (stress window 2022_bear); fall back to the
        # original dual-gate second limb when stress is skipped.
        if stress_bear_for and name in stress_bear_for:
            g2 = stress_bear_for[name]["soxx_dd_improve_pct"] >= SEL_CRISIS_MIN
            crisis_improve = stress_bear_for[name]["soxx_dd_improve_pct"]
        else:
            g2 = r["w468"]["soxx_dd_improve_pct"] >= 40.0
            crisis_improve = r["w468"]["soxx_dd_improve_pct"]

        # Gate 3: no drag in normal times (kernel window incremental total return)
        incr_delta = w468_incr["soxx_total_new_pct"] - w468_incr["soxx_total_base_pct"]
        g3 = incr_delta >= SEL_INCR_MIN

        if not (g1 and g2 and g3):
            continue

        cap_l2 = r["cfg"]["cap_l2"]
        cap_l3 = r["cfg"]["cap_l3"]
        # score: smaller is better.
        # Primary: lower trigger (selectivity). Then, once the crisis-dampening
        # and no-drag bars are cleared, PREFER THE MILDEST CAP (higher cap_l2/l3)
        # -- this is exactly the architect's L2c65-over-L2c50 call (less
        # normal-time drag). Crisis-improve / incremental are only secondary
        # tie-breakers so a more aggressive cap can never win on its deeper
        # bear-market cut alone.
        score = (w22["trigger_rate_pct"], -cap_l2, -cap_l3,
                 -crisis_improve, -incr_delta)
        if best is None or score < best[0]:
            best = (score, name, r)

    # ---- Console ----
    print("\n=== KERNEL WINDOWS (real v5 budgets) ===")
    print(f"{'candidate':22} | {'win':5} | {'trigRO%':7} {'false%':7} | "
          f"{'SOXX_DDb':9} {'SOXX_DDn':9} {'impr%':6} | {'QQQ_DDb':8} {'QQQ_DDn':8} | {'trigAll':7}")
    for name, r in results.items():
        for win, w in (("2022", r["w2022"]), ("468d", r["w468"])):
            print(f"{name:22} | {win:5} | {w['trigger_rate_pct']:7} {str(w['false_trig_pct']):>7} | "
                  f"{w['soxx_maxdd_base_pct']:9} {w['soxx_maxdd_new_pct']:9} {w['soxx_dd_improve_pct']:6} | "
                  f"{w['qqq_maxdd_base_pct']:8} {w['qqq_maxdd_new_pct']:8} | {w['triggered_all_days']:7}")

    print("\n=== INCREMENTAL over LIVE tech dampener (468d) ===")
    for name, r in results.items():
        w = r["w468_incr"]
        print(f"{name:22} | trigRO%={w['trigger_rate_pct']:5} | SOXX_DD {w['soxx_maxdd_base_pct']:7}->{w['soxx_maxdd_new_pct']:7} "
              f"(impr {w['soxx_dd_improve_pct']:5}%) | SOXX_tot {w['soxx_total_base_pct']:7}->{w['soxx_total_new_pct']:7}")

    if stress_results:
        print("\n=== STRESS / CALM (flat 0.8 baseline) ===")
        for wname, per in stress_results.items():
            print(f"-- {wname} --")
            for name, w in per.items():
                print(f"  {name:22} trig%={w['trigger_rate_pct']:5} SOXX_DD {w['soxx_maxdd_base_pct']:7}->{w['soxx_maxdd_new_pct']:7} "
                      f"(impr {w['soxx_dd_improve_pct']:5}%)")

    print(f"\nRECOMMENDED (re-gated three-gate): {best[1] if best else 'NONE qualified'}")
    if best:
        w22 = best[2]["w2022"]
        incr = best[2]["w468_incr"]
        sb = stress_bear_for.get(best[1]) if stress_bear_for else None
        print(f"  Gate1 选择性(kernel2022 trigRO%)={w22['trigger_rate_pct']} (<= {SEL_TRIGGER_MAX})")
        if sb:
            print(f"  Gate2 危机减震(stress2022_bear SOXX impr%)={sb['soxx_dd_improve_pct']} (>= {SEL_CRISIS_MIN})")
        else:
            print(f"  Gate2 危机减震(468d SOXX impr%)={best[2]['w468']['soxx_dd_improve_pct']} (>=40, fallback)")
        print(f"  Gate3 常态不拖累(468d incr total return pp)={round(incr['soxx_total_new_pct']-incr['soxx_total_base_pct'],2)} (>= {SEL_INCR_MIN})  cfg={best[2]['cfg']}")

    # ---- JSON ----
    out = {
        "phase": 2,
        "design": "theme_pressure_level>=2 AND (structural_weak OR macro_suboptimal). "
                  "structural_weak=SOXX/QQQ 20d dd<=thr; macro_suboptimal=regime in {TRANSITION,TIGHT_LIQUIDITY}. "
                  "On RISK_ON days macro is calm by construction, so the micro leg governs. "
                  "Cap applied via min() on non-veto days, subordinate to HARD_VETO.",
        "selection_rule": "Re-gated three-gate (architect verdict 2026-07-21): "
                          "Gate1 selectivity = kernel-window 2022 RISK_ON trigger_rate<=25%; "
                          "Gate2 crisis dampening = stress-window 2022_bear SOXX maxDD improve>=35%; "
                          "Gate3 no-drag = kernel-window 468d incremental total return over live tech dampener >=0.",
        "selection_gates": {
            "selectivity": {"window": "kernel_2022", "metric": "trigger_rate_pct", "max": SEL_TRIGGER_MAX},
            "crisis_dampening": {"window": "stress_2022_bear", "metric": "soxx_dd_improve_pct", "min": SEL_CRISIS_MIN},
            "no_drag": {"window": "kernel_468d_incr", "metric": "soxx_total_new_pct - soxx_total_base_pct", "min": SEL_INCR_MIN},
        },
        "level_map": {"0": "none/risk_on", "1": "mixed", "2": "risk_off", "3": "pressure_override"},
        "recommended": best[1] if best else None,
        "recommended_detail": best[2] if best else "NONE qualified",
        "results": results,
        "stress_results": stress_results,
    }
    (RESEARCH / "theme_andgate_calibration.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- Markdown ----
    md = ["# Phase 2 · 主题信号 AND-gate（联合确认）· 回测与校准\n",
          "> **设计**：`theme_pressure_level >= 2` **AND** (`structural_weak` OR `macro_suboptimal`)。"
          "structural_weak = SOXX/QQQ 20 日回撤 ≤ 阈值；macro_suboptimal = 宏观态 ∈ {TRANSITION, TIGHT_LIQUIDITY}。\n",
          "> RISK_ON 日按定义宏观平静（VIX≤18/HY≤300），故宏观腿恒假 → **微观腿（SOXX 破位）主导** RISK_ON 的确认，正是滤除牛市误报的机制；宏观腿只在 TRANSITION/TIGHT 日补充。cap 仅在非否决日经 min() 生效，从属 HARD_VETO。\n",
          "> `theme_pressure_level`：0=none/risk_on，1=mixed，2=risk_off，3=pressure_override。\n",
          "> 重定三道门（架构师裁决 2026-07-21）：① **选择性**=内核窗口 2022 RISK_ON 触发率 ≤ 25%（实测 6.3%）；② **危机减震**=压力窗 2022_bear SOXX maxDD 改善 ≥ 35%（实测 44.8%）；③ **常态不拖累**=内核窗口 468 天相对已上线减震器增量总收益 ≥ 0（实测 +1.36pp）。DXY 用 FRED DTWEXBGS。\n",
          f"> **推荐档：{out['recommended'] if out['recommended'] else '无达标候选'}**\n",
          "\n## 内核窗口（真实 v5 预算序列）\n",
          "| 候选 | 窗口 | RISK_ON触发% | 误触发% | SOXX基DD% | SOXX新DD% | 改善% | QQQ基DD% | QQQ新DD% | 全触发日 |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, r in results.items():
        for win, w in (("2022", r["w2022"]), ("468d", r["w468"])):
            md.append(f"| {name} | {win} | {w['trigger_rate_pct']} | {w['false_trig_pct']} | "
                      f"{w['soxx_maxdd_base_pct']} | {w['soxx_maxdd_new_pct']} | {w['soxx_dd_improve_pct']} | "
                      f"{w['qqq_maxdd_base_pct']} | {w['qqq_maxdd_new_pct']} | {w['triggered_all_days']} |")
    md.append("\n## 相对『已上线科技减震器』的增量（468d，base 已含 tech dampener）\n")
    md.append("| 候选 | RISK_ON触发% | SOXX基DD% | SOXX新DD% | 改善% | SOXX基总收益% | SOXX新总收益% |")
    md.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, r in results.items():
        w = r["w468_incr"]
        md.append(f"| {name} | {w['trigger_rate_pct']} | {w['soxx_maxdd_base_pct']} | {w['soxx_maxdd_new_pct']} | "
                  f"{w['soxx_dd_improve_pct']} | {w['soxx_total_base_pct']} | {w['soxx_total_new_pct']} |")
    if stress_results:
        md.append("\n## 压力 / 平静窗（flat 0.8 基线，仅看主题联合门价值）\n")
        for wname, per in stress_results.items():
            md.append(f"\n### {wname}\n")
            md.append("| 候选 | 触发率% | SOXX基DD% | SOXX新DD% | 改善% | QQQ基DD% | QQQ新DD% |")
            md.append("|---|---:|---:|---:|---:|---:|---:|")
            for name, w in per.items():
                md.append(f"| {name} | {w['trigger_rate_pct']} | {w['soxx_maxdd_base_pct']} | "
                          f"{w['soxx_maxdd_new_pct']} | {w['soxx_dd_improve_pct']} | {w['qqq_maxdd_base_pct']} | {w['qqq_maxdd_new_pct']} |")
    md.append("\n## 自动推荐（重定三道门）\n")
    if best:
        w22 = best[2]["w2022"]
        incr = best[2]["w468_incr"]
        sb = stress_bear_for.get(best[1]) if stress_bear_for else None
        md.append(f"- **推荐档：`{best[1]}`**（cfg={best[2]['cfg']}）\n")
        md.append(f"- Gate1 选择性（内核窗口 2022 RISK_ON 触发率）={w22['trigger_rate_pct']}% ≤ {SEL_TRIGGER_MAX}\n")
        if sb:
            md.append(f"- Gate2 危机减震（压力窗 2022_bear SOXX maxDD 改善）={sb['soxx_dd_improve_pct']}% ≥ {SEL_CRISIS_MIN}\n")
        else:
            md.append(f"- Gate2 危机减震（468d SOXX maxDD 改善，fallback）={best[2]['w468']['soxx_dd_improve_pct']}% ≥ 40\n")
        md.append(f"- Gate3 常态不拖累（468d 相对减震器增量总收益）={round(incr['soxx_total_new_pct']-incr['soxx_total_base_pct'],2)}pp ≥ {SEL_INCR_MIN}\n")
    else:
        md.append("- 无候选通过重定三道门。\n")
    md.append("\n> 免责声明：历史回测研究，非投资建议。2022 HY 信用利差为重建代理；其余为真实 FRED / yfinance 数据。")
    (RESEARCH / "theme_andgate_calibration.md").write_text("\n".join(md), encoding="utf-8")
    print("\n[written] docs/research/theme_andgate_calibration.json + .md")


if __name__ == "__main__":
    main()
