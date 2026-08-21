#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PPO overlay backtest: BASE vs PPO_flag vs PPO_soft on QQQ/SPY.

BASE = D denom ceiling + exit hyst + frozen tech C-tier + rates shadow (no rates min)
PPO_flag = BASE budgets (identical) + path labels for signal quality
PPO_soft = BASE + DETERIOR/BAD_EASE soft caps (0.55/0.45)
CTRL_E = rates hard on kernel
CTRL_G = min(kernel, tech, denom, rates)

Windows:
  - 2022 p2t / full (via 2021-22 caches + kernel loop)
  - 2023-08..latest from rates_cap_3yr csv if present, else rebuild

Outputs: docs/research/ppo_backtest_qqq_spy.{md,json}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.denom_ceiling import classify_denom_tier, series_bind  # noqa: E402
from core.denominator_state import DenominatorParams, compute_denominator_states  # noqa: E402
from core.positioning_path import (  # noqa: E402
    PATH_DETERIOR,
    PPOParams,
    apply_path_hysteresis,
    classify_path,
    detect_swing_structure,
    path_soft_cap,
)
from core.rates_stress import RatesStressParams, compute_rates_stress_series  # noqa: E402

DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"
RESEARCH.mkdir(parents=True, exist_ok=True)
FRICTION = 0.0005

TECH_TIERS = [(-0.13, 0.35), (-0.10, 0.50), (-0.07, 0.65)]
DENOM_BLOCK = {
    "crisis": {"ceiling": 0.10, "keywords": []},
    "tight": {"ceiling": 0.35, "keywords": []},
    "unconfirmed": {"ceiling": 0.55, "keywords": []},
    "risk_on": {"ceiling": 0.80, "keywords": []},
    "default": 0.55,
}
HYST = {"confirm_enter": 1, "confirm_exit": 3}


def _read_series(path: Path, value_candidates: List[str]) -> pd.Series:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    dcol = cols.get("observation_date") or cols.get("date") or df.columns[0]
    vcol = None
    for c in value_candidates:
        if c.lower() in cols:
            vcol = cols[c.lower()]
            break
    if vcol is None:
        vcol = df.columns[1]
    s = df[[dcol, vcol]].copy()
    s[dcol] = pd.to_datetime(s[dcol])
    if getattr(s[dcol].dt, "tz", None) is not None:
        s[dcol] = s[dcol].dt.tz_localize(None)
    ser = pd.Series(pd.to_numeric(s[vcol], errors="coerce").values, index=pd.DatetimeIndex(s[dcol]))
    return ser.sort_index()[~ser.index.duplicated(keep="last")].dropna()


def _trailing_dd(px: pd.Series, win: int = 20) -> pd.Series:
    peak = px.rolling(win, min_periods=max(5, win // 2)).max()
    return (px / peak - 1.0).clip(upper=0.0)


def tech_cap(dd: pd.Series) -> pd.Series:
    cap = pd.Series(1.0, index=dd.index)
    for thr, c in reversed(TECH_TIERS):
        cap = cap.where(dd > thr, c)
    return cap.fillna(1.0)


def max_dd(nav: pd.Series) -> float:
    peak = nav.cummax()
    dd = (peak - nav) / peak.replace(0, np.nan)
    return float(dd.max()) if len(dd) else float("nan")


def ann_stats(rets: pd.Series) -> Dict[str, float]:
    r = rets.dropna()
    if r.empty:
        return {"total": 0.0, "ann": 0.0, "vol": 0.0, "sharpe": 0.0, "mdd": 0.0, "n": 0}
    total = float((1.0 + r).prod() - 1.0)
    n = len(r)
    years = max(n / 252.0, 1e-9)
    ann = float((1.0 + total) ** (1.0 / years) - 1.0)
    vol = float(r.std() * np.sqrt(252)) if n > 2 else float("nan")
    sharpe = float(ann / vol) if vol and vol > 1e-12 else float("nan")
    mdd = max_dd((1.0 + r).cumprod())
    return {
        "total": round(total, 4),
        "ann": round(ann, 4),
        "vol": round(vol, 4),
        "sharpe": round(sharpe, 3),
        "mdd": round(mdd, 4),
        "n": int(n),
    }


def nav_from_budget(budget: pd.Series, ret: pd.Series, friction: float = FRICTION):
    b = budget.reindex(ret.index).ffill().fillna(1.0)
    b_lag = b.shift(1).fillna(b.iloc[0])
    toggle = (b_lag.diff().abs() > 1e-9).astype(float)
    sr = b_lag * ret - toggle * friction
    nav = (1.0 + sr.fillna(0.0)).cumprod()
    return nav, sr


def build_recent_panel() -> Optional[pd.DataFrame]:
    """Prefer existing 3y rates_cap csv; else None."""
    fp = RESEARCH / "rates_cap_3yr_qqq_spy.csv"
    if not fp.exists():
        return None
    df = pd.read_csv(fp, parse_dates=["date"]).set_index("date").sort_index()
    return df


def kernel_proxy_from_v5(idx: pd.DatetimeIndex) -> pd.Series:
    path = RESEARCH / "pipeline_backtest_daily_v5.csv"
    base = pd.Series(0.80, index=idx, dtype=float)
    if not path.exists():
        return base
    k = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    if "risk_budget" not in k.columns:
        return base
    rb = k["risk_budget"].astype(float)
    common = base.index.intersection(rb.index)
    base.loc[common] = rb.reindex(common)
    return base


def run_window(
    name: str,
    idx: pd.DatetimeIndex,
    qret: pd.Series,
    sret: pd.Series,
    kernel: pd.Series,
    tech_c: pd.Series,
    denom_c: pd.Series,
    rates_c: pd.Series,
    paths: pd.Series,
) -> Dict:
    pparams = PPOParams(mode="soft_cap", confirm_days=2, exit_days=2, deteriorate_confirm_days=3, require_lh_ll_for_deteriorate=True)
    raw_paths = paths.reindex(idx).fillna("MIXED").astype(str).tolist()
    held = apply_path_hysteresis(raw_paths, confirm_days=2, exit_days=2, deteriorate_confirm_days=3)
    held_s = pd.Series(held, index=idx)
    soft = held_s.map(lambda p: path_soft_cap(p, pparams)).astype(float)
    # where None -> 1.0 nonbinding
    soft = soft.fillna(1.0)

    schemes = {
        "BASE": np.minimum(np.minimum(kernel, tech_c), denom_c),
        "PPO_flag": np.minimum(np.minimum(kernel, tech_c), denom_c),  # identical budget
        "PPO_soft": np.minimum(np.minimum(np.minimum(kernel, tech_c), denom_c), soft),
        "CTRL_E": np.minimum(kernel, rates_c),
        "CTRL_G": np.minimum(np.minimum(np.minimum(kernel, tech_c), denom_c), rates_c),
        "A_buy_hold": pd.Series(1.0, index=idx),
    }
    # ensure series
    for k, v in list(schemes.items()):
        schemes[k] = pd.Series(v, index=idx).astype(float)

    out = {
        "window": name,
        "start": str(idx.min().date()),
        "end": str(idx.max().date()),
        "n": len(idx),
        "path_counts": held_s.value_counts().to_dict(),
        "assets": {},
    }

    # signal quality: DETERIOR fwd returns on QQQ
    q = qret.reindex(idx).fillna(0.0)
    det = held_s == PATH_DETERIOR
    def fwd_mean(mask, h):
        # average of future h-day cumulative ret on mask days
        vals = []
        arr = q.values
        m = mask.values
        for i, flag in enumerate(m):
            if not flag:
                continue
            if i + h >= len(arr):
                continue
            vals.append(float((1.0 + pd.Series(arr[i + 1 : i + 1 + h])).prod() - 1.0))
        return float(np.mean(vals)) if vals else None

    non = ~det
    out["signal"] = {
        "deteriorate_days": int(det.sum()),
        "det_fwd5": fwd_mean(det, 5),
        "non_fwd5": fwd_mean(non, 5),
        "det_fwd20": fwd_mean(det, 20),
        "non_fwd20": fwd_mean(non, 20),
    }

    for asset, ret in (("QQQ", qret), ("SPY", sret)):
        rows = []
        r = ret.reindex(idx)
        bh_nav, bh_sr = nav_from_budget(schemes["A_buy_hold"], r)
        bh = ann_stats(bh_sr)
        for sk, bud in schemes.items():
            nav, sr = nav_from_budget(bud, r)
            st = ann_stats(sr)
            rows.append(
                {
                    "scheme": sk,
                    **st,
                    "mean_budget": round(float(bud.mean()), 4),
                    "excess": round(st["total"] - bh["total"], 4),
                    "mdd_improve": round(bh["mdd"] - st["mdd"], 4),
                    "bh_total": bh["total"],
                    "bh_mdd": bh["mdd"],
                }
            )
        # rank bear-like by mdd_improve then excess; else sharpe
        if "2022" in name or "p2t" in name or "drawdown" in name:
            rows.sort(key=lambda x: (x["mdd_improve"], x["excess"], x["sharpe"] or -9), reverse=True)
        else:
            rows.sort(key=lambda x: (x["sharpe"] or -9, x["total"]), reverse=True)
        out["assets"][asset] = rows
    return out


def success_check(results: Dict) -> Dict:
    """Apply pre-registered criteria; recommend production mode."""
    notes = []
    ok_signal = True
    ok_soft_bear = True
    ok_soft_full = True

    # signal across windows that have det days
    for wname, blk in results.items():
        sig = blk.get("signal") or {}
        if not sig.get("deteriorate_days"):
            continue
        d5, n5 = sig.get("det_fwd5"), sig.get("non_fwd5")
        d20, n20 = sig.get("det_fwd20"), sig.get("non_fwd20")
        if d5 is not None and n5 is not None and d5 > n5 + 1e-6:
            ok_signal = False
            notes.append(f"{wname}: det_fwd5 {d5:.4f} > non {n5:.4f}")
        if d20 is not None and n20 is not None and d20 > n20 + 1e-6:
            ok_signal = False
            notes.append(f"{wname}: det_fwd20 {d20:.4f} > non {n20:.4f}")

    def get(w, asset, scheme):
        for r in results[w]["assets"][asset]:
            if r["scheme"] == scheme:
                return r
        return None

    # bear windows
    for w in results:
        if not (("2022" in w and "relief" not in w) or "drawdown" in w or "p2t" in w):
            continue
        if "bull" in w:
            continue
        for asset in ("QQQ", "SPY"):
            b = get(w, asset, "BASE")
            s = get(w, asset, "PPO_soft")
            if not b or not s:
                continue
            if s["mdd_improve"] + 1e-9 < b["mdd_improve"] and s["excess"] + 1e-9 < b["excess"]:
                ok_soft_bear = False
                notes.append(f"{w}/{asset}: soft worse than BASE on both MDD improve and excess")

    # full sample windows
    for w in results:
        if "full" not in w and "2023" not in w:
            continue
        if "p2t" in w or "drawdown" in w:
            continue
        for asset in ("QQQ", "SPY"):
            b = get(w, asset, "BASE")
            s = get(w, asset, "PPO_soft")
            if not b or not s:
                continue
            if (s["sharpe"] or -9) + 1e-9 < (b["sharpe"] or -9) - 0.05:
                ok_soft_full = False
                notes.append(f"{w}/{asset}: soft Sharpe {s['sharpe']} < BASE {b['sharpe']}-0.05")
            if s["total"] + 1e-9 < b["total"] - 0.03:
                ok_soft_full = False
                notes.append(f"{w}/{asset}: soft total {s['total']} < BASE {b['total']}-0.03")

    recommend = "flag_only"
    if ok_signal and ok_soft_bear and ok_soft_full:
        recommend = "soft_cap"
    elif ok_signal:
        recommend = "flag_only"
    else:
        recommend = "flag_only"  # still ship flags; signal weak note

    return {
        "ok_signal": ok_signal,
        "ok_soft_bear": ok_soft_bear,
        "ok_soft_full": ok_soft_full,
        "recommend_mode": recommend,
        "notes": notes,
    }


def main() -> int:
    print("[1] recent panel …", flush=True)
    recent = build_recent_panel()
    results = {}

    if recent is not None:
        idx = recent.index
        qret = recent["qqq_ret"]
        sret = recent["spy_ret"]
        kernel = recent["kernel"] if "kernel" in recent.columns else kernel_proxy_from_v5(idx)
        tech_c = recent["tech_cap"] if "tech_cap" in recent.columns else pd.Series(1.0, index=idx)
        denom_c = recent["denom_cap"] if "denom_cap" in recent.columns else pd.Series(0.55, index=idx)
        rates_c = recent["rates_cap"] if "rates_cap" in recent.columns else pd.Series(1.0, index=idx)

        # rebuild paths from z if possible via rates features
        # Use tips/n30 from separate frame if available
        tips = n30 = hy = None
        try:
            tips = _read_series(DATA / "_tips_daily.csv", ["DFII10"])
            n30 = _read_series(DATA / "_nom30y_daily.csv", ["DGS30"])
            hy = _read_series(DATA / "_hy_daily.csv", ["BAMLH0A0HYM2"])
        except Exception as exc:
            print("  feature load warn", exc)

        qqq_px = (1.0 + qret.fillna(0)).cumprod()
        spy_px = (1.0 + sret.fillna(0)).cumprod()
        q20 = qqq_px.pct_change(20)
        s20 = spy_px.pct_change(20)

        # denom states if we can
        paths = []
        try:
            from scripts.backtest_denominator_state import build_frame

            frame, _ = build_frame()
            ds = compute_denominator_states(frame, DenominatorParams())
            st = ds["state"].reindex(idx).ffill()
            # rates series
            rframe = frame[["nominal_30y", "tips_yield"]].dropna()
            rs = compute_rates_stress_series(rframe, RatesStressParams())
            # bind denom ceilings with hyst
            bound = series_bind(st.astype(str).tolist(), DENOM_BLOCK, HYST)
            denom_c = pd.Series([b["ceiling"] for b in bound], index=st.index).reindex(idx).ffill()
            rates_c = rs["rates_cap"].reindex(idx).ffill().fillna(1.0)
            tips_z = ds.reindex(idx)["tips_z5"] if "tips_z5" in ds.columns else pd.Series(np.nan, index=idx)
            # ds from compute returns tips_z5 in result
            if "tips_z5" not in ds.columns:
                # merge from frame stats — use rs features
                tips_z = rs.reindex(idx)["tips_yield_z5"] if "tips_yield_z5" in rs.columns else pd.Series(0.0, index=idx)
                n30_z = rs.reindex(idx)["nominal_30y_z5"]
            else:
                tips_z = ds["tips_z5"].reindex(idx)
                n30_z = ds["n30_z5"].reindex(idx) if "n30_z5" in ds.columns else rs.reindex(idx)["nominal_30y_z5"]
            hy_z = ds["cs_z5"].reindex(idx) if "cs_z5" in ds.columns else pd.Series(0.0, index=idx)
            # price path for LH/LL
            qqq_px_full = (1.0 + qret.fillna(0.0)).cumprod()
            raw_paths = []
            ppo_p = PPOParams(require_lh_ll_for_deteriorate=True, confirm_days=2, deteriorate_confirm_days=3)
            for dt in idx:
                # trailing closes window
                loc = qqq_px_full.index.get_loc(dt)
                if isinstance(loc, slice):
                    loc = loc.stop - 1
                start = max(0, int(loc) - 79)
                closes = [float(x) for x in qqq_px_full.iloc[start : int(loc) + 1].values]
                feat = {
                    "tips_z5": float(tips_z.loc[dt]) if dt in tips_z.index and pd.notna(tips_z.loc[dt]) else 0.0,
                    "n30_z5": float(n30_z.loc[dt]) if dt in n30_z.index and pd.notna(n30_z.loc[dt]) else 0.0,
                    "qqq_ret_20": float(q20.loc[dt]) if dt in q20.index and pd.notna(q20.loc[dt]) else 0.0,
                    "es_ret_20": float(s20.loc[dt]) if dt in s20.index and pd.notna(s20.loc[dt]) else 0.0,
                    "hy_z5": float(hy_z.loc[dt]) if dt in hy_z.index and pd.notna(hy_z.loc[dt]) else 0.0,
                    "denom_state": str(st.loc[dt]) if dt in st.index else "分裂/未确认",
                    "denom_tier": classify_denom_tier(str(st.loc[dt]) if dt in st.index else ""),
                    "rates_engaged": bool(rs["engaged"].reindex(idx).get(dt, False)) if "engaged" in rs.columns else False,
                    "qqq_closes": closes,
                }
                raw_paths.append(classify_path(feat, ppo_p)["path"])
            paths = pd.Series(raw_paths, index=idx)
        except Exception as exc:
            print("  path rebuild failed, using heuristic", exc)
            # heuristic: bad when q20<=-5% and kernel high
            paths = pd.Series(["MIXED"] * len(idx), index=idx)

        # tech from soxx if column missing
        if "soxx_dd20" in recent.columns:
            tech_c = tech_cap(recent["soxx_dd20"])
        elif (DATA / "_eq_sox.csv").exists():
            soxx = _read_series(DATA / "_eq_sox.csv", ["close"])
            tech_c = tech_cap(_trailing_dd(soxx.reindex(idx).ffill(), 20))

        kernel = pd.Series(kernel, index=idx).astype(float)
        tech_c = pd.Series(tech_c, index=idx).astype(float).fillna(1.0)
        denom_c = pd.Series(denom_c, index=idx).astype(float).fillna(0.55)
        rates_c = pd.Series(rates_c, index=idx).astype(float).fillna(1.0)

        results["recent_full_2023_2026"] = run_window(
            "recent_full_2023_2026", idx, qret, sret, kernel, tech_c, denom_c, rates_c, paths
        )
        # drawdown union from qqq
        px = (1 + qret.fillna(0)).cumprod()
        dd = px / px.cummax() - 1
        # simple: days dd<=-0.08 peak-to-trough approx use all underwater days
        under = dd <= -0.08
        if under.any():
            uidx = idx[under.reindex(idx).fillna(False).values]
            if len(uidx) >= 20:
                results["recent_drawdown_days"] = run_window(
                    "recent_drawdown_days",
                    uidx,
                    qret,
                    sret,
                    kernel,
                    tech_c,
                    denom_c,
                    rates_c,
                    paths,
                )

    # 2022 from prior bear csv if exists
    print("[2] 2022 bear …", flush=True)
    fp22 = RESEARCH / "rates_cap_2022_bear_qqq_spy.csv"
    if fp22.exists():
        d22 = pd.read_csv(fp22, parse_dates=["date"]).set_index("date").sort_index()
        idx = d22.index
        qret, sret = d22["qqq_ret"], d22["spy_ret"]
        kernel = d22["kernel"]
        tech_c = d22["tech_cap"]
        denom_c = d22["denom_cap"]
        rates_c = d22["rates_cap"]
        q20 = (1 + qret.fillna(0)).cumprod().pct_change(20)
        s20 = (1 + sret.fillna(0)).cumprod().pct_change(20)
        # paths: prefer recompute with denom_state col
        raw_paths = []
        qqq_px_full = (1.0 + qret.fillna(0.0)).cumprod()
        ppo_p = PPOParams(require_lh_ll_for_deteriorate=True, confirm_days=2, deteriorate_confirm_days=3)
        for dt in idx:
            st = str(d22.loc[dt, "denom_state"]) if "denom_state" in d22.columns else "分裂/未确认"
            loc = qqq_px_full.index.get_loc(dt)
            if isinstance(loc, slice):
                loc = loc.stop - 1
            start = max(0, int(loc) - 79)
            closes = [float(x) for x in qqq_px_full.iloc[start : int(loc) + 1].values]
            feat = {
                "tips_z5": 0.0,
                "n30_z5": 0.0,
                "qqq_ret_20": float(q20.loc[dt]) if pd.notna(q20.loc[dt]) else 0.0,
                "es_ret_20": float(s20.loc[dt]) if pd.notna(s20.loc[dt]) else 0.0,
                "hy_z5": -1.2 if "信用" in st else 0.0,
                "denom_state": st,
                "denom_tier": classify_denom_tier(st),
                "rates_engaged": bool(d22.loc[dt, "rates_engaged"]) if "rates_engaged" in d22.columns else False,
                "qqq_closes": closes,
            }
            if "regime" in d22.columns and str(d22.loc[dt, "regime"]) == "LIQUIDITY_SQUEEZE":
                feat["tips_z5"] = 0.3
            raw_paths.append(classify_path(feat, ppo_p)["path"])
        paths = pd.Series(raw_paths, index=idx)
        p2t = idx[(idx >= "2022-01-03") & (idx <= "2022-10-12")]
        results["2022_p2t"] = run_window("2022_p2t", p2t, qret, sret, kernel, tech_c, denom_c, rates_c, paths)
        results["2022_full"] = run_window("2022_full", idx, qret, sret, kernel, tech_c, denom_c, rates_c, paths)
    else:
        print("  skip 2022 csv missing")

    verdict = success_check(results)

    summary = {"friction": FRICTION, "verdict": verdict, "windows": results}
    jp = RESEARCH / "ppo_backtest_qqq_spy.json"
    jp.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = []
    lines.append("# PPO Backtest — BASE vs flag vs soft (QQQ/SPY)")
    lines.append("")
    lines.append("Tightened DETERIOR: require LH/LL + ret20<=-5%; deteriorate_confirm_days=3.")
    lines.append("")
    lines.append(f"- Friction: {FRICTION*1e4:.0f}bps toggle; budget lag-1")
    lines.append(
        f"- **Verdict:** signal_ok={verdict['ok_signal']} soft_bear_ok={verdict['ok_soft_bear']} "
        f"soft_full_ok={verdict['ok_soft_full']} → **recommend `{verdict['recommend_mode']}`**"
    )
    if verdict["notes"]:
        lines.append("- Notes:")
        for n in verdict["notes"][:20]:
            lines.append(f"  - {n}")
    lines.append("")

    for wname, blk in results.items():
        lines.append(f"## {wname} ({blk['start']} → {blk['end']}, n={blk['n']})")
        lines.append("")
        lines.append(f"- path_counts: `{blk.get('path_counts')}`")
        sig = blk.get("signal") or {}
        lines.append(
            f"- signal DETERIOR days={sig.get('deteriorate_days')} "
            f"fwd5 det/non={sig.get('det_fwd5')}/{sig.get('non_fwd5')} "
            f"fwd20 det/non={sig.get('det_fwd20')}/{sig.get('non_fwd20')}"
        )
        lines.append("")
        for asset in ("QQQ", "SPY"):
            lines.append(f"### {asset}")
            lines.append("")
            lines.append("| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|")
            for r in blk["assets"][asset]:
                lines.append(
                    f"| {r['scheme']} | {r['total']*100:.1f}% | {r['excess']*100:+.1f}pp | "
                    f"{r['mdd']*100:.1f}% | {r['mdd_improve']*100:+.1f}pp | {r['sharpe']:.2f} | {r['mean_budget']:.2f} |"
                )
            lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append("1. PPO_flag budgets equal BASE by construction — value is gate/advice + signal stats.")
    lines.append("2. PPO_soft only recommended if verdict.recommend_mode == soft_cap.")
    lines.append("3. CTRL_E/G are negative controls; do not adopt if they only win on MDD with large full-sample drag.")
    lines.append("")

    mp = RESEARCH / "ppo_backtest_qqq_spy.md"
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("WROTE", mp)
    print("VERDICT", verdict)
    # print quick tops
    for w in results:
        print("\n==", w, "QQQ")
        for r in results[w]["assets"]["QQQ"][:6]:
            print(
                f"  {r['scheme']:12s} tot={r['total']*100:7.1f}% ex={r['excess']*100:+6.1f} "
                f"mdd={r['mdd']*100:5.1f}% sh={r['sharpe']:5.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
