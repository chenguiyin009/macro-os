#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2022 bear-window scheme comparison: QQQ vs SPY.

Same scheme IDs as rates_cap_3yr (A..L), but data/kernel rebuilt for
2021-01-01..2022-12-31 with evaluation focus on the 2022 bear:
  - full 2022 calendar
  - peak-to-trough core 2022-01-03 .. 2022-10-12 (classic NDX/SPX trough area)
  - H1 2022, H2-to-trough, relief rallies (for false-cut check)

Kernel: same decide() loop as backtest_equity_overlay_2022.py
Rates: core.rates_stress on FRED DGS30 + DFII10 (30Y from long _nom30y_daily)
Denom state: compute_denominator_states when enough series present
Tech: frozen C-tier on SOXX 20d DD only (aligned with 3y harness)

Outputs:
  docs/research/rates_cap_2022_bear_qqq_spy.{md,json,csv}
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

from core.decision_kernel import decide  # noqa: E402
from core.denominator_state import DenominatorParams, compute_denominator_states  # noqa: E402
from core.macro.physical_red_lines import evaluate_physical_red_lines  # noqa: E402
from core.rates_stress import RatesStressParams, compute_rates_stress_series  # noqa: E402
from core.regime import compute_regime  # noqa: E402
from core.regime_probabilistic import compute_regime_probs, probs_to_hard_label  # noqa: E402
from scripts.backtest_regime import CONFIG, RED_LINES, _compute_risk_score  # noqa: E402

DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"
RESEARCH.mkdir(parents=True, exist_ok=True)

WARM_START = pd.Timestamp("2021-01-01")
BEAR_START = pd.Timestamp("2022-01-03")
BEAR_END = pd.Timestamp("2022-12-30")
P2T_END = pd.Timestamp("2022-10-12")  # approx equity trough cluster
FRICTION = 0.0005

TECH_TIERS = [(-0.13, 0.35), (-0.10, 0.50), (-0.07, 0.65)]
DENOM_CEILING = {
    "分母端宽松": 0.80,
    "久期压力": 0.35,
    "美元压力": 0.35,
    "信用传导": 0.10,
    "仓位主导(覆盖)": 0.10,
    "分裂/未确认": 0.55,
    "预热中": 0.55,
}

# HY monthly anchors (bps) — same proxy as equity_overlay_2022
HY_MONTHLY_ANCHORS = {
    "2021-01": 372, "2021-02": 360, "2021-03": 357, "2021-04": 358,
    "2021-05": 345, "2021-06": 341, "2021-07": 339, "2021-08": 340,
    "2021-09": 338, "2021-10": 344, "2021-11": 348, "2021-12": 342,
    "2022-01": 338, "2022-02": 345, "2022-03": 380, "2022-04": 405,
    "2022-05": 445, "2022-06": 495, "2022-07": 525, "2022-08": 510,
    "2022-09": 560, "2022-10": 588, "2022-11": 510, "2022-12": 475,
}

PROXY_NOTE = (
    "HY OAS is monthly-anchor interpolated proxy (FRED BAMLH0A0HYM2 vintage gap for 2021-22). "
    "DXY prefers ICE DX-Y.NYB cache _2022_dxy.csv. Kernel identical to overlay_2022 decide() loop."
)


def _read_csv_series(path: Path, value_candidates: List[str]) -> pd.Series:
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


def _yf_close(ticker: str, start: str, end: str, cache_name: str) -> pd.Series:
    p = DATA / cache_name
    if p.exists():
        return _read_csv_series(p, ["close", "Close", "c"])
    import yfinance as yf

    h = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
    if h is None or h.empty:
        raise RuntimeError(f"yfinance empty for {ticker}")
    close = h["Close"]
    if getattr(close.index, "tz", None) is not None:
        close = close.tz_localize(None)
    out = pd.Series(close.values, index=pd.DatetimeIndex(close.index)).sort_index()
    out = out[~out.index.duplicated(keep="last")].dropna()
    out.rename("close").to_frame().rename_axis("observation_date").reset_index().to_csv(p, index=False)
    return out


def _hy_proxy(idx: pd.DatetimeIndex) -> pd.Series:
    months = pd.DatetimeIndex([pd.Timestamp(k + "-01") for k in HY_MONTHLY_ANCHORS])
    vals = pd.Series(list(HY_MONTHLY_ANCHORS.values()), index=months, dtype=float)
    # month-end anchors
    anchors = vals.copy()
    anchors.index = anchors.index + pd.offsets.MonthEnd(0)
    s = anchors.reindex(idx.union(anchors.index)).sort_index().interpolate(method="time")
    return s.reindex(idx).ffill().bfill()


def _trailing_dd(px: pd.Series, win: int = 20) -> pd.Series:
    peak = px.rolling(win, min_periods=max(5, win // 2)).max()
    return (px / peak - 1.0).clip(upper=0.0)


def tech_cap_from_dd(dd: pd.Series) -> pd.Series:
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
        return {"total": 0.0, "ann": 0.0, "vol": 0.0, "sharpe": 0.0, "mdd": 0.0, "calmar": 0.0, "n": 0}
    total = float((1.0 + r).prod() - 1.0)
    n = len(r)
    years = max(n / 252.0, 1e-9)
    ann = float((1.0 + total) ** (1.0 / years) - 1.0)
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


def nav_from_budget(budget: pd.Series, ret: pd.Series, friction: float = FRICTION):
    b = budget.reindex(ret.index).ffill().fillna(1.0)
    b_lag = b.shift(1).fillna(b.iloc[0])
    toggle = (b_lag.diff().abs() > 1e-9).astype(float)
    strat_r = b_lag * ret - toggle * friction
    nav = (1.0 + strat_r.fillna(0.0)).cumprod()
    return nav, strat_r


def kernel_loop(frame: pd.DataFrame) -> pd.DataFrame:
    prev = 0.5
    rows = []
    for date, row in frame.iterrows():
        features = {
            "tips_yield": float(row["tips_yield"]),
            "vix": float(row["vix"]),
            "dxy": float(row["dxy"]),
            "hy_credit_spread": float(row["hy_credit_spread"]),
            "nominal_10y": float(row["nominal_10y"]),
            "tips_yield_roc_60d": float(row["tips_yield_roc_60d"]) if pd.notna(row.get("tips_yield_roc_60d")) else None,
            "dxy_zscore_60d": float(row["dxy_zscore_60d"]) if pd.notna(row.get("dxy_zscore_60d")) else None,
        }
        probs = compute_regime_probs(features)
        hard_label = probs_to_hard_label(probs)
        rule_regime = compute_regime(features, CONFIG)
        red = evaluate_physical_red_lines(features, RED_LINES)
        eff = red.forced_hard_regime if red.triggered else rule_regime
        risk_score = _compute_risk_score(features)
        _ro = CONFIG["regime"]["risk_on"]
        if (
            features["vix"] is not None
            and features["vix"] <= _ro.get("vix_calm_max", 18.0)
            and features["hy_credit_spread"] is not None
            and features["hy_credit_spread"] <= _ro.get("hy_calm_max", 300.0)
        ):
            risk_score = max(risk_score, 0.85)
        dec = decide(
            features=features,
            hard_regime=eff,
            soft_regime_label=hard_label,
            risk_score=risk_score,
            confidence=0.75,
            config=CONFIG,
            previous_risk_budget=prev,
        )
        prev = dec.risk_budget
        rows.append({"date": date, "rule_regime": eff, "risk_budget": float(dec.risk_budget)})
    return pd.DataFrame(rows).set_index("date").sort_index()


def load_macro() -> Tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series]:
    print("[1] load 2021-22 macro + equity …", flush=True)
    vix = _read_csv_series(DATA / "_2022_vix.csv", ["VIXCLS", "vix"])
    tips = _read_csv_series(DATA / "_2022_tips.csv", ["DFII10", "tips_yield"])
    n10 = _read_csv_series(DATA / "_2022_nom10y.csv", ["DGS10", "nominal_10y"])
    n30 = _read_csv_series(DATA / "_nom30y_daily.csv", ["DGS30", "nominal_30y"])
    dxy = _read_csv_series(DATA / "_2022_dxy.csv", ["close", "dxy", "DTWEXBGS"])
    qqq = _read_csv_series(DATA / "_2022_eq_qqq.csv", ["close"])
    soxx = _read_csv_series(DATA / "_2022_eq_soxx.csv", ["close"])
    # SPY / optional extras
    spy = _yf_close("SPY", "2020-06-01", "2023-01-15", "_2022_eq_spy.csv")
    # confirmation for denom if possible
    try:
        iwm = _yf_close("IWM", "2020-06-01", "2023-01-15", "_2022_eq_iwm.csv")
    except Exception:
        iwm = pd.Series(dtype=float)
    try:
        gld = _yf_close("GLD", "2020-06-01", "2023-01-15", "_2022_eq_gld.csv")
    except Exception:
        gld = pd.Series(dtype=float)

    # extend tips if needed from long file (usually not)
    tips_long = None
    if (DATA / "_tips_daily.csv").exists():
        tips_long = _read_csv_series(DATA / "_tips_daily.csv", ["DFII10"])

    idx = vix.index
    idx = idx[(idx >= WARM_START) & (idx <= BEAR_END)]
    hy = _hy_proxy(idx)

    frame = pd.DataFrame(
        {
            "vix": vix.reindex(idx).ffill().bfill(),
            "tips_yield": tips.reindex(idx).ffill().bfill(),
            "nominal_10y": n10.reindex(idx).ffill().bfill(),
            "nominal_30y": n30.reindex(idx).ffill().bfill(),
            "hy_credit_spread": hy,  # already bps-like monthly avgs
            "dxy": dxy.reindex(idx).interpolate(method="time").ffill().bfill(),
        },
        index=idx,
    )
    # denom expects hy in bp-ish; monthly anchors already bps. Daily kernel uses same units as 2022 overlay (bps).
    frame["tips_yield_roc_60d"] = frame["tips_yield"].pct_change(60)
    dxy_mean = frame["dxy"].rolling(60).mean()
    dxy_std = frame["dxy"].rolling(60).std()
    frame["dxy_zscore_60d"] = (frame["dxy"] - dxy_mean) / dxy_std
    # optional px
    frame["qqq"] = qqq.reindex(idx).ffill()
    frame["sox"] = soxx.reindex(idx).ffill()
    if len(iwm):
        frame["iwm"] = iwm.reindex(idx).ffill()
    if len(gld):
        frame["gold"] = gld.reindex(idx).ffill()
    frame["spx"] = spy.reindex(idx).ffill()  # SPY as spx proxy ok for confirmation direction
    frame = frame.dropna(subset=["vix", "tips_yield", "nominal_10y", "nominal_30y", "hy_credit_spread", "dxy"])
    return frame, qqq, spy, soxx, n30


def main() -> int:
    frame, qqq_px, spy_px, soxx_px, n30 = load_macro()
    print(f"    macro {frame.index.min().date()} .. {frame.index.max().date()} n={len(frame)}", flush=True)

    print("[2] kernel loop …", flush=True)
    kv = kernel_loop(frame)
    kernel = kv["risk_budget"].astype(float)
    regime = kv["rule_regime"].astype(str)

    print("[3] denom state + rates …", flush=True)
    # denom needs bei optional; synthesize rough bei = nom10 - tips if missing
    dframe = frame.copy()
    if "bei_10y" not in dframe.columns:
        dframe["bei_10y"] = dframe["nominal_10y"] - dframe["tips_yield"]
    # rename sox -> sox already; denominator uses "sox"? _PX_COLS has sox as "sox" - check
    # denominator_state _PX_COLS: qqq, sox - yes "sox"
    if "sox" not in dframe.columns and "soxx" in dframe.columns:
        dframe["sox"] = dframe["soxx"]
    try:
        ds = compute_denominator_states(dframe, DenominatorParams())
        denom_state = ds["state"].reindex(kv.index).ffill()
    except Exception as exc:  # noqa: BLE001
        print(f"    denom failed: {exc!r}; using SPLIT", flush=True)
        denom_state = pd.Series("分裂/未确认", index=kv.index)
    denom_c = denom_state.map(lambda s: DENOM_CEILING.get(str(s), 0.55)).astype(float)

    rframe = frame[["nominal_30y", "tips_yield"]].copy()
    variants = {
        "default": RatesStressParams(),
        "tighter": RatesStressParams(
            pct_enter=95.0, z_enter=0.75, confirm_days=2, exit_days=4,
            cap_level_only=0.60, cap_slope_level=0.50, cap_extreme=0.40, z_extreme=1.5,
        ),
        "looser": RatesStressParams(
            pct_enter=98.5, z_enter=1.25, confirm_days=3, exit_days=2,
            cap_level_only=0.70, cap_slope_level=0.60, cap_extreme=0.50, z_extreme=2.0,
        ),
        "level_only": RatesStressParams(
            z_enter=99.0, z_extreme=99.0, pct_enter=97.0, confirm_days=2, exit_days=3,
            cap_level_only=0.65, cap_slope_level=0.65, cap_extreme=0.65,
        ),
    }
    # need history before window for percentile — use full n30/tips from 2019 if possible
    n30_long = _read_csv_series(DATA / "_nom30y_daily.csv", ["DGS30"])
    tips_long = _read_csv_series(DATA / "_2022_tips.csv", ["DFII10"])
    # prepend tips only 2021-22; for percentile use n30 long + tips where available
    long_idx = n30_long.index[(n30_long.index >= "2019-01-01") & (n30_long.index <= BEAR_END)]
    rlong = pd.DataFrame({"nominal_30y": n30_long.reindex(long_idx).ffill()})
    rlong["tips_yield"] = tips_long.reindex(long_idx).ffill()
    # if tips short, leave nan early — rates uses max of tips/n30 pct
    rates = {k: compute_rates_stress_series(rlong.dropna(subset=["nominal_30y"]), p) for k, p in variants.items()}

    def rc(name: str) -> pd.Series:
        s = rates[name]["rates_cap"].reindex(kv.index).ffill().fillna(1.0).astype(float)
        return s

    rates_eng = rates["default"]["engaged"].reindex(kv.index).fillna(False).astype(bool)

    print("[4] equity align + schemes …", flush=True)
    idx = kv.index
    qqq = qqq_px.reindex(idx).ffill()
    spy = spy_px.reindex(idx).ffill()
    soxx = soxx_px.reindex(idx).ffill()
    qret = qqq.pct_change()
    sret = spy.pct_change()
    tech_c = tech_cap_from_dd(_trailing_dd(soxx, 20).reindex(idx))

    schemes: Dict[str, pd.Series] = {
        "A_buy_hold": pd.Series(1.0, index=idx),
        "B_kernel_only": kernel.reindex(idx).ffill(),
        "C_kernel_tech": np.minimum(kernel, tech_c),
        "D_kernel_denom": np.minimum(kernel, denom_c),
        "E_kernel_rates": np.minimum(kernel, rc("default")),
        "F_kernel_tech_rates": np.minimum(np.minimum(kernel, tech_c), rc("default")),
        "G_full_min": np.minimum(np.minimum(np.minimum(kernel, tech_c), denom_c), rc("default")),
        "H_kernel_rates_tighter": np.minimum(kernel, rc("tighter")),
        "I_kernel_rates_looser": np.minimum(kernel, rc("looser")),
        "J_kernel_rates_level_only": np.minimum(kernel, rc("level_only")),
        "K_tech_only": tech_c,
        "L_rates_only": rc("default"),
    }

    windows = {
        "2022_full": (BEAR_START, BEAR_END),
        "2022_p2t_Jan_to_Oct12": (BEAR_START, P2T_END),
        "2022_H1": (pd.Timestamp("2022-01-03"), pd.Timestamp("2022-06-30")),
        "2022_H2_to_trough": (pd.Timestamp("2022-07-01"), P2T_END),
        "2022_relief_JunAug": (pd.Timestamp("2022-06-17"), pd.Timestamp("2022-08-16")),
        "2022_relief_OctDec": (pd.Timestamp("2022-10-12"), BEAR_END),
        "2021_bull_control": (pd.Timestamp("2021-01-04"), pd.Timestamp("2021-12-31")),
        "full_2021_2022": (idx.min(), idx.max()),
    }

    def eval_window(wname: str, start: pd.Timestamp, end: pd.Timestamp) -> Dict:
        sub_idx = idx[(idx >= start) & (idx <= end)]
        out = {"window": wname, "start": str(sub_idx.min().date()), "end": str(sub_idx.max().date()), "n": len(sub_idx), "assets": {}}
        if len(sub_idx) < 5:
            return out
        tight = denom_state.reindex(sub_idx).isin(["久期压力", "美元压力", "信用传导", "仓位主导(覆盖)"])
        eng = rates_eng.reindex(sub_idx).fillna(False)
        out["rates_engaged_days"] = int(eng.sum())
        out["rates_engaged_frac"] = round(float(eng.mean()), 4)
        out["b_zone_days"] = int((eng & ~tight).sum())
        out["regime_top"] = regime.reindex(sub_idx).value_counts().head(5).to_dict()
        out["denom_top"] = denom_state.reindex(sub_idx).astype(str).value_counts().head(6).to_dict()
        for asset, ret_all in (("QQQ", qret), ("SPY", sret)):
            ret = ret_all.reindex(sub_idx)
            rows = []
            for name, bud in schemes.items():
                nav, sr = nav_from_budget(bud.reindex(sub_idx), ret)
                st = ann_stats(sr)
                bh_nav, bh_sr = nav_from_budget(pd.Series(1.0, index=sub_idx), ret)
                bh = ann_stats(bh_sr)
                rows.append(
                    {
                        "scheme": name,
                        **st,
                        "mean_budget": round(float(bud.reindex(sub_idx).mean()), 4),
                        "engaged_frac": round(float((bud.reindex(sub_idx) < 0.999).mean()), 4),
                        "excess": round(st["total"] - bh["total"], 4),
                        "mdd_improve": round(bh["mdd"] - st["mdd"], 4),
                        "bh_total": bh["total"],
                        "bh_mdd": bh["mdd"],
                        "end_nav": round(float(nav.iloc[-1]), 4),
                    }
                )
            # rank: bear windows by mdd_improve then excess; bull/control by sharpe then total
            if "relief" in wname or "bull" in wname or wname == "full_2021_2022":
                rows.sort(key=lambda x: (x["sharpe"] if x["sharpe"] is not None else -9, x["total"]), reverse=True)
            else:
                rows.sort(key=lambda x: (x["mdd_improve"], x["excess"], x["sharpe"] if x["sharpe"] is not None else -9), reverse=True)
            out["assets"][asset] = rows
        return out

    print("[5] evaluate windows …", flush=True)
    results = {k: eval_window(k, a, b) for k, (a, b) in windows.items()}

    # wide csv for 2022 full
    widx = idx[(idx >= BEAR_START) & (idx <= BEAR_END)]
    wide = pd.DataFrame({"date": widx})
    wide["qqq_ret"] = qret.reindex(widx).values
    wide["spy_ret"] = sret.reindex(widx).values
    wide["kernel"] = kernel.reindex(widx).values
    wide["tech_cap"] = tech_c.reindex(widx).values
    wide["denom_cap"] = denom_c.reindex(widx).values
    wide["denom_state"] = denom_state.reindex(widx).astype(str).values
    wide["regime"] = regime.reindex(widx).astype(str).values
    wide["rates_cap"] = rc("default").reindex(widx).values
    wide["rates_engaged"] = rates_eng.reindex(widx).astype(int).values
    for name, bud in schemes.items():
        wide[f"bud_{name}"] = bud.reindex(widx).values
    csv_path = RESEARCH / "rates_cap_2022_bear_qqq_spy.csv"
    wide.to_csv(csv_path, index=False)

    summary = {
        "proxy_note": PROXY_NOTE,
        "friction_per_toggle": FRICTION,
        "params": {k: asdict(v) for k, v in variants.items()},
        "windows": results,
    }
    json_path = RESEARCH / "rates_cap_2022_bear_qqq_spy.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # markdown
    lines = []
    lines.append("# 2022 熊市窗 — QQQ / SPY 方案对比")
    lines.append("")
    lines.append(f"- 数据热身: {WARM_START.date()} 起；主评估 **2022 全年** 与 **峰值→谷底(至 2022-10-12)**")
    lines.append(f"- {PROXY_NOTE}")
    lines.append("- 口径与 3 年窗一致: 预算 T-1，换档 5bps；熊窗主排序 **MDD改善 → excess**；反弹/牛市对照看 Sharpe/收益")
    lines.append("- 方案 ID 同 `rates_cap_3yr`：A 满仓 / B kernel / C +tech / D +denom / E +rates / F tech+rates / G full min / H–J rates 变体 / K tech-only / L rates-only")
    lines.append("")

    focus = [
        "2022_p2t_Jan_to_Oct12",
        "2022_full",
        "2022_H1",
        "2022_H2_to_trough",
        "2022_relief_JunAug",
        "2022_relief_OctDec",
        "2021_bull_control",
    ]
    for w in focus:
        blk = results[w]
        lines.append(f"## {w} ({blk['start']} → {blk['end']}, n={blk['n']})")
        lines.append("")
        lines.append(
            f"- rates 触发日: **{blk.get('rates_engaged_days', 0)}** ({100*blk.get('rates_engaged_frac', 0):.1f}%)；"
            f"B区(触发且分母非紧缩): **{blk.get('b_zone_days', 0)}**"
        )
        if blk.get("denom_top"):
            lines.append(f"- 分母状态 Top: `{blk['denom_top']}`")
        if blk.get("regime_top"):
            lines.append(f"- kernel regime Top: `{blk['regime_top']}`")
        lines.append("")
        for asset in ("QQQ", "SPY"):
            rows = blk.get("assets", {}).get(asset) or []
            if not rows:
                continue
            lines.append(f"### {asset}")
            lines.append("")
            lines.append("| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
            for r in rows:
                lines.append(
                    f"| {r['scheme']} | {r['total']*100:.1f}% | {r['excess']*100:+.1f}pp | "
                    f"{r['mdd']*100:.1f}% | {r['mdd_improve']*100:+.1f}pp | {r['sharpe']:.2f} | "
                    f"{r['ann']*100:.1f}% | {r['mean_budget']:.2f} |"
                )
            best = rows[0]
            lines.append("")
            lines.append(
                f"**本窗排序第一**: `{best['scheme']}` "
                f"(excess {best['excess']*100:+.1f}pp, MDD改善 {best['mdd_improve']*100:+.1f}pp, Sharpe {best['sharpe']:.2f})"
            )
            lines.append("")

    # concise conclusion from p2t and full
    lines.append("## 结论（2022）")
    lines.append("")

    def pick(w, asset, key_schemes=None):
        rows = results[w]["assets"][asset]
        if key_schemes:
            rows = [r for r in rows if r["scheme"] in key_schemes]
            rows.sort(key=lambda x: (x["mdd_improve"], x["excess"], x["sharpe"]), reverse=True)
        return rows[0], {r["scheme"]: r for r in results[w]["assets"][asset]}

    for asset in ("QQQ", "SPY"):
        best_p2t, m_p2t = pick("2022_p2t_Jan_to_Oct12", asset)
        best_full, m_full = pick("2022_full", asset)
        e = m_p2t.get("E_kernel_rates")
        b = m_p2t.get("B_kernel_only")
        c = m_p2t.get("C_kernel_tech")
        g = m_p2t.get("G_full_min")
        d = m_p2t.get("D_kernel_denom")
        l = m_p2t.get("L_rates_only")
        a = m_p2t.get("A_buy_hold")
        lines.append(f"### {asset}")
        lines.append("")
        lines.append(
            f"- **峰值→谷底**最优: `{best_p2t['scheme']}` "
            f"(收益 {best_p2t['total']*100:.1f}%, excess {best_p2t['excess']*100:+.1f}pp, MDD改善 {best_p2t['mdd_improve']*100:+.1f}pp)"
        )
        lines.append(
            f"- **2022全年**最优(同排序): `{best_full['scheme']}` "
            f"(收益 {best_full['total']*100:.1f}%, excess {best_full['excess']*100:+.1f}pp)"
        )
        if e and b:
            lines.append(
                f"- E vs B (p2t): excess {e['excess']*100:+.1f} vs {b['excess']*100:+.1f}pp；"
                f"MDD改善 {e['mdd_improve']*100:+.1f} vs {b['mdd_improve']*100:+.1f}pp "
                f"→ {'E更好' if (e['mdd_improve'], e['excess']) > (b['mdd_improve'], b['excess']) else 'E未优于B / 持平'}"
            )
        if c and e:
            lines.append(
                f"- E vs C (p2t): excess {e['excess']*100:+.1f} vs {c['excess']*100:+.1f}pp；"
                f"MDD {e['mdd']*100:.1f}% vs {c['mdd']*100:.1f}%"
            )
        if g and d:
            lines.append(
                f"- G/D (p2t): G excess {g['excess']*100:+.1f}pp / D {d['excess']*100:+.1f}pp"
            )
        if l and a:
            lines.append(
                f"- 纯 rates L vs 满仓 A (p2t): {l['total']*100:.1f}% vs {a['total']*100:.1f}% "
                f"(excess {l['excess']*100:+.1f}pp)"
            )
        lines.append("")

    lines.append("### 读法")
    lines.append("")
    lines.append("1. 2022 是利率+美元+信用同时施压的真熊，kernel/分母本身就会大幅降仓；看 rates 是否**额外**有用。")
    lines.append("2. 若 E≈B 且 L 接近 A，说明 rates 腿在 2022 仍非主保护来源。")
    lines.append("3. 反弹窗（Jun–Aug / Oct–Dec）若 G/D 明显拖累 Sharpe，说明过紧约束有空头踏空成本。")
    lines.append("4. 与 2023-26 回撤并集对比：2022 才是 rates 第一性叙事（折现率熊）的主考场。")
    lines.append("")
    lines.append(f"产物: `{csv_path.name}`, `{json_path.name}`")

    md_path = RESEARCH / "rates_cap_2022_bear_qqq_spy.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n=== 2022 p2t QQQ top ===")
    for r in results["2022_p2t_Jan_to_Oct12"]["assets"]["QQQ"][:8]:
        print(
            f"  {r['scheme']:28s} tot={r['total']*100:7.1f}% ex={r['excess']*100:+6.1f}pp "
            f"mdd={r['mdd']*100:5.1f}% imp={r['mdd_improve']*100:+5.1f}pp sharpe={r['sharpe']:5.2f} b={r['mean_budget']:.2f}"
        )
    print("\n=== 2022 p2t SPY top ===")
    for r in results["2022_p2t_Jan_to_Oct12"]["assets"]["SPY"][:8]:
        print(
            f"  {r['scheme']:28s} tot={r['total']*100:7.1f}% ex={r['excess']*100:+6.1f}pp "
            f"mdd={r['mdd']*100:5.1f}% imp={r['mdd_improve']*100:+5.1f}pp sharpe={r['sharpe']:5.2f} b={r['mean_budget']:.2f}"
        )
    print("\n=== 2022 full QQQ top ===")
    for r in results["2022_full"]["assets"]["QQQ"][:6]:
        print(
            f"  {r['scheme']:28s} tot={r['total']*100:7.1f}% ex={r['excess']*100:+6.1f}pp "
            f"mdd={r['mdd']*100:5.1f}% imp={r['mdd_improve']*100:+5.1f}pp"
        )
    print("written", md_path, json_path, csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
