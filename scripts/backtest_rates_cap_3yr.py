#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3y rates_cap scheme comparison on QQQ (纳指100 ETF) and SPY (标普500 ETF).

Compares budget overlays (additive, no kernel edit):
  A  buy_hold              — always 1.0 equity
  B  kernel_only           — v5 risk_budget (or recompute proxy) alone
  C  + tech_dd_C           — frozen SOXX C-tier dampener on kernel
  D  + denom_ceiling       — map Pine denominator state -> ceiling, min with kernel
  E  + rates_cap           — recommended fifth-leg rates stress alone on kernel
  F  kernel+tech+rates     — tech dampener AND rates_cap (orthogonal)
  G  full_min              — min(kernel, tech_dd, denom_ceiling, rates_cap)
  H  rates_tighter         — rates with lower caps / easier trigger (sensitivity)
  I  rates_looser          — rates with higher caps / harder trigger

Window: ~3y ending at latest FRED/cache date (target 2023-08-01 .. latest).
NAV: cumprod(1 + budget_{t-1} * r_t) with optional toggle friction.

Outputs under docs/research/:
  rates_cap_3yr_qqq_spy.{csv,json,md}
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.denominator_state import (  # noqa: E402
    DenominatorParams,
    compute_denominator_states,
)
from core.rates_stress import (  # noqa: E402
    RatesStressParams,
    compute_rates_stress_series,
    overlap_with_duration_state,
)
from scripts.backtest_denominator_state import build_frame  # noqa: E402

DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"
RESEARCH.mkdir(parents=True, exist_ok=True)

START = pd.Timestamp("2023-08-01")
# open end — clip to data
FRICTION = 0.0005  # 5 bps per budget toggle day (same order as overlay harness)

# Frozen tech C-tier (SOXX 20d DD -> cap). Orthogonal to rates.
TECH_TIERS = [
    (-0.13, 0.35),
    (-0.10, 0.50),
    (-0.07, 0.65),
]

DENOM_CEILING = {
    "分母端宽松": 0.80,
    "久期压力": 0.35,  # maps into tight band (含「压力」)
    "美元压力": 0.35,
    "信用传导": 0.10,
    "仓位主导(覆盖)": 0.10,
    "分裂/未确认": 0.55,
    "预热中": 0.55,
}


def _load_eq_close(candidates: List[str]) -> pd.Series:
    for name in candidates:
        p = DATA / name
        if not p.exists():
            continue
        df = pd.read_csv(p)
        # normalize columns
        cols = {c.lower(): c for c in df.columns}
        date_col = cols.get("observation_date") or cols.get("date") or cols.get("d")
        px_col = cols.get("close") or cols.get("c") or cols.get("adj close")
        if date_col is None or px_col is None:
            # try first two
            date_col, px_col = df.columns[0], df.columns[1]
        s = df[[date_col, px_col]].copy()
        s[date_col] = pd.to_datetime(s[date_col])
        if getattr(s[date_col].dt, "tz", None) is not None:
            s[date_col] = s[date_col].dt.tz_localize(None)
        out = pd.to_numeric(s[px_col], errors="coerce")
        ser = pd.Series(out.values, index=pd.DatetimeIndex(s[date_col])).sort_index()
        ser = ser[~ser.index.duplicated(keep="last")].dropna()
        if len(ser) > 100:
            return ser
    raise FileNotFoundError(f"no equity cache among {candidates}")


def _trailing_dd(px: pd.Series, win: int = 20) -> pd.Series:
    peak = px.rolling(win, min_periods=max(5, win // 2)).max()
    return (px / peak - 1.0).clip(upper=0.0)


def tech_cap_from_dd(dd: pd.Series) -> pd.Series:
    cap = pd.Series(1.0, index=dd.index)
    # apply from mild to severe so severe overwrites
    for thr, c in reversed(TECH_TIERS):
        cap = cap.where(dd > thr, c)  # dd more negative than thr
    # when dd is NaN keep 1.0
    return cap.fillna(1.0)


def max_dd(nav: pd.Series) -> float:
    peak = nav.cummax()
    dd = (peak - nav) / peak.replace(0, np.nan)
    return float(dd.max()) if len(dd) else float("nan")


def ann_stats(rets: pd.Series) -> Dict[str, float]:
    r = rets.dropna()
    if r.empty:
        return {"total": 0.0, "ann": 0.0, "vol": 0.0, "sharpe": 0.0, "mdd": 0.0, "calmar": 0.0}
    total = float((1.0 + r).prod() - 1.0)
    n = len(r)
    years = n / 252.0
    ann = float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 else float("nan")
    vol = float(r.std() * np.sqrt(252)) if n > 2 else float("nan")
    sharpe = float(ann / vol) if vol and vol > 1e-12 else float("nan")
    nav = (1.0 + r).cumprod()
    mdd = max_dd(nav)
    calmar = float(ann / mdd) if mdd and mdd > 1e-12 else float("nan")
    return {
        "total": round(total, 4),
        "ann": round(ann, 4),
        "vol": round(vol, 4),
        "sharpe": round(sharpe, 3),
        "mdd": round(mdd, 4),
        "calmar": round(calmar, 3),
        "n": int(n),
        "years": round(years, 2),
    }


def nav_from_budget(budget: pd.Series, ret: pd.Series, friction: float = FRICTION) -> Tuple[pd.Series, pd.Series]:
    """Use yesterday's budget on today's return; friction on |dbudget|>1e-9."""
    b = budget.reindex(ret.index).ffill().fillna(1.0)
    b_lag = b.shift(1).fillna(b.iloc[0])
    toggle = (b_lag.diff().abs() > 1e-9).astype(float)
    fr = toggle * friction
    strat_r = b_lag * ret - fr
    nav = (1.0 + strat_r.fillna(0.0)).cumprod()
    return nav, strat_r


def load_kernel_budget(idx: pd.DatetimeIndex) -> pd.Series:
    """Prefer production v5 daily; outside window fill 0.80 (RISK_ON default)."""
    path = RESEARCH / "pipeline_backtest_daily_v5.csv"
    base = pd.Series(0.80, index=idx, dtype=float)
    if not path.exists():
        return base
    k = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    if "risk_budget" not in k.columns:
        return base
    rb = k["risk_budget"].astype(float)
    base.loc[base.index.intersection(rb.index)] = rb.reindex(base.index.intersection(rb.index))
    return base


def main() -> int:
    print("[1/5] build denominator frame + states …", flush=True)
    frame, present_eq = build_frame()
    print(f"    frame {frame.index.min().date()} .. {frame.index.max().date()} cols={list(frame.columns)[:8]}… eq={present_eq}")
    ds = compute_denominator_states(frame, DenominatorParams())

    print("[2/5] rates stress variants …", flush=True)
    # rates frame: yields only (+ optional tlt if present as bond proxy — we don't have TLT in build_frame)
    rframe = frame[["nominal_30y"]].copy()
    if "tips_yield" in frame.columns:
        rframe["tips_yield"] = frame["tips_yield"]

    variants = {
        "rates_default": RatesStressParams(),  # recommended
        "rates_tighter": RatesStressParams(
            pct_enter=95.0, z_enter=0.75, confirm_days=2, exit_days=4,
            cap_level_only=0.60, cap_slope_level=0.50, cap_extreme=0.40, z_extreme=1.5,
        ),
        "rates_looser": RatesStressParams(
            pct_enter=98.5, z_enter=1.25, confirm_days=3, exit_days=2,
            cap_level_only=0.70, cap_slope_level=0.60, cap_extreme=0.50, z_extreme=2.0,
        ),
        "rates_level_only": RatesStressParams(
            # ignore slope: still uses level_only path when pct hot
            z_enter=99.0, z_extreme=99.0, pct_enter=97.0, confirm_days=2, exit_days=3,
            cap_level_only=0.65, cap_slope_level=0.65, cap_extreme=0.65,
        ),
    }
    rates_series = {k: compute_rates_stress_series(rframe, p) for k, p in variants.items()}

    # overlap diagnosis on default
    ov = overlap_with_duration_state(rates_series["rates_default"], ds["state"])
    print(f"    overlap default vs duration: {ov}", flush=True)

    print("[3/5] load QQQ / SPY …", flush=True)
    qqq = _load_eq_close(["_eq_qqq.csv", "_eq_QQQ.csv"])
    spy = _load_eq_close(["_eq_spx.csv", "_eq_spy.csv"])  # SPX proxy if SPY missing
    # Prefer actual SPY if yfinance cache appears later
    try:
        import yfinance as yf

        spy_yf = yf.Ticker("SPY").history(start="2022-01-01", auto_adjust=True)["Close"]
        if getattr(spy_yf.index, "tz", None) is not None:
            spy_yf = spy_yf.tz_localize(None)
        if len(spy_yf) > 200:
            spy = spy_yf
            print("    SPY from yfinance", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"    SPY yfinance fallback to cache ({exc!r})", flush=True)

    soxx = _load_eq_close(["_eq_sox.csv", "_eq_soxx.csv"])

    end = min(frame.index.max(), qqq.index.max(), spy.index.max(), soxx.index.max())
    start = START
    idx = pd.DatetimeIndex([d for d in frame.index if start <= d <= end])
    print(f"    eval window {idx[0].date()} .. {idx[-1].date()} n={len(idx)}", flush=True)

    qqq = qqq.reindex(idx).ffill()
    spy = spy.reindex(idx).ffill()
    soxx = soxx.reindex(idx).ffill()
    qret = qqq.pct_change()
    sret = spy.pct_change()
    soxx_dd = _trailing_dd(soxx, 20)
    tech_c = tech_cap_from_dd(soxx_dd)

    kernel = load_kernel_budget(idx)
    # When outside v5 file, keep 0.80 — document as baseline RISK_ON proxy
    denom_state = ds["state"].reindex(idx).ffill()
    denom_c = denom_state.map(lambda s: DENOM_CEILING.get(str(s), 0.55)).astype(float)

    rdef = rates_series["rates_default"].reindex(idx)
    rtight = rates_series["rates_tighter"].reindex(idx)
    rloose = rates_series["rates_looser"].reindex(idx)
    rlevel = rates_series["rates_level_only"].reindex(idx)

    def rc(series_df: pd.DataFrame) -> pd.Series:
        return series_df["rates_cap"].fillna(1.0).astype(float)

    schemes: Dict[str, pd.Series] = {
        "A_buy_hold": pd.Series(1.0, index=idx),
        "B_kernel_only": kernel,
        "C_kernel_tech": np.minimum(kernel, tech_c),
        "D_kernel_denom": np.minimum(kernel, denom_c),
        "E_kernel_rates": np.minimum(kernel, rc(rdef)),
        "F_kernel_tech_rates": np.minimum(np.minimum(kernel, tech_c), rc(rdef)),
        "G_full_min": np.minimum(np.minimum(np.minimum(kernel, tech_c), denom_c), rc(rdef)),
        "H_kernel_rates_tighter": np.minimum(kernel, rc(rtight)),
        "I_kernel_rates_looser": np.minimum(kernel, rc(rloose)),
        "J_kernel_rates_level_only": np.minimum(kernel, rc(rlevel)),
        "K_tech_only": tech_c,  # pure lag dampener reference
        "L_rates_only": rc(rdef),
    }

    print("[4/5] simulate …", flush=True)
    results = {"QQQ": {}, "SPY": {}}
    daily_rows = []
    for name, bud in schemes.items():
        for asset, ret in (("QQQ", qret), ("SPY", sret)):
            nav, strat_r = nav_from_budget(bud, ret)
            st = ann_stats(strat_r)
            # engaged / binding-ish stats
            engaged_frac = float((bud < 0.999).mean())
            mean_bud = float(bud.mean())
            results[asset][name] = {
                **st,
                "mean_budget": round(mean_bud, 4),
                "engaged_frac": round(engaged_frac, 4),
                "end_nav": round(float(nav.iloc[-1]), 4),
            }
        daily_rows.append(pd.DataFrame({
            "date": idx,
            "scheme": name,
            "budget": bud.values,
        }))

    # incremental B-zone: rates engaged while denom not tight
    tight_mask = denom_state.isin(["久期压力", "美元压力", "信用传导", "仓位主导(覆盖)"])
    rates_eng = rdef["engaged"].fillna(False).astype(bool)
    b_zone = rates_eng & ~tight_mask
    print(f"    B-zone days (rates on, denom not tight): {int(b_zone.sum())} / {len(idx)}", flush=True)

    # per-year breakdown for key schemes
    def yearly(asset_ret: pd.Series, bud: pd.Series) -> Dict[str, float]:
        _, sr = nav_from_budget(bud, asset_ret)
        out = {}
        for y, g in sr.groupby(sr.index.year):
            out[str(int(y))] = round(float((1.0 + g.fillna(0)).prod() - 1.0), 4)
        return out

    key = ["A_buy_hold", "B_kernel_only", "C_kernel_tech", "E_kernel_rates", "F_kernel_tech_rates", "G_full_min", "H_kernel_rates_tighter"]
    yearly_tbl = {
        "QQQ": {k: yearly(qret, schemes[k]) for k in key},
        "SPY": {k: yearly(sret, schemes[k]) for k in key},
    }

    # rank by sharpe then total for each asset
    def rank_table(asset: str) -> List[Dict]:
        rows = []
        for name, m in results[asset].items():
            rows.append({"scheme": name, **m})
        rows.sort(key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -9, x.get("total", -9)), reverse=True)
        return rows

    summary = {
        "window": {"start": str(idx[0].date()), "end": str(idx[-1].date()), "n": len(idx)},
        "overlap_rates_vs_duration": ov,
        "b_zone_days": int(b_zone.sum()),
        "b_zone_frac": round(float(b_zone.mean()), 4),
        "params": {k: asdict(v) for k, v in variants.items()},
        "friction_per_toggle": FRICTION,
        "notes": [
            "Budget applied lag-1 on returns; toggle friction 5bps.",
            "Kernel budget from pipeline_backtest_daily_v5.csv where available; else 0.80 proxy.",
            "SPY preferred via yfinance; else SPX cache proxy.",
            "Tech C-tier on SOXX 20d DD; rates on FRED 30Y/TIPS levels — orthogonal.",
            "Illustrative overlay NAV, not a live execution strategy.",
        ],
        "QQQ_ranked": rank_table("QQQ"),
        "SPY_ranked": rank_table("SPY"),
        "yearly": yearly_tbl,
        "metrics": results,
    }

    # CSV wide budgets + returns
    wide = pd.DataFrame({"date": idx})
    wide["qqq_ret"] = qret.values
    wide["spy_ret"] = sret.values
    wide["soxx_dd20"] = soxx_dd.reindex(idx).values
    wide["kernel"] = kernel.values
    wide["tech_cap"] = tech_c.values
    wide["denom_cap"] = denom_c.values
    wide["denom_state"] = denom_state.astype(str).values
    wide["rates_cap"] = rc(rdef).values
    wide["rates_engaged"] = rates_eng.astype(int).values
    wide["b_zone"] = b_zone.astype(int).values
    for name, bud in schemes.items():
        wide[f"bud_{name}"] = bud.values
    csv_path = RESEARCH / "rates_cap_3yr_qqq_spy.csv"
    wide.to_csv(csv_path, index=False)

    json_path = RESEARCH / "rates_cap_3yr_qqq_spy.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Markdown report
    def fmt_row(asset: str, name: str) -> str:
        m = results[asset][name]
        return (
            f"| {name} | {m['total']*100:.1f}% | {m['ann']*100:.1f}% | {m['vol']*100:.1f}% | "
            f"{m['sharpe']:.2f} | {m['mdd']*100:.1f}% | {m['calmar']:.2f} | {m['mean_budget']:.2f} | {m['engaged_frac']*100:.1f}% |"
        )

    lines = []
    lines.append("# rates_cap 3年方案对比 — QQQ vs SPY")
    lines.append("")
    lines.append(f"- 窗口: **{idx[0].date()} → {idx[-1].date()}** ({len(idx)} 交易日)")
    lines.append(f"- B区（rates 触发且分母非紧缩）: **{int(b_zone.sum())}** 日 ({b_zone.mean()*100:.1f}%)")
    lines.append(f"- 与「久期压力」重叠诊断: `{json.dumps(ov, ensure_ascii=False)}`")
    lines.append(f"- 摩擦: 换档 {FRICTION*10000:.0f}bps / 次；预算滞后 1 日")
    lines.append("")
    lines.append("## 方案说明")
    lines.append("")
    lines.append("| ID | 含义 |")
    lines.append("|---|---|")
    lines.append("| A_buy_hold | 始终满仓 |")
    lines.append("| B_kernel_only | 仅 kernel risk_budget |")
    lines.append("| C_kernel_tech | kernel × 冻结 SOXX C-tier 减震 |")
    lines.append("| D_kernel_denom | kernel × 分母状态 ceiling |")
    lines.append("| E_kernel_rates | kernel × **推荐 rates_cap** |")
    lines.append("| F_kernel_tech_rates | kernel × tech × rates（正交叠加） |")
    lines.append("| G_full_min | kernel × tech × denom × rates |")
    lines.append("| H/I/J | rates 更紧 / 更松 / 仅水位 |")
    lines.append("| K/L | 纯 tech / 纯 rates 参考 |")
    lines.append("")

    for asset in ("QQQ", "SPY"):
        lines.append(f"## {asset} 全样本")
        lines.append("")
        lines.append("| 方案 | 总收益 | 年化 | 波动 | Sharpe | 最大回撤 | Calmar | 均预算 | 约束日占比 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in rank_table(asset):
            lines.append(fmt_row(asset, row["scheme"]))
        lines.append("")
        best = rank_table(asset)[0]
        lines.append(f"**Sharpe 最优**: `{best['scheme']}` (Sharpe={best['sharpe']}, 总收益={best['total']*100:.1f}%, MDD={best['mdd']*100:.1f}%)")
        lines.append("")

    lines.append("## 分年收益（关键方案）")
    lines.append("")
    for asset in ("QQQ", "SPY"):
        lines.append(f"### {asset}")
        years = sorted({y for sch in key for y in yearly_tbl[asset][sch]})
        lines.append("| 方案 | " + " | ".join(years) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(years)) + "|")
        for sch in key:
            cells = [f"{yearly_tbl[asset][sch].get(y, 0)*100:.1f}%" for y in years]
            lines.append(f"| {sch} | " + " | ".join(cells) + " |")
        lines.append("")

    lines.append("## 解读要点")
    lines.append("")
    lines.append("1. 若 **E/F 相对 C** 在 QQQ 上 Sharpe 或 MDD 改善，且 B 区天数 > 0，说明 rates 前瞻腿有增量。")
    lines.append("2. 若 E≈C 且 B 区≈0，说明与分母/减震高度重叠，rates 腿应降级为分母内部阈值，而不是独立 min。")
    lines.append("3. G_full_min 通常最保守；若收益显著落后而 MDD 改善有限，生产应避免四腿过紧。")
    lines.append("4. SPY 久期敏感度弱于 QQQ，rates 腿对 SPY 的相对价值通常更低——以 QQQ 为主决策资产。")
    lines.append("")
    lines.append(f"产物: `{csv_path.name}`, `{json_path.name}`")
    md_path = RESEARCH / "rates_cap_3yr_qqq_spy.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("[5/5] written:", csv_path, json_path, md_path, flush=True)
    # concise stdout table
    print("\n=== QQQ rank (top 6) ===")
    for row in rank_table("QQQ")[:6]:
        print(f"  {row['scheme']:28s} tot={row['total']*100:6.1f}% sharpe={row['sharpe']:5.2f} mdd={row['mdd']*100:5.1f}% mean_b={row['mean_budget']:.2f}")
    print("\n=== SPY rank (top 6) ===")
    for row in rank_table("SPY")[:6]:
        print(f"  {row['scheme']:28s} tot={row['total']*100:6.1f}% sharpe={row['sharpe']:5.2f} mdd={row['mdd']*100:5.1f}% mean_b={row['mean_budget']:.2f}")
    print(f"\nB-zone days: {int(b_zone.sum())}  overlap: {ov}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
