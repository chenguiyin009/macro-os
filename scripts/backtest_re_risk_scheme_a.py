#!/usr/bin/env python3
"""Scheme A backtest: DEFENSE_ONLY vs RE_RISK target on QQQ/SPY."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.denom_ceiling import classify_denom_tier, series_bind  # noqa: E402
from core.denominator_state import DenominatorParams, compute_denominator_states  # noqa: E402
from core.positioning_path import PPOParams, apply_path_hysteresis, classify_path  # noqa: E402
from core.rates_stress import RatesStressParams, compute_rates_stress_series  # noqa: E402
from core.re_risk import ReRiskParams, compute_re_risk  # noqa: E402

DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"
FRICTION = 0.0005
TECH_TIERS = [(-0.13, 0.35), (-0.10, 0.50), (-0.07, 0.65)]
DENOM_BLOCK = {
    "crisis": {"ceiling": 0.10},
    "tight": {"ceiling": 0.35},
    "unconfirmed": {"ceiling": 0.55},
    "risk_on": {"ceiling": 0.80},
    "default": 0.55,
}


def ann_stats(rets: pd.Series):
    r = rets.dropna()
    if r.empty:
        return {"total": 0.0, "ann": 0.0, "sharpe": 0.0, "mdd": 0.0, "n": 0}
    total = float((1 + r).prod() - 1)
    n = len(r)
    years = max(n / 252.0, 1e-9)
    ann = float((1 + total) ** (1 / years) - 1)
    vol = float(r.std() * np.sqrt(252)) if n > 2 else float("nan")
    sh = float(ann / vol) if vol and vol > 1e-12 else float("nan")
    nav = (1 + r).cumprod()
    mdd = float(((nav.cummax() - nav) / nav.cummax()).max())
    return {"total": round(total, 4), "ann": round(ann, 4), "sharpe": round(sh, 3), "mdd": round(mdd, 4), "n": n}


def nav_bud(budget: pd.Series, ret: pd.Series):
    b = budget.reindex(ret.index).ffill().fillna(1.0)
    bl = b.shift(1).fillna(b.iloc[0])
    fr = (bl.diff().abs() > 1e-9).astype(float) * FRICTION
    sr = bl * ret - fr
    return (1 + sr.fillna(0)).cumprod(), sr


def tech_cap(dd):
    c = pd.Series(1.0, index=dd.index)
    for thr, cap in reversed(TECH_TIERS):
        c = c.where(dd > thr, cap)
    return c.fillna(1.0)


def run_panel(idx, qret, sret, defense, paths_raw, ppo_flags_series, tiers, rates_eng, name):
    # held paths
    held = apply_path_hysteresis(
        paths_raw.reindex(idx).fillna("MIXED").tolist(),
        confirm_days=2,
        exit_days=2,
        deteriorate_confirm_days=3,
    )
    held_s = pd.Series(held, index=idx)
    # build target path
    rr_p = ReRiskParams(step_confirm_days=3, max_step_up=0.10, min_hold_after_up_days=2)
    targets = []
    defenses = []
    permits = []
    state = None
    q20 = (1 + qret.fillna(0)).cumprod().pct_change(20)
    for dt in idx:
        dceil = float(defense.loc[dt])
        path = str(held_s.loc[dt])
        gate = "BLOCK_ADD" if path in ("DETERIOR", "BAD_EASE") else (
            "ALLOW_DISCUSS" if path == "REPAIR" else "NEUTRAL"
        )
        flags = ppo_flags_series.loc[dt] if dt in ppo_flags_series.index else {}
        if not isinstance(flags, dict):
            flags = {}
        tier = str(tiers.loc[dt]) if dt in tiers.index else "unconfirmed"
        snap = compute_re_risk(
            defense_ceiling=dceil,
            prev_state=state,
            denom_tier=tier,
            tech_ceiling=0.80,  # panel may not have daily tech; defense already includes tech in defense series
            ppo={"gate": gate, "flags": flags, "metrics": {"qqq_ret_20": float(q20.loc[dt]) if pd.notna(q20.loc[dt]) else 0.0}, "skew": flags.get("skew", "mixed")},
            rates_shadow={"engaged": bool(rates_eng.loc[dt]) if dt in rates_eng.index else False},
            qqq_ret_20=float(q20.loc[dt]) if pd.notna(q20.loc[dt]) else 0.0,
            params=rr_p,
        )
        state = {
            "target_budget": snap["target_budget"],
            "permit_streak": snap["permit_streak"],
            "days_since_up": snap["days_since_up"],
        }
        targets.append(snap["target_budget"])
        defenses.append(dceil)
        permits.append(snap["risk_on_permit"])
    target = pd.Series(targets, index=idx)
    def_s = pd.Series(defenses, index=idx)
    # Sticky: once below rolling start, never rise — models "cut and freeze" behavior
    sticky_vals = []
    smin = float(def_s.iloc[0])
    for v in def_s.values:
        smin = min(smin, float(v))
        sticky_vals.append(smin)
    sticky = pd.Series(sticky_vals, index=idx)
    schemes = {
        "STICKY_CUT": sticky,
        "DEFENSE_ONLY": def_s,
        "SCHEME_A": target,
        "BUY_HOLD": pd.Series(1.0, index=idx),
    }
    out = {"window": name, "start": str(idx.min().date()), "end": str(idx.max().date()), "n": len(idx),
           "permit_frac": round(float(np.mean(permits)), 4), "assets": {}}
    for asset, ret in (("QQQ", qret), ("SPY", sret)):
        rows = []
        r = ret.reindex(idx)
        _, bh = nav_bud(schemes["BUY_HOLD"], r)
        bhs = ann_stats(bh)
        for sk, bud in schemes.items():
            _, sr = nav_bud(bud, r)
            st = ann_stats(sr)
            rows.append({
                "scheme": sk,
                **st,
                "mean_budget": round(float(bud.mean()), 4),
                "excess": round(st["total"] - bhs["total"], 4),
                "mdd_improve": round(bhs["mdd"] - st["mdd"], 4),
            })
        rows.sort(key=lambda x: (x["sharpe"] or -9, x["total"]), reverse=True)
        out["assets"][asset] = rows
    # compare A vs defense
    def get(asset, sk):
        for row in out["assets"][asset]:
            if row["scheme"] == sk:
                return row
        return None
    out["delta_vs_defense"] = {}
    out["delta_vs_sticky"] = {}
    for asset in ("QQQ", "SPY"):
        a, d, s = get(asset, "SCHEME_A"), get(asset, "DEFENSE_ONLY"), get(asset, "STICKY_CUT")
        out["delta_vs_defense"][asset] = {
            "total_pp": round((a["total"] - d["total"]) * 100, 2),
            "sharpe_diff": round((a["sharpe"] or 0) - (d["sharpe"] or 0), 3),
            "mdd_pp": round((a["mdd"] - d["mdd"]) * 100, 2),
        }
        out["delta_vs_sticky"][asset] = {
            "total_pp": round((a["total"] - s["total"]) * 100, 2),
            "sharpe_diff": round((a["sharpe"] or 0) - (s["sharpe"] or 0), 3),
            "mdd_pp": round((a["mdd"] - s["mdd"]) * 100, 2),
        }
    return out


def main():
    results = {}
    # Recent from 3y csv
    fp = RESEARCH / "rates_cap_3yr_qqq_spy.csv"
    if fp.exists():
        df = pd.read_csv(fp, parse_dates=["date"]).set_index("date").sort_index()
        idx = df.index
        qret, sret = df["qqq_ret"], df["spy_ret"]
        kernel = df["kernel"] if "kernel" in df.columns else pd.Series(0.8, index=idx)
        tech = df["tech_cap"] if "tech_cap" in df.columns else pd.Series(1.0, index=idx)
        denom = df["denom_cap"] if "denom_cap" in df.columns else pd.Series(0.55, index=idx)
        defense = np.minimum(np.minimum(kernel, tech), denom)
        # paths via qqq structure
        from core.positioning_path import classify_path as cp
        qpx = (1 + qret.fillna(0)).cumprod()
        raw = []
        flags = {}
        for dt in idx:
            loc = qpx.index.get_loc(dt)
            if isinstance(loc, slice):
                loc = loc.stop - 1
            closes = [float(x) for x in qpx.iloc[max(0, int(loc) - 79): int(loc) + 1].values]
            r20 = float(qpx.pct_change(20).loc[dt]) if pd.notna(qpx.pct_change(20).loc[dt]) else 0.0
            feat = {"tips_z5": 0.0, "n30_z5": 0.0, "qqq_ret_20": r20, "qqq_closes": closes, "denom_tier": "unconfirmed"}
            o = cp(feat, PPOParams())
            raw.append(o["path"])
            flags[dt] = o.get("flags") or {}
        paths = pd.Series(raw, index=idx)
        flag_s = pd.Series(flags)
        tiers = pd.Series("unconfirmed", index=idx)
        # better tiers from denom if rebuild
        try:
            from scripts.backtest_denominator_state import build_frame
            frame, _ = build_frame()
            ds = compute_denominator_states(frame, DenominatorParams())
            st = ds["state"].reindex(idx).ffill()
            bound = series_bind(st.astype(str).tolist(), DENOM_BLOCK, {"confirm_enter": 1, "confirm_exit": 3})
            denom = pd.Series([b["ceiling"] for b in bound], index=st.index).reindex(idx).ffill()
            tiers = pd.Series([b["tier"] for b in bound], index=st.index).reindex(idx).ffill()
            defense = np.minimum(np.minimum(kernel.astype(float), tech.astype(float)), denom.astype(float))
            rs = compute_rates_stress_series(frame[["nominal_30y", "tips_yield"]].dropna(), RatesStressParams())
            reng = rs["engaged"].reindex(idx).fillna(False)
        except Exception as exc:
            print("rebuild warn", exc)
            reng = pd.Series(False, index=idx)
        results["recent_full"] = run_panel(idx, qret, sret, pd.Series(defense, index=idx), paths, flag_s, tiers, reng, "recent_full")

    fp22 = RESEARCH / "rates_cap_2022_bear_qqq_spy.csv"
    if fp22.exists():
        d22 = pd.read_csv(fp22, parse_dates=["date"]).set_index("date").sort_index()
        idx = d22.index
        qret, sret = d22["qqq_ret"], d22["spy_ret"]
        defense = np.minimum(np.minimum(d22["kernel"], d22["tech_cap"]), d22["denom_cap"])
        qpx = (1 + qret.fillna(0)).cumprod()
        raw, flags = [], {}
        for dt in idx:
            loc = qpx.index.get_loc(dt)
            if isinstance(loc, slice):
                loc = loc.stop - 1
            closes = [float(x) for x in qpx.iloc[max(0, int(loc) - 79): int(loc) + 1].values]
            r20 = float(qpx.pct_change(20).loc[dt]) if pd.notna(qpx.pct_change(20).loc[dt]) else 0.0
            st = str(d22.loc[dt, "denom_state"]) if "denom_state" in d22.columns else "分裂/未确认"
            feat = {
                "tips_z5": 0.3 if str(d22.loc[dt].get("regime", "")) == "LIQUIDITY_SQUEEZE" else 0.0,
                "n30_z5": 0.0,
                "qqq_ret_20": r20,
                "qqq_closes": closes,
                "denom_state": st,
                "denom_tier": classify_denom_tier(st),
            }
            o = classify_path(feat, PPOParams())
            raw.append(o["path"])
            flags[dt] = o.get("flags") or {}
        paths = pd.Series(raw, index=idx)
        flag_s = pd.Series(flags)
        tiers = pd.Series([classify_denom_tier(str(d22.loc[dt, "denom_state"]) if "denom_state" in d22.columns else "") for dt in idx], index=idx)
        reng = d22["rates_engaged"].astype(bool) if "rates_engaged" in d22.columns else pd.Series(False, index=idx)
        p2t = idx[(idx >= "2022-01-03") & (idx <= "2022-10-12")]
        results["2022_p2t"] = run_panel(p2t, qret, sret, pd.Series(defense, index=idx), paths, flag_s, tiers, reng, "2022_p2t")
        results["2022_full"] = run_panel(idx, qret, sret, pd.Series(defense, index=idx), paths, flag_s, tiers, reng, "2022_full")
        relief = idx[(idx >= "2022-06-17") & (idx <= "2022-08-16")]
        if len(relief) > 10:
            results["2022_relief_JunAug"] = run_panel(relief, qret, sret, pd.Series(defense, index=idx), paths, flag_s, tiers, reng, "2022_relief_JunAug")

    # verdict
    notes = []
    # Pre-registered (corrected): Scheme A cannot beat live DEFENSE_ONLY on long-only
    # by construction (target <= defense). Primary alpha claim is vs STICKY_CUT.
    ok = {
        "recent_gt_sticky": True,
        "bear_mdd_vs_defense": True,
        "a_not_far_below_defense_bull": True,
    }
    if "recent_full" in results:
        for asset in ("QQQ", "SPY"):
            dlt_s = results["recent_full"]["delta_vs_sticky"][asset]["total_pp"]
            dlt_d = results["recent_full"]["delta_vs_defense"][asset]["total_pp"]
            if dlt_s <= 0:
                ok["recent_gt_sticky"] = False
                notes.append(f"recent {asset} A vs sticky total_pp {dlt_s} <= 0")
            # allow lag cost vs live defense up to 15pp total over ~3y
            if dlt_d < -15.0:
                ok["a_not_far_below_defense_bull"] = False
                notes.append(f"recent {asset} A vs defense total_pp {dlt_d} < -15")
    if "2022_p2t" in results:
        for asset in ("QQQ", "SPY"):
            mdd_pp = results["2022_p2t"]["delta_vs_defense"][asset]["mdd_pp"]
            if mdd_pp > 2.0:
                ok["bear_mdd_vs_defense"] = False
                notes.append(f"2022p2t {asset} MDD worse vs defense by {mdd_pp}pp")

    recommend = ok["recent_gt_sticky"] and ok["bear_mdd_vs_defense"] and ok["a_not_far_below_defense_bull"]
    summary = {
        "ok": ok,
        "recommend_enable_step_up": recommend,
        "notes": notes,
        "windows": results,
    }
    (RESEARCH / "scheme_a_re_risk_backtest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = ["# Scheme A Re-Risk Backtest", "", "Note: target_budget <= defense_ceiling always; A cannot beat live DEFENSE_ONLY on long-only returns by construction. Value is measured primarily vs STICKY_CUT (cut-and-freeze).", ""]
    lines.append(f"- recommend_enable_step_up: **{recommend}**")
    lines.append(f"- ok={ok}")
    for n in notes:
        lines.append(f"- note: {n}")
    lines.append("")
    for w, blk in results.items():
        lines.append(f"## {w} ({blk['start']} → {blk['end']}, n={blk['n']}, permit_frac={blk['permit_frac']})")
        lines.append("")
        lines.append(f"- delta vs DEFENSE: `{blk.get('delta_vs_defense')}`")
        lines.append(f"- delta vs STICKY: `{blk.get('delta_vs_sticky')}`")
        for asset in ("QQQ", "SPY"):
            lines.append(f"### {asset}")
            lines.append("| scheme | total | excess | MDD | Sharpe | mean_b |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for r in blk["assets"][asset]:
                lines.append(
                    f"| {r['scheme']} | {r['total']*100:.1f}% | {r['excess']*100:+.1f}pp | "
                    f"{r['mdd']*100:.1f}% | {r['sharpe']:.2f} | {r['mean_budget']:.2f} |"
                )
            lines.append("")
    (RESEARCH / "scheme_a_re_risk_backtest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("VERDICT", recommend, ok, notes)
    for w, blk in results.items():
        print(w, "vsD", blk.get("delta_vs_defense"), "vsS", blk.get("delta_vs_sticky"))
        for r in blk["assets"]["QQQ"]:
            if r["scheme"] in ("DEFENSE_ONLY", "SCHEME_A", "BUY_HOLD", "STICKY_CUT"):
                print(f"  QQQ {r['scheme']:14s} tot={r['total']*100:7.1f}% sh={r['sharpe']:5.2f} mdd={r['mdd']*100:5.1f}% b={r['mean_budget']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
