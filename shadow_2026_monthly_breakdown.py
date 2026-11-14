# -*- coding: utf-8 -*-
"""Calendar-month returns for 2026-01 .. 2026-09 (spy + tech sleeves)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("SECTOR_PROXY", "off")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import shadow_c_enhancement_bt as sc
import shadow_tech_mom_compare_bt as tech

OUT = Path("/workspace/output/docs")
YEAR = 2026
MONTHS = list(range(1, 10))
COST = 0.001
WARM = "2021-01-01"
END = "2026-09-15"
PRE = "2025-10-01"  # warm weights into Jan


def month_slice_stats(nav: pd.Series, year: int, months):
    rows = {}
    for m in months:
        sub = nav[(nav.index.year == year) & (nav.index.month == m)]
        if len(sub) < 2:
            rows[m] = None
            continue
        prev = nav[nav.index < sub.index[0]]
        start = float(prev.iloc[-1]) if len(prev) else float(sub.iloc[0])
        endv = float(sub.iloc[-1])
        path = pd.concat([pd.Series([start], index=[sub.index[0] - pd.Timedelta(days=1)]), sub])
        rows[m] = {
            "ret": endv / start - 1.0,
            "maxdd": float((path / path.cummax() - 1.0).min()),
            "n_days": int(len(sub)),
            "nav_start": start,
            "nav_end": endv,
        }
    return rows


def ytd_ret(nav: pd.Series, year: int) -> float | None:
    sub = nav[(nav.index.year == year) & (nav.index.month <= 9)]
    if len(sub) < 2:
        return None
    prev = nav[nav.index < sub.index[0]]
    start = float(prev.iloc[-1]) if len(prev) else float(sub.iloc[0])
    return float(sub.iloc[-1] / start - 1.0)


def win_rate(monthly: dict) -> float:
    rets = [v["ret"] for v in monthly.values() if v]
    if not rets:
        return float("nan")
    return float(np.mean([r > 0 for r in rets]))


def main():
    params = sc._load_gate_params_file()
    end = END

    # ---- SPY ----
    tickers = sorted(set(sc.SECTOR_TICKERS + [sc.CORE, sc.CASH, "QQQ", "SOXX"]))
    print("[monthly] spy download", tickers)
    close, volume = sc.download_close_vol(tickers, WARM, end)
    vix = sc.download_vix(WARM, end)
    idx = close.index.intersection(vix.index)
    close = close.reindex(idx).ffill()
    volume = volume.reindex(idx).ffill()
    vix = vix.reindex(idx).ffill()

    gate, gate_caveat, gate_meta = sc.resolve_gate(
        idx, close["QQQ"], close["SOXX"], vix, params, WARM, end
    )
    print("[monthly] gate", gate_meta.get("mode"))
    panel = sc.pine_panel(close, volume, close[sc.CORE])
    for k in panel:
        panel[k] = panel[k].reindex(idx)

    sim_idx = idx[(idx >= pd.Timestamp(PRE)) & (idx <= pd.Timestamp(end))]
    rebal = [d for d in sc.rebalance_dates(sim_idx, "W") if d in sim_idx and d in gate.index]
    books_engines = {
        "C": ("pine", "C"),
        "B": ("pine", "B"),
        "C_dyn40": ("pine", "C_dyn40"),
        "C_mom12": ("mom12", "C_mom12"),
    }
    spy_navs = {}
    for bname, (eng, bkey) in books_engines.items():
        targets = sc.build_target_series(bkey, rebal, gate, close, panel, eng)
        nav, turns, sat_w = sc.simulate(
            close.reindex(sim_idx).ffill(),
            targets,
            COST,
        )
        spy_navs[bname] = nav

    spy_px = close[sc.CORE].reindex(sim_idx).ffill()
    spy_navs["SPY_BH"] = (1.0 + spy_px.pct_change().fillna(0.0)).cumprod() * 1e5

    spy_monthly = {b: month_slice_stats(nav, YEAR, MONTHS) for b, nav in spy_navs.items()}
    spy_ytd = {b: ytd_ret(nav, YEAR) for b, nav in spy_navs.items()}

    # ---- TECH ----
    print("[monthly] tech download")
    t_tickers = sorted(set(tech.THEME_TICKERS + [tech.CORE, tech.CASH, "SOXX", "QQQ"]))
    t_close, _ = sc.download_close_vol(t_tickers, WARM, end)
    t_idx = t_close.index.intersection(vix.index)
    t_close = t_close.reindex(t_idx).ffill()
    t_gate, _, t_meta = sc.resolve_gate(
        t_idx,
        t_close["QQQ"],
        t_close["SOXX"] if "SOXX" in t_close.columns else t_close["QQQ"],
        vix.reindex(t_idx).ffill(),
        params,
        WARM,
        end,
    )
    t_sim = t_idx[(t_idx >= pd.Timestamp(PRE)) & (t_idx <= pd.Timestamp(end))]
    t_rebal = [d for d in sc.rebalance_dates(t_sim, "W") if d in t_sim and d in t_gate.index]
    engines = ["mom6m", "mom12", "blend_z"]
    tech_navs = {}
    for eng in engines:
        targs = {}
        for d in t_rebal:
            sm = float(t_gate.at[d, "sat_mult"])
            picks = tech.select_top3(d, t_close, eng)
            targs[d] = tech.book_c_tech(picks, sm)
        nav, turns, sat_w = tech.simulate_tech(t_close.reindex(t_sim).ffill(), targs, COST)
        tech_navs[eng] = nav
    q = t_close[tech.CORE].reindex(t_sim).ffill()
    tech_navs["QQQ_BH"] = (1.0 + q.pct_change().fillna(0.0)).cumprod() * 1e5

    tech_monthly = {e: month_slice_stats(nav, YEAR, MONTHS) for e, nav in tech_navs.items()}
    tech_ytd = {e: ytd_ret(nav, YEAR) for e, nav in tech_navs.items()}

    # ---- report ----
    month_names = {i: f"2026-{i:02d}" for i in MONTHS}
    payload = {
        "as_of": datetime.utcnow().strftime("%Y-%m-%d"),
        "year": YEAR,
        "months": MONTHS,
        "cost_one_way": COST,
        "rebalance": "W",
        "gate_mode": gate_meta.get("mode"),
        "gate_caveat": gate_caveat,
        "spy": {
            "ytd": spy_ytd,
            "monthly": spy_monthly,
            "win_rate": {b: win_rate(spy_monthly[b]) for b in spy_monthly},
        },
        "tech": {
            "ytd": tech_ytd,
            "monthly": tech_monthly,
            "win_rate": {e: win_rate(tech_monthly[e]) for e in tech_monthly},
            "gate_mode": t_meta.get("mode"),
        },
    }

    def fmt(x):
        return "—" if x is None else f"{x*100:+.2f}%"

    lines = []
    lines.append("# 2026年1–9月 分月回测（真闸 hybrid · 周频 · 成本10bp）\n")
    lines.append(f"- 生成：{payload['as_of']} UTC")
    lines.append(f"- 闸：`{gate_meta.get('mode')}`（2026年内 overlay 覆盖较好；详见 caveat）")
    lines.append(f"- 调仓：周频；成本单边 {COST}")
    lines.append(f"- YTD 截止：样本末日 ≈ {sim_idx[-1].date()}（含9月已过交易日，非整月）\n")

    lines.append("## 标普袖（70/30 C 骨架）\n")
    header = "| 月份 | C | B | C_dyn40 | C_mom12 | SPY BH | mom12−C |"
    sep = "|---|---:|---:|---:|---:|---:|---:|"
    lines.append(header)
    lines.append(sep)
    for m in MONTHS:
        cells = [month_names[m]]
        for b in ["C", "B", "C_dyn40", "C_mom12", "SPY_BH"]:
            v = spy_monthly[b][m]
            cells.append(fmt(v["ret"] if v else None))
        c = spy_monthly["C"][m]
        m12 = spy_monthly["C_mom12"][m]
        diff = None if not c or not m12 else m12["ret"] - c["ret"]
        cells.append(fmt(diff))
        lines.append("| " + " | ".join(cells) + " |")
    # YTD
    ycells = ["**YTD**"]
    for b in ["C", "B", "C_dyn40", "C_mom12", "SPY_BH"]:
        ycells.append(f"**{fmt(spy_ytd[b])}**")
    ycells.append(f"**{fmt((spy_ytd['C_mom12'] or 0)-(spy_ytd['C'] or 0) if spy_ytd['C'] is not None and spy_ytd['C_mom12'] is not None else None)}**")
    lines.append("| " + " | ".join(ycells) + " |")
    lines.append("")
    lines.append(
        f"- 9个月胜率（收益>0）：C {spy_monthly and win_rate(spy_monthly['C']):.0%} · "
        f"mom12 {win_rate(spy_monthly['C_mom12']):.0%} · SPY {win_rate(spy_monthly['SPY_BH']):.0%}"
    )
    # months mom12 beat C
    beat = []
    for m in MONTHS:
        c, m12 = spy_monthly["C"][m], spy_monthly["C_mom12"][m]
        if c and m12 and m12["ret"] > c["ret"]:
            beat.append(month_names[m])
    lines.append(f"- mom12 跑赢 C 的月份：{', '.join(beat) if beat else '无'}\n")

    lines.append("## 科技袖（70% QQQ + 30% Top3）\n")
    lines.append("| 月份 | mom6m | mom12 | blend | QQQ BH | mom12−mom6m |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for m in MONTHS:
        cells = [month_names[m]]
        for e in ["mom6m", "mom12", "blend_z", "QQQ_BH"]:
            v = tech_monthly[e][m]
            cells.append(fmt(v["ret"] if v else None))
        a, b = tech_monthly["mom6m"][m], tech_monthly["mom12"][m]
        diff = None if not a or not b else b["ret"] - a["ret"]
        cells.append(fmt(diff))
        lines.append("| " + " | ".join(cells) + " |")
    y2 = ["**YTD**"]
    for e in ["mom6m", "mom12", "blend_z", "QQQ_BH"]:
        y2.append(f"**{fmt(tech_ytd[e])}**")
    y2.append(
        f"**{fmt((tech_ytd['mom12'] or 0)-(tech_ytd['mom6m'] or 0) if tech_ytd['mom6m'] is not None and tech_ytd['mom12'] is not None else None)}**"
    )
    lines.append("| " + " | ".join(y2) + " |")
    lines.append("")
    lines.append(
        f"- 9个月胜率：mom6m {win_rate(tech_monthly['mom6m']):.0%} · "
        f"mom12 {win_rate(tech_monthly['mom12']):.0%} · QQQ {win_rate(tech_monthly['QQQ_BH']):.0%}\n"
    )

    lines.append("## 读法\n")
    lines.append("- 9月为**截至样本末日的不完全月**，不要当整月定论。")
    lines.append("- 标普：看 `mom12−C` 列；科技：看 `mom12−mom6m` 列。")
    lines.append("- 与此前全样本结论对照：标普 mom12 偏正、科技 mom12 偏负，是否在 2026 年内也成立。\n")
    lines.append("## Gate caveat\n")
    lines.append(gate_caveat)
    lines.append("\n教学/数据参考，不构成投资建议。\n")

    md_path = OUT / "monthly_2026_JanSep_breakdown.md"
    js_path = OUT / "monthly_2026_JanSep_breakdown.json"
    # JSON: convert keys to str
    def conv(obj):
        if isinstance(obj, dict):
            return {str(k): conv(v) for k, v in obj.items()}
        return obj

    js_path.write_text(json.dumps(conv(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", md_path)
    print("wrote", js_path)
    print("SPY YTD", {k: (None if v is None else round(v*100, 2)) for k, v in spy_ytd.items()})
    print("TECH YTD", {k: (None if v is None else round(v*100, 2)) for k, v in tech_ytd.items()})


if __name__ == "__main__":
    main()
