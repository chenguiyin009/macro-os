# -*- coding: utf-8 -*-
"""
shadow_tech_mom_compare_bt.py — Tech sleeve mom6m vs mom12 (research)
====================================================================
C-like tech: 70% QQQ + 30% Top3 themes by momentum engine.
Engines: mom6m (126d, production-like), mom12 (252d), optional z-blend.

SECTOR_PROXY=off. Does NOT change STRATEGY_BOOKS / evening routines.
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

os.environ["SECTOR_PROXY"] = "off"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import shadow_c_enhancement_bt as sc  # reuse downloads / gate / sim

OUT_DIR = Path("/workspace/output/docs")
OUT_DIR.mkdir(parents=True, exist_ok=True)

THEME_ETF = {
    "半导体": "SMH", "软件": "IGV", "云计算": "SKYY",
    "网络安全": "CIBR", "机器人": "BOTZ", "AI综合": "AIQ",
}
THEME_TICKERS = list(THEME_ETF.values())
CORE = "QQQ"
CASH = "BIL"
MOM6 = 126
MOM12 = 252
COST = 0.001
REPORT_DATE = "2026-09-15"
SAMPLE_START = "2023-01-01"
TOP3_SINGLE_CAP = 0.15
CROWD_REL60_START, CROWD_REL60_SCALE, CROWD_PEN_PP_CAP = 15.0, 1.5, 5.0
S3_SOFT_CROWD_PP = 0.05


def crowd_pen_mom(rel60_pp: float) -> float:
    if rel60_pp == rel60_pp:
        pp = min(CROWD_PEN_PP_CAP, max(0.0, (rel60_pp - CROWD_REL60_START) / CROWD_REL60_SCALE))
        return float(S3_SOFT_CROWD_PP) * (pp / CROWD_PEN_PP_CAP)
    return 0.0


def rel60_vs_qqq(close: pd.DataFrame, day: pd.Timestamp, tic: str) -> float:
    if tic not in close.columns or CORE not in close.columns or day not in close.index:
        return float("nan")
    loc = close.index.get_loc(day)
    if isinstance(loc, slice) or loc < 60:
        return float("nan")
    ratio = close[tic] / close[CORE].replace(0, np.nan)
    a, b = float(ratio.iloc[loc - 60]), float(ratio.iloc[loc])
    if not (np.isfinite(a) and np.isfinite(b) and a > 0):
        return float("nan")
    return (b / a - 1.0) * 100.0


def mom_n(close: pd.DataFrame, day: pd.Timestamp, n: int) -> Dict[str, float]:
    if day not in close.index:
        return {}
    loc = close.index.get_loc(day)
    if isinstance(loc, slice) or loc < n:
        return {}
    px0 = close.iloc[loc - n]
    px1 = close.iloc[loc]
    out = {}
    for tic in THEME_TICKERS:
        if tic not in close.columns:
            continue
        a, b = float(px0.get(tic, np.nan)), float(px1.get(tic, np.nan))
        if np.isfinite(a) and np.isfinite(b) and a > 0:
            out[tic] = b / a - 1.0
    return out


def zscore_map(vals: Dict[str, float]) -> Dict[str, float]:
    if len(vals) < 2:
        return {k: 0.0 for k in vals}
    arr = np.array(list(vals.values()), dtype=float)
    mu, sd = float(np.nanmean(arr)), float(np.nanstd(arr, ddof=0))
    if sd < 1e-12:
        return {k: 0.0 for k in vals}
    return {k: (v - mu) / sd for k, v in vals.items()}


def select_top3(day: pd.Timestamp, close: pd.DataFrame, engine: str) -> List[Tuple[str, float]]:
    """Return [(ticker, rank_score)] Top3. Soft crowd on mom6/mom12; blend uses z then crowd."""
    m6 = mom_n(close, day, MOM6)
    m12 = mom_n(close, day, MOM12)
    if engine == "mom6m":
        base = m6
    elif engine == "mom12":
        base = m12
    elif engine == "blend_z":
        if len(m6) < 3 or len(m12) < 3:
            return []
        z6, z12 = zscore_map(m6), zscore_map(m12)
        keys = set(z6) & set(z12)
        base = {k: 0.5 * z6[k] + 0.5 * z12[k] for k in keys}
    else:
        raise ValueError(engine)
    if len(base) < 3:
        return []
    # bottom 20% ban on raw mom for mom engines; for blend ban on blend score
    thr = np.nanpercentile(list(base.values()), 20)
    cands = []
    for tic, m in base.items():
        if m <= thr:
            continue
        rel = rel60_vs_qqq(close, day, tic)
        rank = float(m) - crowd_pen_mom(rel)
        cands.append((tic, rank))
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands[:3]


def book_c_tech(picks: List[Tuple[str, float]], sat_mult: float) -> Dict[str, float]:
    sm = float(np.clip(sat_mult, 0.0, 1.0))
    core_pct, sat_pct = 0.70, 0.30
    sat_share = sat_pct * sm
    w: Dict[str, float] = {CORE: core_pct}
    # reuse top3_weights but SECTOR_TICKERS check is for sat_w metrics — use local cap logic
    if sat_share > 1e-12 and picks:
        raw = [max(float(s), 0.0) for _, s in picks]
        if sum(raw) <= 1e-12:
            ww = [sat_share / len(picks)] * len(picks)
        else:
            s = sum(raw)
            ww = [sat_share * (x / s) for x in raw]
        cap = TOP3_SINGLE_CAP
        n = len(ww)
        for _ in range(5):
            over = [i for i, wi in enumerate(ww) if wi > cap + 1e-12]
            if not over:
                break
            excess = sum(ww[i] - cap for i in over)
            for i in over:
                ww[i] = cap
            free = [i for i in range(n) if i not in over]
            if not free or excess <= 1e-12:
                break
            base = sum(ww[i] for i in free) or 1.0
            for i in free:
                ww[i] += excess * (ww[i] / base)
        tw = sum(ww) or 1.0
        ww = [wi / tw * sat_share for wi in ww]
        for i, (tic, _) in enumerate(picks):
            w[tic] = ww[i]
    used = sum(w.values())
    bil = max(0.0, 1.0 - used)
    if bil > 1e-9:
        w[CASH] = bil
    s = sum(w.values())
    if abs(s - 1.0) > 1e-8 and s > 0:
        w = {k: v / s for k, v in w.items()}
    return w


def simulate_tech(close: pd.DataFrame, targets, cost: float):
    # monkey-patch sat ticker set for metrics via local simulate copy
    idx = close.index
    rets = close.pct_change()
    ordered = sorted(targets.keys())
    w_signal = []
    cur = {CASH: 1.0}
    si = 0
    for d in idx:
        while si < len(ordered) and ordered[si] <= d:
            cur = dict(targets[ordered[si]])
            si += 1
        w_signal.append(dict(cur))
    w_earn = [{CASH: 1.0}] + w_signal[:-1]
    nav = pd.Series(1.0, index=idx, dtype=float)
    turns = pd.Series(0.0, index=idx, dtype=float)
    sat_w = pd.Series(0.0, index=idx, dtype=float)
    prev_w = dict(w_earn[0])
    for i, d in enumerate(idx):
        w = dict(w_earn[i])
        if i == 0:
            nav.iloc[i] = 1.0
            prev_w = dict(w)
            sat_w.iloc[i] = sum(v for k, v in w.items() if k in THEME_TICKERS)
            continue
        keys = set(prev_w) | set(w)
        turn = 0.5 * sum(abs(w.get(k, 0.0) - prev_w.get(k, 0.0)) for k in keys)
        turns.iloc[i] = turn
        r = 0.0
        for t, wt in w.items():
            if abs(wt) < 1e-12:
                continue
            if t not in rets.columns or not np.isfinite(rets.at[d, t]):
                continue
            r += wt * float(rets.at[d, t])
        r -= cost * turn
        nav.iloc[i] = nav.iloc[i - 1] * (1.0 + r)
        sat_w.iloc[i] = sum(v for k, v in w.items() if k in THEME_TICKERS)
        prev_w = dict(w)
    return nav, turns, sat_w


def overlap_engines(rebal, close, a="mom6m", b="mom12"):
    overlaps = []
    for d in rebal:
        pa = {t for t, _ in select_top3(d, close, a)}
        pb = {t for t, _ in select_top3(d, close, b)}
        if len(pa) == 3 and len(pb) == 3:
            overlaps.append(len(pa & pb) / 3.0)
    return {
        "n_compared": len(overlaps),
        "mean_overlap_frac": float(np.mean(overlaps)) if overlaps else float("nan"),
        "pct_identical": float(np.mean([o >= 1.0 - 1e-9 for o in overlaps])) if overlaps else float("nan"),
    }


def go_nogo(metrics_by_eng: dict, ov: dict) -> dict:
    m6 = metrics_by_eng["mom6m"]
    m12 = metrics_by_eng["mom12"]
    blend = metrics_by_eng.get("blend_z")
    # Switch default to mom12 only if clear excess OR similar return with less turnover
    excess = m12["cagr"] > m6["cagr"] + 0.005 or m12["sharpe_rf0"] > m6["sharpe_rf0"] + 0.05
    turn_win = (abs(m12["cagr"] - m6["cagr"]) < 0.01) and (
        m12["ann_turnover_one_way"] < m6["ann_turnover_one_way"] * 0.9
    )
    mdd_ok = abs(m12["max_dd"]) <= abs(m6["max_dd"]) * 1.15 + 1e-12
    overlap_high = (ov.get("mean_overlap_frac") or 0) > 0.80
    switch = bool((excess or turn_win) and mdd_ok and not (overlap_high and not excess and not turn_win))
    flag_only = bool((excess or turn_win) and not switch) or bool(
        (m12["cagr"] > m6["cagr"] or m12["sharpe_rf0"] > m6["sharpe_rf0"]) and not switch
    )
    # Prefer: if switch criteria fail but mom12 not worse → flag_only research toggle
    if not switch and (m12["cagr"] >= m6["cagr"] - 0.005 or m12["ann_turnover_one_way"] < m6["ann_turnover_one_way"]):
        flag_only = True
    rec = "switch_default_to_mom12" if switch else ("flag_only_keep_mom6m_default" if flag_only else "no_change_keep_mom6m")
    reason = []
    if switch:
        reason.append("mom12 clears excess/turnover bar with MDD within 1.15×")
    else:
        if not excess and not turn_win:
            reason.append("no clear CAGR/Sharpe excess or turnover win")
        if not mdd_ok:
            reason.append("MaxDD worse than 1.15× mom6m")
        if overlap_high and not excess:
            reason.append("high Top3 overlap without edge")
        if flag_only:
            reason.append("retain as optional flag / shadow only")
    out = {
        "recommendation": rec,
        "switch_tech_default": bool(switch),
        "flag_only": bool(flag_only and not switch),
        "mom12_vs_mom6m": {
            "cagr_delta": m12["cagr"] - m6["cagr"],
            "sharpe_delta": m12["sharpe_rf0"] - m6["sharpe_rf0"],
            "mdd_delta": m12["max_dd"] - m6["max_dd"],
            "turnover_ratio": (
                m12["ann_turnover_one_way"] / m6["ann_turnover_one_way"]
                if m6["ann_turnover_one_way"] > 1e-12
                else None
            ),
            "mean_top3_overlap": ov.get("mean_overlap_frac"),
        },
        "reason": "; ".join(reason) if reason else "GO switch",
    }
    if blend:
        out["blend_vs_mom6m_cagr_delta"] = blend["cagr"] - m6["cagr"]
    return out


def fmt_pct(x: float) -> str:
    return f"{100 * x:+.2f}%"


def fmt_num(x: float) -> str:
    return f"{x:.3f}"


def main():
    print("[tech_mom] SECTOR_PROXY=", os.environ.get("SECTOR_PROXY"))
    params = sc._load_gate_params_file()
    end = datetime.utcnow().strftime("%Y-%m-%d")
    warm_start = "2021-01-01"
    tickers = sorted(set(THEME_TICKERS + [CORE, CASH, "SOXX", "QQQ"]))
    print("[tech_mom] downloading", tickers)
    close, _vol = sc.download_close_vol(tickers, warm_start, end)
    if CASH not in close.columns or close[CASH].isna().all():
        raise RuntimeError("BIL missing")
    vix = sc.download_vix(warm_start, end)
    idx = close.index.intersection(vix.index)
    close = close.reindex(idx).ffill()
    vix = vix.reindex(idx).ffill()

    gate, gate_caveat, gate_meta = sc.resolve_gate(
        idx, close["QQQ"], close["SOXX"] if "SOXX" in close.columns else close["QQQ"], vix, params, warm_start, end
    )
    print("[tech_mom] gate mode=", gate_meta.get("mode"))

    sample_start_ts = pd.Timestamp(SAMPLE_START)
    first_ok = idx[MOM12] if len(idx) > MOM12 else idx[0]
    sample_start_ts = max(sample_start_ts, first_ok)
    sample_idx = idx[(idx >= sample_start_ts) & (idx <= pd.Timestamp(end))]
    if len(sample_idx) < 100:
        raise RuntimeError(f"sample too short: {len(sample_idx)}")

    rebal = [d for d in sc.rebalance_dates(sample_idx, "W") if d in sample_idx and d in gate.index]
    engines = ["mom6m", "mom12", "blend_z"]
    bil_rets = close[CASH].pct_change().fillna(0.0)
    labels = gate["label"].reindex(sample_idx).ffill()

    results = {}
    for eng in engines:
        targets = {}
        for d in rebal:
            sm = float(gate.at[d, "sat_mult"])
            picks = select_top3(d, close, eng)
            targets[d] = book_c_tech(picks, sm)
        cl = close.reindex(sample_idx).ffill()
        nav, turns, sat_w = simulate_tech(cl, targets, COST)
        m = sc.metrics(nav, bil_rets, turns, sat_w)
        # honest equity
        ordered = sorted(targets.keys())
        cur = {CASH: 1.0}
        si = 0
        w_sig = []
        for d in sample_idx:
            while si < len(ordered) and ordered[si] <= d:
                cur = dict(targets[ordered[si]])
                si += 1
            w_sig.append(dict(cur))
        w_earn = [{CASH: 1.0}] + w_sig[:-1]
        m["avg_equity_weight"] = float(np.mean([sum(v for k, v in w.items() if k != CASH) for w in w_earn]))
        m["avg_sat_weight"] = float(np.mean([sum(v for k, v in w.items() if k in THEME_TICKERS) for w in w_earn]))
        m["regime_contrib"] = sc.regime_contrib(nav, labels)
        m["final_nav"] = float(nav.iloc[-1])
        results[eng] = {"metrics": m, "nav_end": float(nav.iloc[-1])}

    # excess vs mom6m
    for eng in engines:
        if eng == "mom6m":
            results[eng]["metrics"]["excess_vs_mom6m_total"] = 0.0
        else:
            results[eng]["metrics"]["excess_vs_mom6m_total"] = float(
                results[eng]["nav_end"] / results["mom6m"]["nav_end"] - 1.0
            )

    ov = overlap_engines(rebal, close, "mom6m", "mom12")
    ov_b = overlap_engines(rebal, close, "mom6m", "blend_z")
    gn = go_nogo({e: results[e]["metrics"] for e in engines}, ov)

    # QQQ BH
    spy = close[CORE].reindex(sample_idx).dropna()
    qqq_nav = spy / spy.iloc[0]
    qqq_bh = sc.metrics(qqq_nav, bil_rets, pd.Series(0.0, index=qqq_nav.index), pd.Series(0.0, index=qqq_nav.index))

    g_sample = gate.reindex(sample_idx)
    gate_mix = {
        "off_pct": float((g_sample["label"] == "OFF").mean()),
        "warn_pct": float((g_sample["label"] == "WARN").mean()),
        "bear_pct": float((g_sample["label"] == "BEAR").mean()),
        "sat_mult_mean": float(g_sample["sat_mult"].mean()),
    }

    payload = {
        "schema": "tech_mom_compare_v1",
        "report_date": REPORT_DATE,
        "sample_start": str(sample_idx[0].date()),
        "sample_end": str(sample_idx[-1].date()),
        "sleeve": "tech",
        "skeleton": "70% QQQ + 30%*sat_mult Top3 theme ETFs + BIL residual",
        "universe": THEME_ETF,
        "engines": engines,
        "cost_one_way": COST,
        "rebalance": "weekly",
        "n_rebalance": len(rebal),
        "gate_mode": gate_meta.get("mode"),
        "gate_meta": gate_meta,
        "gate_caveat": gate_caveat,
        "gate_mix": gate_mix,
        "overlap_mom6m_vs_mom12": ov,
        "overlap_mom6m_vs_blend": ov_b,
        "books": {e: {"metrics": results[e]["metrics"]} for e in engines},
        "qqq_bh": qqq_bh,
        "go_nogo": gn,
        "production_STRATEGY_BOOKS_unchanged": True,
        "production_tech_default_still_mom6m": True,
    }

    # markdown
    lines = []
    lines.append(f"# Tech sleeve mom6m vs mom12 shadow（{REPORT_DATE}）\n")
    lines.append("## 中文摘要\n")
    lines.append(f"- **样本：** {payload['sample_start']} → {payload['sample_end']}（科技袖，周频）")
    lines.append(f"- **结构：** 70% QQQ + 30%×sat_mult Top3（THEME_ETF 六主题）+ BIL；成本单边 {COST}")
    lines.append(f"- **闸：** mode=`{gate_meta.get('mode')}` — {gate_caveat}")
    lines.append(f"- **推荐：** `{gn['recommendation']}` — {gn['reason']}")
    lines.append(
        f"- **mom6m vs mom12 Top3 重叠：** mean={ov.get('mean_overlap_frac'):.1%}（n={ov.get('n_compared')}）\n"
    )
    lines.append("## English detail\n")
    lines.append("### Main metrics (weekly, cost=0.001)\n")
    lines.append(
        "| Engine | CAGR | Vol | Sharpe(rf0) | Sharpe(vs BIL) | MaxDD | Calmar | "
        "AnnTurn | AvgSat | AvgEq | Excess vs mom6m |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for eng in engines:
        m = results[eng]["metrics"]
        lines.append(
            f"| {eng} | {fmt_pct(m['cagr'])} | {fmt_pct(m['vol'])} | {fmt_num(m['sharpe_rf0'])} | "
            f"{fmt_num(m['sharpe_vs_bil'])} | {fmt_pct(m['max_dd'])} | {fmt_num(m['calmar'])} | "
            f"{fmt_num(m['ann_turnover_one_way'])} | {fmt_pct(m['avg_sat_weight'])} | "
            f"{fmt_pct(m['avg_equity_weight'])} | {fmt_pct(m.get('excess_vs_mom6m_total', 0))} |"
        )
    qb = qqq_bh
    lines.append(
        f"| QQQ BH | {fmt_pct(qb['cagr'])} | {fmt_pct(qb['vol'])} | {fmt_num(qb['sharpe_rf0'])} | "
        f"{fmt_num(qb['sharpe_vs_bil'])} | {fmt_pct(qb['max_dd'])} | {fmt_num(qb['calmar'])} | "
        f"0 | 0 | 100% | — |"
    )
    lines.append("")
    lines.append("### Regime contribution (sum daily returns)\n")
    lines.append("| Engine | OFF n / sum | WARN n / sum | BEAR n / sum |")
    lines.append("|---|---|---|---|")
    for eng in engines:
        rc = results[eng]["metrics"]["regime_contrib"]
        lines.append(
            f"| {eng} | {rc['OFF']['n_days']} / {fmt_pct(rc['OFF']['sum_return'])} | "
            f"{rc['WARN']['n_days']} / {fmt_pct(rc['WARN']['sum_return'])} | "
            f"{rc['BEAR']['n_days']} / {fmt_pct(rc['BEAR']['sum_return'])} |"
        )
    lines.append("")
    lines.append("### Recommendation\n")
    lines.append(f"- **Decision:** `{gn['recommendation']}`")
    lines.append(f"- **Switch tech default to mom12?** {'YES' if gn['switch_tech_default'] else 'NO'}")
    lines.append(f"- **Flag-only / research toggle?** {'YES' if gn['flag_only'] else 'NO'}")
    lines.append(f"- **Reason:** {gn['reason']}")
    lines.append(f"- **Deltas:** {json.dumps(gn['mom12_vs_mom6m'], ensure_ascii=False)}")
    lines.append("")
    lines.append("### Artifacts\n")
    lines.append("- Script: `/workspace/tradingview-desk/shadow_tech_mom_compare_bt.py`")
    lines.append(f"- Report: `/workspace/output/docs/tech_mom_compare_{REPORT_DATE}.md`")
    lines.append(f"- JSON: `/workspace/output/docs/tech_mom_compare_{REPORT_DATE}.json`")
    lines.append("")
    lines.append("> Production tech default remains mom6m unless parent explicitly flips. STRATEGY_BOOKS untouched.\n")

    md_path = OUT_DIR / f"tech_mom_compare_{REPORT_DATE}.md"
    js_path = OUT_DIR / f"tech_mom_compare_{REPORT_DATE}.json"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    js_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[tech_mom] wrote", md_path)
    print("[tech_mom] wrote", js_path)
    print("[tech_mom] go_nogo", json.dumps(gn, ensure_ascii=False))
    for eng in engines:
        m = results[eng]["metrics"]
        print(
            f"  {eng}: CAGR={m['cagr']:.4f} Sh={m['sharpe_rf0']:.3f} MDD={m['max_dd']:.4f} "
            f"turn={m['ann_turnover_one_way']:.3f} xs6={m.get('excess_vs_mom6m_total', 0):.4f}"
        )


if __name__ == "__main__":
    main()
