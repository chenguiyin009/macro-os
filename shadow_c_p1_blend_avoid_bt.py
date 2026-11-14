# -*- coding: utf-8 -*-
"""P1 batch: C vs C_mom12 vs C_blend vs C_mom12_aw (SMA200). Spy sleeve only."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

os.environ.setdefault("SECTOR_PROXY", "off")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import shadow_c_enhancement_bt as sc

OUT = Path("/workspace/output/docs")
COST = 0.001
WARM = "2021-01-01"
END = "2026-09-15"
PRE_2026 = "2025-10-01"
SMA_N = 200
BOOKS = ("C", "C_mom12", "C_blend", "C_mom12_aw")


def _z(vals: Dict[str, float]) -> Dict[str, float]:
    if len(vals) < 2:
        return {k: 0.0 for k in vals}
    arr = np.array(list(vals.values()), dtype=float)
    mu, sd = float(arr.mean()), float(arr.std(ddof=0))
    if sd < 1e-12:
        return {k: 0.0 for k in vals}
    return {k: (v - mu) / sd for k, v in vals.items()}


def _mom12_map(day: pd.Timestamp, close: pd.DataFrame) -> Dict[str, float]:
    if day not in close.index:
        return {}
    loc = close.index.get_loc(day)
    if isinstance(loc, slice) or loc < sc.MOM12:
        return {}
    px0, px1 = close.iloc[loc - sc.MOM12], close.iloc[loc]
    moms = {}
    for tic in sc.SECTOR_TICKERS:
        if tic not in close.columns:
            continue
        a, b = float(px0.get(tic, np.nan)), float(px1.get(tic, np.nan))
        if np.isfinite(a) and np.isfinite(b) and a > 0:
            moms[tic] = b / a - 1.0
    return moms


def _above_sma(day: pd.Timestamp, close: pd.DataFrame, tic: str, n: int = SMA_N) -> bool:
    if tic not in close.columns or day not in close.index:
        return False
    loc = close.index.get_loc(day)
    if isinstance(loc, slice) or loc + 1 < n:
        return False
    window = close[tic].iloc[loc + 1 - n : loc + 1]
    if window.isna().any():
        return False
    px = float(window.iloc[-1])
    sma = float(window.mean())
    return np.isfinite(px) and np.isfinite(sma) and px > sma


def select_top3_blend(day: pd.Timestamp, close: pd.DataFrame, panel: dict) -> List[Tuple[str, float]]:
    moms = _mom12_map(day, close)
    if len(moms) < 3:
        return []
    thr = float(np.nanpercentile(list(moms.values()), 20))
    sc_row = panel["score"].loc[day] if day in panel["score"].index else None
    st = panel["state"].loc[day] if day in panel["state"].index else None
    r60 = panel["rel60"].loc[day] if day in panel["rel60"].index else None
    ov = panel["over_ext"].loc[day] if day in panel["over_ext"].index else None

    pine_adj: Dict[str, float] = {}
    mom_adj: Dict[str, float] = {}
    for tic in sc.SECTOR_TICKERS:
        if sc_row is None or tic not in sc_row.index:
            continue
        score = float(sc_row.get(tic, np.nan))
        if not np.isfinite(score):
            continue
        state = int(st.get(tic, 0) or 0) if st is not None else 0
        if state == -1 or score < sc.SCORE_BAN:
            continue
        if tic not in moms or moms[tic] <= thr:
            continue
        rel = float(r60.get(tic, np.nan)) if r60 is not None else np.nan
        over = bool(ov.get(tic, 0)) if ov is not None else False
        pine_adj[tic] = score - sc.crowd_pen_pine(rel, over)
        mom_adj[tic] = moms[tic] - sc.crowd_pen_mom(rel, over)

    if len(pine_adj) < 3:
        return []
    zp, zm = _z(pine_adj), _z(mom_adj)
    blend = {t: 0.5 * zp[t] + 0.5 * zm[t] for t in pine_adj}
    ranked = sorted(blend.items(), key=lambda x: x[1], reverse=True)
    return ranked[:3]


def select_top3_mom12_aw(day: pd.Timestamp, close: pd.DataFrame, panel: dict) -> List[Tuple[str, float]]:
    """mom12 + bottom20% ban + soft crowd + SMA200 filter."""
    moms = _mom12_map(day, close)
    if len(moms) < 3:
        return []
    thr = float(np.nanpercentile(list(moms.values()), 20))
    r60 = panel["rel60"].loc[day] if day in panel["rel60"].index else None
    ov = panel["over_ext"].loc[day] if day in panel["over_ext"].index else None
    st = panel["state"].loc[day] if day in panel["state"].index else None
    cands = []
    for tic, m in moms.items():
        if m <= thr:
            continue
        if not _above_sma(day, close, tic):
            continue
        state = int(st.get(tic, 0) or 0) if st is not None else 0
        if state == -1:
            continue
        rel = float(r60.get(tic, np.nan)) if r60 is not None else np.nan
        over = bool(ov.get(tic, 0)) if ov is not None else False
        cands.append((tic, m - sc.crowd_pen_mom(rel, over)))
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands[:3]


def pick_engine(engine: str, day, close, panel):
    if engine == "pine":
        return sc.select_top3_pine(day, panel)
    if engine == "mom12":
        return sc.select_top3_mom12(day, close, panel)
    if engine == "blend":
        return select_top3_blend(day, close, panel)
    if engine == "mom12_aw":
        return select_top3_mom12_aw(day, close, panel)
    raise ValueError(engine)


def build_targets(engine: str, rebal, gate, close, panel):
    targets = {}
    for d in rebal:
        if d not in gate.index or d not in close.index:
            continue
        sm = float(gate.at[d, "sat_mult"])
        lab = str(gate.at[d, "label"])
        picks = pick_engine(engine, d, close, panel)
        # all books use C skeleton
        targets[d] = sc.book_weights("C", sc.CORE, picks, sm, lab)
    return targets


def overlap(rebal, close, panel, eng_a, eng_b) -> dict:
    overs = []
    for d in rebal:
        a = {t for t, _ in pick_engine(eng_a, d, close, panel)}
        b = {t for t, _ in pick_engine(eng_b, d, close, panel)}
        if len(a) == 3 and len(b) == 3:
            overs.append(len(a & b) / 3.0)
    return {
        "n": len(overs),
        "mean": float(np.mean(overs)) if overs else float("nan"),
        "pct_ge_2": float(np.mean([o >= 2 / 3 for o in overs])) if overs else float("nan"),
    }


def month_stats(nav: pd.Series, year=2026, months=range(1, 10)):
    rows = {}
    for m in months:
        sub = nav[(nav.index.year == year) & (nav.index.month == m)]
        if len(sub) < 2:
            rows[m] = None
            continue
        prev = nav[nav.index < sub.index[0]]
        start = float(prev.iloc[-1]) if len(prev) else float(sub.iloc[0])
        endv = float(sub.iloc[-1])
        rows[m] = {"ret": endv / start - 1.0, "n_days": int(len(sub))}
    return rows


def ytd(nav, year=2026):
    sub = nav[(nav.index.year == year) & (nav.index.month <= 9)]
    if len(sub) < 2:
        return None
    prev = nav[nav.index < sub.index[0]]
    start = float(prev.iloc[-1]) if len(prev) else float(sub.iloc[0])
    return float(sub.iloc[-1] / start - 1.0)


def go_nogo(metrics: dict) -> dict:
    c, m12, bl, aw = metrics["C"], metrics["C_mom12"], metrics["C_blend"], metrics["C_mom12_aw"]
    m12_excess = m12["final_nav"] / c["final_nav"] - 1.0
    bl_excess = bl["final_nav"] / c["final_nav"] - 1.0
    aw_vs_m12 = aw["final_nav"] / m12["final_nav"] - 1.0
    # blend: positive vs C; keep >=70% of mom12 excess OR (sharpe>=mom12 and mdd ok)
    keep70 = (m12_excess > 0 and bl_excess >= 0.70 * m12_excess) or (
        m12_excess <= 0 and bl_excess > 0
    )
    sharpe_ok = bl["sharpe_rf0"] + 1e-12 >= m12["sharpe_rf0"]
    mdd_ok = bl["max_dd"] >= c["max_dd"] * 1.2  # less negative? max_dd is negative; "not worse than 1.2x"
    # "MaxDD 不差于 C×1.2" means |mdd_bl| <= 1.2 * |mdd_c|
    mdd_ok = abs(bl["max_dd"]) <= 1.2 * abs(c["max_dd"]) + 1e-12
    blend_go = (bl_excess > 0) and (keep70 or (sharpe_ok and mdd_ok))

    # aw: better than mom12 total OR mdd improve >=1pp with excess loss <1pp; turn <= 1.5x
    mdd_improve = (aw["max_dd"] - m12["max_dd"]) >= 0.01  # e.g. -0.16 vs -0.17 → +0.01
    excess_loss_ok = aw_vs_m12 > -0.01
    turn_ok = aw["ann_turnover_one_way"] <= 1.5 * m12["ann_turnover_one_way"] + 1e-9
    aw_go = turn_ok and ((aw_vs_m12 > 0) or (mdd_improve and excess_loss_ok))

    return {
        "C_blend": {
            "go": bool(blend_go),
            "excess_vs_C": float(bl_excess),
            "mom12_excess_vs_C": float(m12_excess),
            "kept_frac_of_mom12_excess": float(bl_excess / m12_excess) if abs(m12_excess) > 1e-9 else None,
            "sharpe_ge_mom12": bool(sharpe_ok),
            "mdd_le_1_2x_C": bool(mdd_ok),
            "reason": "GO" if blend_go else "NO-GO: fail excess/keep70/sharpe-mdd rule",
        },
        "C_mom12_aw": {
            "go": bool(aw_go),
            "excess_vs_mom12": float(aw_vs_m12),
            "mdd_improve_ge_1pp": bool(mdd_improve),
            "turn_ok": bool(turn_ok),
            "reason": "GO" if aw_go else "NO-GO: no edge vs mom12 under locked criteria",
        },
    }


def main():
    print("[p1] start", flush=True)
    params = sc._load_gate_params_file()
    end = END
    tickers = sorted(set(sc.SECTOR_TICKERS + [sc.CORE, sc.CASH, "QQQ", "SOXX"]))
    close, volume = sc.download_close_vol(tickers, WARM, end)
    vix = sc.download_vix(WARM, end)
    idx = close.index.intersection(vix.index)
    close = close.reindex(idx).ffill()
    volume = volume.reindex(idx).ffill()
    vix = vix.reindex(idx).ffill()
    gate, caveat, gmeta = sc.resolve_gate(idx, close["QQQ"], close["SOXX"], vix, params, WARM, end)
    print("[p1] gate", gmeta.get("mode"), flush=True)
    panel = sc.pine_panel(close, volume, close[sc.CORE])
    for k in panel:
        panel[k] = panel[k].reindex(idx)

    sample_start = max(pd.Timestamp(sc.SAMPLE_START), idx[sc.MOM12] if len(idx) > sc.MOM12 else idx[0])
    # need SMA200 too
    sample_start = max(sample_start, idx[SMA_N] if len(idx) > SMA_N else idx[0])
    sample_idx = idx[(idx >= sample_start) & (idx <= pd.Timestamp(end))]
    rebal = [d for d in sc.rebalance_dates(sample_idx, "W") if d in sample_idx and d in gate.index and d in panel["score"].index]
    print(f"[p1] sample {sample_idx[0].date()}→{sample_idx[-1].date()} rebal={len(rebal)}", flush=True)

    engines = {
        "C": "pine",
        "C_mom12": "mom12",
        "C_blend": "blend",
        "C_mom12_aw": "mom12_aw",
    }
    bil_rets = close[sc.CASH].pct_change().fillna(0.0)
    labels = gate["label"].reindex(sample_idx).ffill()
    results = {}
    navs = {}
    for bname, eng in engines.items():
        targets = build_targets(eng, rebal, gate, close, panel)
        nav, turns, sat_w = sc.simulate(close.reindex(sample_idx).ffill(), targets, COST)
        m = sc.metrics(nav, bil_rets, turns, sat_w)
        # honest equity
        ordered = sorted(targets.keys())
        cur = {sc.CASH: 1.0}
        si = 0
        w_sig = []
        for d in sample_idx:
            while si < len(ordered) and ordered[si] <= d:
                cur = dict(targets[ordered[si]])
                si += 1
            w_sig.append(dict(cur))
        w_earn = [{sc.CASH: 1.0}] + w_sig[:-1]
        m["avg_equity_weight"] = float(np.mean([sum(v for k, v in w.items() if k != sc.CASH) for w in w_earn]))
        m["avg_sat_weight"] = float(np.mean([sum(v for k, v in w.items() if k in sc.SECTOR_TICKERS) for w in w_earn]))
        m["regime_contrib"] = sc.regime_contrib(nav, labels)
        m["final_nav"] = float(nav.iloc[-1])
        results[bname] = m
        navs[bname] = nav

    for b in BOOKS:
        if b == "C":
            results[b]["excess_vs_C_total"] = 0.0
        else:
            results[b]["excess_vs_C_total"] = float(navs[b].iloc[-1] / navs["C"].iloc[-1] - 1.0)

    ov_pm = overlap(rebal, close, panel, "pine", "mom12")
    ov_pb = overlap(rebal, close, panel, "pine", "blend")
    ov_ma = overlap(rebal, close, panel, "mom12", "mom12_aw")

    gn = go_nogo(results)

    # 2026 monthly with warm-in
    sim_idx = idx[(idx >= pd.Timestamp(PRE_2026)) & (idx <= pd.Timestamp(end))]
    rebal26 = [d for d in sc.rebalance_dates(sim_idx, "W") if d in sim_idx and d in gate.index]
    navs26 = {}
    for bname, eng in engines.items():
        targets = build_targets(eng, rebal26, gate, close, panel)
        nav, _, _ = sc.simulate(close.reindex(sim_idx).ffill(), targets, COST)
        navs26[bname] = nav
    spy = close[sc.CORE].reindex(sim_idx).ffill()
    navs26["SPY_BH"] = (1.0 + spy.pct_change().fillna(0.0)).cumprod()
    monthly = {b: month_stats(nav) for b, nav in navs26.items()}
    ytds = {b: ytd(nav) for b, nav in navs26.items()}

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "as_of": as_of,
        "gate_mode": gmeta.get("mode"),
        "gate_caveat": caveat,
        "gate_meta": gmeta,
        "sample_start": str(sample_idx[0].date()),
        "sample_end": str(sample_idx[-1].date()),
        "n_rebalance": len(rebal),
        "cost": COST,
        "books": results,
        "overlap": {"pine_mom12": ov_pm, "pine_blend": ov_pb, "mom12_aw": ov_ma},
        "go_nogo": gn,
        "ytd_2026": ytds,
        "monthly_2026": monthly,
    }

    def fmt(x):
        return "—" if x is None else f"{100*x:+.2f}%"

    lines = []
    lines.append(f"# C P1 blend / avoid_weak 回测（{as_of}）— gate={gmeta.get('mode')}\n")
    lines.append("## 中文摘要\n")
    lines.append(f"- 样本：{payload['sample_start']} → {payload['sample_end']}，周频，成本 10bp")
    lines.append(f"- 闸：`{gmeta.get('mode')}`")
    lines.append(
        f"- **C_blend：** {'✅ GO' if gn['C_blend']['go'] else '❌ NO-GO'} — {gn['C_blend']['reason']}"
    )
    lines.append(
        f"- **C_mom12_aw：** {'✅ GO' if gn['C_mom12_aw']['go'] else '❌ NO-GO'} — {gn['C_mom12_aw']['reason']}"
    )
    lines.append(
        f"- 重叠：pine↔mom12 {ov_pm['mean']:.1%} · pine↔blend {ov_pb['mean']:.1%} · mom12↔aw {ov_ma['mean']:.1%}\n"
    )

    lines.append("## 全样本主表（周频 cost=0.001）\n")
    lines.append("| Book | CAGR | Sharpe | MaxDD | AnnTurn | AvgSat | Excess vs C |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for b in BOOKS:
        m = results[b]
        lines.append(
            f"| {b} | {fmt(m['cagr'])} | {m['sharpe_rf0']:.3f} | {fmt(m['max_dd'])} | "
            f"{m['ann_turnover_one_way']:.2f} | {fmt(m['avg_sat_weight'])} | {fmt(m['excess_vs_C_total'])} |"
        )
    lines.append("")

    lines.append("## 2026 分月（YTD 至样本末日，九月未满）\n")
    lines.append("| 月 | C | mom12 | blend | mom12_aw | SPY | blend−C | aw−mom12 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for m in range(1, 10):
        cells = [f"2026-{m:02d}"]
        for b in ["C", "C_mom12", "C_blend", "C_mom12_aw", "SPY_BH"]:
            v = monthly[b][m]
            cells.append(fmt(v["ret"] if v else None))
        c, bl = monthly["C"][m], monthly["C_blend"][m]
        m12, aw = monthly["C_mom12"][m], monthly["C_mom12_aw"][m]
        cells.append(fmt(None if not c or not bl else bl["ret"] - c["ret"]))
        cells.append(fmt(None if not m12 or not aw else aw["ret"] - m12["ret"]))
        lines.append("| " + " | ".join(cells) + " |")
    yrow = ["**YTD**"]
    for b in ["C", "C_mom12", "C_blend", "C_mom12_aw", "SPY_BH"]:
        yrow.append(f"**{fmt(ytds[b])}**")
    yrow.append(f"**{fmt((ytds['C_blend'] or 0)-(ytds['C'] or 0) if ytds['C'] is not None and ytds['C_blend'] is not None else None)}**")
    yrow.append(f"**{fmt((ytds['C_mom12_aw'] or 0)-(ytds['C_mom12'] or 0) if ytds['C_mom12'] is not None and ytds['C_mom12_aw'] is not None else None)}**")
    lines.append("| " + " | ".join(yrow) + " |")
    lines.append("")

    lines.append("## Go/No-Go 细节\n")
    lines.append("```json")
    lines.append(json.dumps(gn, ensure_ascii=False, indent=2))
    lines.append("```\n")
    lines.append("## Gate caveat\n")
    lines.append(caveat)
    lines.append("\n设计见 `C_p1_blend_avoid_design.md`。教学参考，不构成投资建议。生产默认未改。\n")

    md = OUT / "C_p1_blend_avoid_backtest_2026-09-15.md"
    js = OUT / "C_p1_blend_avoid_backtest_2026-09-15.json"

    def conv(o):
        if isinstance(o, dict):
            return {str(k): conv(v) for k, v in o.items()}
        if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
            return None
        return o

    # thin monthly for json
    payload["monthly_2026"] = {
        b: {str(m): (None if v is None else v) for m, v in months.items()}
        for b, months in monthly.items()
    }
    js.write_text(json.dumps(conv(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    md.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", md)
    print("GO blend", gn["C_blend"]["go"], "aw", gn["C_mom12_aw"]["go"])
    print("excess", {b: round(results[b]["excess_vs_C_total"] * 100, 2) for b in BOOKS})
    print("YTD26", {b: (None if ytds[b] is None else round(ytds[b] * 100, 2)) for b in list(BOOKS) + ["SPY_BH"]})


if __name__ == "__main__":
    main()
