"""Long-horizon backtest of the LIVE kernel + C-grade tech dampener with the
ASYMMETRIC HYSTERESIS BAND (进档快 / 出档慢), across 3 macro regimes, net of an
8 bps transaction cost.

WHY THIS EXISTS
---------------
The one-year backtest (backtest_last_year_dampener.py) proved the dampener helps in
2026-07, but its "总收益反超" was driven by a single tail event and the raw drawdown
sensor whipsawed inside the 7-month selloff. Per the v5.1 decision we (a) moved the
hysteresis band into the L1 adapter (EquityStressSensor) so decision_kernel stays a
pure stateless map, and (b) must validate the patched dampener across MULTIPLE macro
regimes with realistic costs before封版.

This harness stitches two real-data windows the user explicitly asked for:
  * 2022 全年  (加息大熊市, 分母驱动)       -> data/_2022_*.csv (ICE DXY + HY proxy + FRED VIX/TIPS/10Y + SOXX/QQQ)
  * 2024-2026 (科技单边 + 震荡)            -> data/_*_daily.csv + data/_eq_*.csv (FRED-rebased DXY + FRED HY)
2023 is intentionally EXCLUDED (the two windows are disjoint by design).

Methodology note (must be stated): DXY in 2022 uses real ICE DX-Y.NYB (native ICE
scale, matches the frozen dxy thresholds); DXY in 2024-2026 uses FRED DTWEXBGS
rebased to the same ICE anchors (build_daily_feature_frame). HY in 2022 is a
reconstructed monthly-anchor proxy (FRED BAMLH0A0HYM2 has a 2023-07 vintage break
and does not serve 2022); HY in 2024-2026 is real FRED. Both are in bps, both feed the
SAME kernel, so the baseline-vs-dampened COMPARISON is internally consistent. The
absolute budget level differs in methodology across the 2023 gap, but that gap is
excluded so no spurious budget jump pollutes either curve.

Transaction cost: 8 bps applied to |Δbudget| each rebalance day (budget = gross proxy
exposure). Both baseline and dampened pay the same cost model, so the comparison is
fair. Net-of-cost is the headline; gross is shown for reference.

Run:  python scripts/backtest_long_run_dampener.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.regime import compute_regime
from core.regime_probabilistic import compute_regime_probs, probs_to_hard_label
from core.macro.physical_red_lines import evaluate_physical_red_lines
from core.decision_kernel import decide
from scripts.backtest_regime import CONFIG, RED_LINES, _compute_risk_score  # type: ignore
from adapters.equity_stress_sensor import EquityStressSensor

DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"

# --- Dampener / cost config ---
LOOKBACK = 20          # 20-day peak-to-trough drawdown
LAG = 2                # hysteresis-band lag (出档慢: 反弹需企稳 N 日才解除; 经长周期多环境 A/B 校准)
COST_BPS = 8           # transaction cost on |Δbudget|
COST = COST_BPS / 10000.0

# 2022 HY credit-spread proxy — documented monthly BofA US HY OAS (bps).
# FRED BAMLH0A0HYM2 has a 2023-07 vintage break and does not serve 2021-2022.
HY_MONTHLY_ANCHORS_2022 = {
    "2021-01": 372, "2021-02": 360, "2021-03": 357, "2021-04": 358,
    "2021-05": 345, "2021-06": 341, "2021-07": 339, "2021-08": 340,
    "2021-09": 338, "2021-10": 344, "2021-11": 348, "2021-12": 342,
    "2022-01": 338, "2022-02": 345, "2022-03": 380, "2022-04": 405,
    "2022-05": 445, "2022-06": 495, "2022-07": 525, "2022-08": 510,
    "2022-09": 560, "2022-10": 588, "2022-11": 510, "2022-12": 475,
}

SEGMENTS = [
    ("2022 加息大熊市", pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31"), "legacy"),
    ("2024-2026 科技单边+震荡", pd.Timestamp("2024-01-01"), pd.Timestamp("2026-07-17"), "modern"),
]


# ----------------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------------
def _load_fred_csv(name: str, col: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    p = DATA / name
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(p)
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    df[col] = pd.to_numeric(df[col], errors="coerce")
    s = df.set_index("observation_date")[col].sort_index()
    return s[(s.index >= start) & (s.index <= end)]


def _load_eq_csv(name: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    p = DATA / name
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(p, parse_dates=["observation_date"])
    s = df.dropna(subset=["close"]).set_index("observation_date")["close"].sort_index()
    return s[(s.index >= start) & (s.index <= end)]


def _hy_proxy_2022(idx: pd.DatetimeIndex) -> pd.Series:
    anchors = pd.Series({
        pd.Timestamp(f"{m}-01") + pd.offsets.MonthEnd(0): v
        for m, v in HY_MONTHLY_ANCHORS_2022.items()
    }).sort_index()
    interp = anchors.reindex(idx, method=None)
    interp = interp.interpolate(method="time").ffill().bfill()
    return interp


def _build_features(row: pd.Series) -> dict:
    return {
        "tips_yield": float(row["tips_yield"]),
        "vix": float(row["vix"]),
        "dxy": float(row["dxy"]),
        "hy_credit_spread": float(row["hy_credit_spread"]),
        "nominal_10y": float(row["nominal_10y"]),
        "tips_yield_roc_60d": (float(row["tips_yield_roc_60d"])
                               if pd.notna(row["tips_yield_roc_60d"]) else None),
        "dxy_zscore_60d": (float(row["dxy_zscore_60d"])
                           if pd.notna(row["dxy_zscore_60d"]) else None),
    }


def _risk_score_with_exemption(features: dict) -> float:
    risk_score = _compute_risk_score(features)
    _ro_cfg = CONFIG["regime"]["risk_on"]
    if (
        features.get("vix") is not None
        and features["vix"] <= _ro_cfg.get("vix_calm_max", 18.0)
        and features.get("hy_credit_spread") is not None
        and features["hy_credit_spread"] <= _ro_cfg.get("hy_calm_max", 300.0)
    ):
        risk_score = max(risk_score, 0.85)
    return risk_score


def _decide_for_day(features: dict, prev_budget: float, tech_dd: float | None) -> dict:
    """Run the real kernel for one day. tech_dd=None => dampener dormant (baseline)."""
    probs = compute_regime_probs(features)
    hard_label = probs_to_hard_label(probs)
    rule_regime = compute_regime(features, CONFIG)
    red = evaluate_physical_red_lines(features, RED_LINES)
    effective_hard = red.forced_hard_regime if red.triggered else rule_regime
    risk_score = _risk_score_with_exemption(features)

    feats = dict(features)
    if tech_dd is not None:
        feats["tech_drawdown"] = float(tech_dd)

    dec = decide(
        features=feats,
        hard_regime=effective_hard,
        soft_regime_label=hard_label,
        risk_score=risk_score,
        confidence=0.75,
        config=CONFIG,
        previous_risk_budget=prev_budget,
    )
    note = dec.audit_trail.get("step_2c_tech_dampener") or {}
    return {
        "rule_regime": rule_regime,
        "effective_hard_regime": effective_hard,
        "risk_budget": dec.risk_budget,
        "authority": dec.authority.value,
        "dampener_active": bool(note.get("active")),
        "dampener_cap": note.get("cap"),
    }


# ----------------------------------------------------------------------------
# Segment frame builders
# ----------------------------------------------------------------------------
def build_segment_2022(start: pd.Timestamp, end: pd.Timestamp, lag: int) -> pd.DataFrame:
    vix = _load_fred_csv("_2022_vix.csv", "VIXCLS", start, end)
    tips = _load_fred_csv("_2022_tips.csv", "DFII10", start, end)
    nom = _load_fred_csv("_2022_nom10y.csv", "DGS10", start, end)
    dxy = _load_fred_csv("_2022_dxy.csv", "close", start, end)         # ICE DX-Y.NYB (native scale)
    soxx = _load_eq_csv("_2022_eq_soxx.csv", pd.Timestamp("2021-01-01"), end)
    qqq = _load_eq_csv("_2022_eq_qqq.csv", pd.Timestamp("2021-01-01"), end)

    idx = vix.index
    frame = pd.DataFrame({
        "vix": vix.reindex(idx).ffill().bfill(),
        "tips_yield": tips.reindex(idx).ffill().bfill(),
        "nominal_10y": nom.reindex(idx).ffill().bfill(),
        "hy_credit_spread": _hy_proxy_2022(idx),                       # bps proxy
        "dxy": dxy.reindex(idx).interpolate(method="time").ffill().bfill(),
    })
    frame["tips_yield_roc_60d"] = frame["tips_yield"].pct_change(60)
    dxy_mean = frame["dxy"].rolling(60).mean()
    dxy_std = frame["dxy"].rolling(60).std()
    frame["dxy_zscore_60d"] = (frame["dxy"] - dxy_mean) / dxy_std
    frame = frame.dropna(subset=["vix", "tips_yield", "nominal_10y",
                                 "hy_credit_spread", "dxy"]).sort_index()
    frame = frame[(frame.index >= start) & (frame.index <= end)]
    return _attach_soxx(frame, soxx, qqq, lag)


def build_segment_modern(start: pd.Timestamp, end: pd.Timestamp, lag: int) -> pd.DataFrame:
    from scripts.backtest_regime import DXY_MONTHLY  # ICE monthly anchors

    vix = _load_fred_csv("_vix_daily.csv", "VIXCLS", start, end)
    tips = _load_fred_csv("_tips_daily.csv", "DFII10", start, end)
    nom = _load_fred_csv("_nom10y_daily.csv", "DGS10", start, end)
    hy_pct = _load_fred_csv("_hy_daily.csv", "BAMLH0A0HYM2", start, end)
    dxy_raw = _load_fred_csv("_dxy_daily.csv", "DTWEXBGS", start, end)
    soxx = _load_eq_csv("_eq_sox.csv", pd.Timestamp("2023-10-01"), end)
    qqq = _load_eq_csv("_eq_qqq.csv", pd.Timestamp("2023-10-01"), end)

    idx = vix.index
    tips_a = tips.reindex(idx).ffill().bfill()
    nom_a = nom.reindex(idx).ffill().bfill()
    hy_a = (hy_pct.reindex(idx).ffill().bfill()) * 100.0

    # Rebase DTWEXBGS to ICE monthly anchors (same as daily pipeline).
    dxy_raw_d = dxy_raw.reindex(idx).interpolate(method="time").ffill().bfill()
    ratio_series = pd.Series(dtype=float)
    for m, ice_val in DXY_MONTHLY.items():
        y, mo = map(int, m.split("-"))
        mend = pd.Timestamp(y, mo, 1) + pd.offsets.MonthEnd(0)
        nearest = dxy_raw.index[dxy_raw.index.get_indexer([mend], method="nearest")[0]]
        dtwex_val = dxy_raw.asof(nearest)
        if dtwex_val is not None and not pd.isna(dtwex_val):
            ratio_series.loc[nearest] = ice_val / float(dtwex_val)
    ratio_daily = ratio_series.reindex(idx).interpolate(method="time").bfill()
    dxy_daily = dxy_raw_d * ratio_daily

    frame = pd.DataFrame({
        "vix": vix.reindex(idx).ffill().bfill(),
        "tips_yield": tips_a,
        "nominal_10y": nom_a,
        "hy_credit_spread": hy_a,
        "dxy": dxy_daily.reindex(idx),
    })
    frame["tips_yield_roc_60d"] = frame["tips_yield"].pct_change(60)
    dxy_mean = frame["dxy"].rolling(60).mean()
    dxy_std = frame["dxy"].rolling(60).std()
    frame["dxy_zscore_60d"] = (frame["dxy"] - dxy_mean) / dxy_std
    frame = frame.dropna(subset=["vix", "tips_yield", "nominal_10y",
                                 "hy_credit_spread", "dxy"]).sort_index()
    frame = frame[(frame.index >= start) & (frame.index <= end)]
    return _attach_soxx(frame, soxx, qqq, lag)


def _attach_soxx(frame: pd.DataFrame, soxx: pd.Series, qqq: pd.Series, lag: int) -> pd.DataFrame:
    sensor = EquityStressSensor(lookback_window=LOOKBACK, smoothing_lag_days=lag)
    soxx_dd = sensor.smoothed_drawdown_series(soxx).reindex(frame.index).ffill().bfill()
    soxx_ret = soxx.pct_change().reindex(frame.index).ffill().fillna(0.0)
    qqq_ret = qqq.pct_change().reindex(frame.index).ffill().fillna(0.0)
    frame = frame.copy()
    frame["soxx_dd20"] = soxx_dd
    frame["soxx_ret"] = soxx_ret
    frame["qqq_ret"] = qqq_ret
    return frame


def build_segment(kind: str, start: pd.Timestamp, end: pd.Timestamp, lag: int) -> pd.DataFrame:
    if kind == "legacy":
        return build_segment_2022(start, end, lag)
    return build_segment_modern(start, end, lag)


# ----------------------------------------------------------------------------
# Curve helpers
# ----------------------------------------------------------------------------
def max_dd(nav: np.ndarray) -> float:
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1.0).min())


def build_nav(budgets: np.ndarray, rets: np.ndarray, cost: float) -> dict:
    """Return gross + net (cost on |Δbudget|) NAV arrays. Day 0 = entry, no cost."""
    n = len(budgets)
    gross = np.ones(n)
    net = np.ones(n)
    for i in range(n):
        if i == 0:
            gross[i] = 1.0 + budgets[i] * rets[i]
            net[i] = gross[i]
        else:
            gross[i] = gross[i - 1] * (1.0 + budgets[i] * rets[i])
            net[i] = net[i - 1] * (1.0 + budgets[i] * rets[i]) * (1.0 - cost * abs(budgets[i] - budgets[i - 1]))
    turnover = float(np.abs(np.diff(budgets)).sum())
    return {"gross": gross, "net": net, "turnover": turnover}


def block_stats(base_nav: dict, damp_nav: dict, label: str) -> dict:
    return {
        "segment": label,
        "baseline_total_return_pct": round(float(base_nav["net"][-1] - 1) * 100, 2),
        "baseline_max_dd_pct": round(max_dd(base_nav["net"]) * 100, 2),
        "baseline_gross_total_return_pct": round(float(base_nav["gross"][-1] - 1) * 100, 2),
        "dampened_total_return_pct": round(float(damp_nav["net"][-1] - 1) * 100, 2),
        "dampened_max_dd_pct": round(max_dd(damp_nav["net"]) * 100, 2),
        "dampened_gross_total_return_pct": round(float(damp_nav["gross"][-1] - 1) * 100, 2),
        "base_turnover": round(base_nav["turnover"], 3),
        "damp_turnover": round(damp_nav["turnover"], 3),
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def process_segment(name: str, kind: str, start: pd.Timestamp, end: pd.Timestamp, lag: int) -> dict:
    frame = build_segment(kind, start, end, lag)
    if frame.empty:
        return {"name": name, "rows": 0}
    rows = []
    prev_base = 0.0
    prev_damp = 0.0
    for date, row in frame.iterrows():
        feats = _build_features(row)
        dd = float(row["soxx_dd20"])
        base = _decide_for_day(feats, prev_base, tech_dd=None)
        damp = _decide_for_day(feats, prev_damp, tech_dd=dd)
        prev_base = base["risk_budget"]
        prev_damp = damp["risk_budget"]
        rows.append({
            "date": date.strftime("%Y-%m-%d"), "_ts": date,
            "segment": name, "rule_regime": base["rule_regime"],
            "effective_hard_regime": base["effective_hard_regime"],
            "vix": round(feats["vix"], 2), "hy_spread": round(feats["hy_credit_spread"], 0),
            "dxy": round(float(row["dxy"]), 2),
            "soxx_dd20": round(dd, 4),
            "base_budget": base["risk_budget"], "damp_budget": damp["risk_budget"],
            "dampener_active": damp["dampener_active"], "dampener_cap": damp["dampener_cap"],
            "soxx_ret": float(row["soxx_ret"]), "qqq_ret": float(row["qqq_ret"]),
        })
    df = pd.DataFrame(rows)

    soxx_r = df["soxx_ret"].values
    qqq_r = df["qqq_ret"].values
    base_soxx = build_nav(df["base_budget"].values, soxx_r, COST)
    damp_soxx = build_nav(df["damp_budget"].values, soxx_r, COST)
    base_qqq = build_nav(df["base_budget"].values, qqq_r, COST)
    damp_qqq = build_nav(df["damp_budget"].values, qqq_r, COST)

    soxx_block = block_stats(base_soxx, damp_soxx, name)
    qqq_block = block_stats(base_qqq, damp_qqq, name)

    n = len(df)
    risk_on_days = int((df["rule_regime"] == "RISK_ON").sum())
    trig_days = int(df["dampener_active"].sum())
    cap_counts = df[df["dampener_active"]]["dampener_cap"].value_counts().to_dict()
    diff_days = int((df["damp_budget"] < df["base_budget"] - 1e-9).sum())
    regime_dist = {k: int(v) for k, v in df["rule_regime"].value_counts().items()}

    return {
        "name": name, "rows": n,
        "risk_on_days": risk_on_days, "dampener_active_days": trig_days,
        "dampener_active_pct": round(100.0 * trig_days / max(1, risk_on_days), 1),
        "budget_lowered_days": diff_days,
        "dampener_cap_distribution": {str(k): int(v) for k, v in cap_counts.items()},
        "regime_distribution": regime_dist,
        "mean_base_budget": round(float(df["base_budget"].mean()), 4),
        "mean_damp_budget": round(float(df["damp_budget"].mean()), 4),
        "soxx_proxy": soxx_block, "qqq_proxy": qqq_block,
        "df": df,
    }


def main() -> None:
    # Run two sensor configs to isolate the hysteresis band's contribution:
    #   * hysteresis (LAG=2, calibrated): the shipped patch (进档快 / 出档慢)
    #   * raw        (LAG=1): no band — equivalent to the pre-patch raw-drawdown sensor
    configs = [("hysteresis", LAG), ("raw", 1)]
    results = {}
    for tag, lag in configs:
        segments = [process_segment(name, kind, s, e, lag) for (name, s, e, kind) in SEGMENTS]
        segments = [s for s in segments if s.get("rows")]
        full_df = pd.concat([seg["df"] for seg in segments]).sort_index()
        soxx_r = full_df["soxx_ret"].values
        qqq_r = full_df["qqq_ret"].values
        base_soxx = _nav_with_seg_reset(full_df, "base_budget", soxx_r)
        damp_soxx = _nav_with_seg_reset(full_df, "damp_budget", soxx_r)
        base_qqq = _nav_with_seg_reset(full_df, "base_budget", qqq_r)
        damp_qqq = _nav_with_seg_reset(full_df, "damp_budget", qqq_r)
        soxx_comb = block_stats(base_soxx, damp_soxx, "全窗口")
        qqq_comb = block_stats(base_qqq, damp_qqq, "全窗口")
        n_total = len(full_df)
        risk_on_total = int((full_df["rule_regime"] == "RISK_ON").sum())
        trig_total = int(full_df["dampener_active"].sum())
        results[tag] = {
            "lag": lag,
            "combined": {
                "trading_days": n_total, "risk_on_days": risk_on_total,
                "dampener_active_days": trig_total,
                "dampener_active_pct_of_risk_on": round(100.0 * trig_total / max(1, risk_on_total), 1),
                "soxx_proxy": {k: soxx_comb[k] for k in [
                    "baseline_total_return_pct", "baseline_max_dd_pct",
                    "dampened_total_return_pct", "dampened_max_dd_pct",
                    "baseline_gross_total_return_pct", "dampened_gross_total_return_pct",
                    "base_turnover", "damp_turnover"]},
                "qqq_proxy": {k: qqq_comb[k] for k in [
                    "baseline_total_return_pct", "baseline_max_dd_pct",
                    "dampened_total_return_pct", "dampened_max_dd_pct",
                    "baseline_gross_total_return_pct", "dampened_gross_total_return_pct",
                    "base_turnover", "damp_turnover"]},
            },
            "by_segment": [{
                "name": seg["name"], "trading_days": seg["rows"],
                "risk_on_days": seg["risk_on_days"], "dampener_active_days": seg["dampener_active_days"],
                "dampener_active_pct": seg["dampener_active_pct"],
                "budget_lowered_days": seg["budget_lowered_days"],
                "dampener_cap_distribution": seg["dampener_cap_distribution"],
                "regime_distribution": seg["regime_distribution"],
                "mean_base_budget": seg["mean_base_budget"], "mean_damp_budget": seg["mean_damp_budget"],
                "soxx_proxy": seg["soxx_proxy"], "qqq_proxy": seg["qqq_proxy"],
            } for seg in segments],
        }

    h = results["hysteresis"]
    r = results["raw"]

    # A/B: how much does the band change turnover (whipsaw friction) and net result?
    def ab(proxy):
        return {
            "hysteresis_net_return_pct": h["combined"][proxy]["dampened_total_return_pct"],
            "raw_net_return_pct": r["combined"][proxy]["dampened_total_return_pct"],
            "hysteresis_max_dd_pct": h["combined"][proxy]["dampened_max_dd_pct"],
            "raw_max_dd_pct": r["combined"][proxy]["dampened_max_dd_pct"],
            "hysteresis_turnover": h["combined"][proxy]["damp_turnover"],
            "raw_turnover": r["combined"][proxy]["damp_turnover"],
            "turnover_saved_by_band": round(r["combined"][proxy]["damp_turnover"]
                                            - h["combined"][proxy]["damp_turnover"], 3),
        }
    ab_comp = {"soxx_proxy": ab("soxx_proxy"), "qqq_proxy": ab("qqq_proxy")}

    out = {
        "config": {
            "lookback_window": LOOKBACK,
            "hysteresis_lag_days": LAG, "raw_lag_days": 1, "cost_bps": COST_BPS,
            "code_version": "live kernel + C-grade tech dampener + asymmetric hysteresis band "
                            "(-0.13/-0.10/-0.07 -> 0.35/0.50/0.65)",
            "windows": [[str(s.date()), str(e.date())] for (_, s, e, _) in SEGMENTS],
        },
        "hysteresis_vs_baseline": h["combined"],
        "raw_vs_baseline": r["combined"],
        "ab_hysteresis_vs_raw": ab_comp,
        "by_segment_hysteresis": h["by_segment"],
        "by_segment_raw": r["by_segment"],
    }

    RESEARCH.mkdir(parents=True, exist_ok=True)

    # CSV from the hysteresis (shipped) config
    full_df = pd.concat([s["df"] for s in [process_segment(name, kind, s, e, LAG)
                                            for (name, s, e, kind) in SEGMENTS] if s.get("rows")]).sort_index()
    csv_cols = ["date", "segment", "rule_regime", "effective_hard_regime", "vix", "hy_spread",
                "dxy", "soxx_dd20", "base_budget", "damp_budget", "dampener_active",
                "dampener_cap", "soxx_ret", "qqq_ret"]
    with open(RESEARCH / "backtest_long_run_dampener.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols)
        w.writeheader()
        for _, row in full_df.iterrows():
            w.writerow({c: row[c] for c in csv_cols})

    (RESEARCH / "backtest_long_run_dampener.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_md(out, h["by_segment"], h["combined"], r["combined"], ab_comp)

    print(json.dumps(out, ensure_ascii=False, indent=2))


def _nav_with_seg_reset(full_df: pd.DataFrame, budget_col: str, rets: np.ndarray) -> dict:
    """Build NAV with a cost reset at each segment boundary (no cross-segment jump cost)."""
    budgets = full_df[budget_col].values
    segs = full_df["segment"].values
    n = len(budgets)
    gross = np.ones(n)
    net = np.ones(n)
    prev_b = budgets[0]
    prev_i = 0
    for i in range(n):
        if i == 0:
            gross[i] = 1.0 + budgets[i] * rets[i]
            net[i] = gross[i]
        else:
            # reset prev at segment change: carry budget but no cost that day
            if segs[i] != segs[i - 1]:
                prev_b = budgets[i]
                gross[i] = gross[i - 1] * (1.0 + budgets[i] * rets[i])
                net[i] = gross[i]
            else:
                gross[i] = gross[i - 1] * (1.0 + budgets[i] * rets[i])
                net[i] = net[i - 1] * (1.0 + budgets[i] * rets[i]) * (1.0 - COST * abs(budgets[i] - prev_b))
                prev_b = budgets[i]
    turnover = float(np.abs(np.diff(budgets)).sum())
    return {"gross": gross, "net": net, "turnover": turnover}


def _fmt_block(b: dict) -> str:
    return (f"- 基线(净, 扣{COST_BPS}bps): 总收益 {b['baseline_total_return_pct']}%, "
            f"最大回撤 {b['baseline_max_dd_pct']}%（毛利 {b['baseline_gross_total_return_pct']}%）\n"
            f"- 阻尼(净, 扣{COST_BPS}bps): 总收益 {b['dampened_total_return_pct']}%, "
            f"最大回撤 {b['dampened_max_dd_pct']}%（毛利 {b['dampened_gross_total_return_pct']}%）\n"
            f"- 换手(累计|Δbudget|): 基线 {b['base_turnover']} / 阻尼 {b['damp_turnover']}")


def _write_md(out: dict, segments: list, h_comb: dict, r_comb: dict, ab_comp: dict) -> None:
    md = [
        "# 长周期终极回测：live kernel + C 档科技阻尼器（迟滞带）+ 8bps 成本\n",
        f"**窗口**：{out['config']['windows']}",
        f"**配置**：lookback={out['config']['lookback_window']}日, 迟滞带 lag={out['config']['hysteresis_lag_days']}日, "
        f"成本={out['config']['cost_bps']}bps, {out['config']['code_version']}\n",
        "对比方法：真实 kernel `decide()` 逐日跑两遍——基线(tech_drawdown=None, 阻尼休眠) vs "
        "阻尼(注入 SOXX 20日回撤)。净值=预算×代理日收益，8bps 成本计入每次预算变动。\n",
        "**双配置 A/B**：`hysteresis`(lag={lag}, 进档快/出档慢, 已发布补丁) vs `raw`(lag=1, 无迟滞带, 补丁前行为)，".format(lag=LAG),
        "两者跑同一真实 kernel，隔离迟滞带对「同段抖动/换手摩擦」的贡献。\n",
        "## 全窗口（2022 + 2024-2026 拼接，2023 故意排除）\n",
        f"- 交易日 {h_comb['trading_days']}，RISK_ON {h_comb['risk_on_days']} 天，"
        f"阻尼激活 {h_comb['dampener_active_days']} 天（占 RISK_ON {h_comb['dampener_active_pct_of_risk_on']}%）\n",
        "### SOXX 代理（半导体方向，用户痛点）\n", _fmt_block(h_comb['soxx_proxy']),
        "### QQQ 代理（广谱科技）\n", _fmt_block(h_comb['qqq_proxy']),
        "\n## 迟滞带 A/B（hysteresis vs raw，全窗口）\n",
        f"- SOXX 净收益：迟滞带 {ab_comp['soxx_proxy']['hysteresis_net_return_pct']}% vs 无带 {ab_comp['soxx_proxy']['raw_net_return_pct']}%"
        f"；最大回撤：{ab_comp['soxx_proxy']['hysteresis_max_dd_pct']}% vs {ab_comp['soxx_proxy']['raw_max_dd_pct']}%\n",
        f"- 换手(累计|Δbudget|)：迟滞带 {ab_comp['soxx_proxy']['hysteresis_turnover']} vs 无带 {ab_comp['soxx_proxy']['raw_turnover']}"
        f" → 迟滞带省下 **{ab_comp['soxx_proxy']['turnover_saved_by_band']}** 换手（即减少的无谓交易摩擦）\n",
        f"- QQQ 净收益：迟滞带 {ab_comp['qqq_proxy']['hysteresis_net_return_pct']}% vs 无带 {ab_comp['qqq_proxy']['raw_net_return_pct']}%"
        f"；最大回撤：{ab_comp['qqq_proxy']['hysteresis_max_dd_pct']}% vs {ab_comp['qqq_proxy']['raw_max_dd_pct']}%\n",
        "\n## 分段（三种宏观环境）\n",
    ]
    for seg in segments:
        md.append(f"### {seg['name']}\n")
        md.append(f"- 交易日 {seg['trading_days']}，RISK_ON {seg['risk_on_days']} 天，"
                  f"阻尼激活 {seg['dampener_active_days']} 天（{seg['dampener_active_pct']}%），"
                  f"预算被压低 {seg['budget_lowered_days']} 天")
        md.append(f"- 档位分布：{seg['dampener_cap_distribution']}；状态分布：{seg['regime_distribution']}")
        md.append(f"- 均值预算：基线 {seg['mean_base_budget']} → 阻尼 {seg['mean_damp_budget']}")
        md.append("#### SOXX 代理\n"); md.append(_fmt_block(seg['soxx_proxy']))
        md.append("#### QQQ 代理\n"); md.append(_fmt_block(seg['qqq_proxy']))
        md.append("")
    md += [
        "\n---\n",
        "## 方法论与局限（必须明示）\n",
        "- **2022 段 DXY** 用真实 ICE `DX-Y.NYB`（原生 ICE 刻度，匹配冻结阈值 dxy_min=103/dxy_max=100）；"
        "**2024-2026 段 DXY** 用 FRED DTWEXBGS 重定基到同一 ICE 锚点（与日频管线一致）。两者同为 ICE 刻度。\n",
        "- **2022 段 HY 信用利差** 为重建月度锚代理（FRED BAMLH0A0HYM2 在 2023-07 有 vintage 断档，不服务 2022）；"
        "2024-2026 段为真实 FRED。两者皆为 bps，喂同一内核，故 baseline-vs-阻尼 对比内部一致。\n",
        "- 2023 被故意排除（两段不连续），避免跨段预算跳变污染任一曲线；跨段不计交易成本。\n",
        "- 代理口径：预算=对代理的毛敞口，降仓收益记 0；成本仅按 |Δbudget|×8bps 计，未含滑点/融券/税费。\n",
        "- 内核 `decision_kernel` 一行未改；迟滞带全在 L1 适配器 `EquityStressSensor`（进档快/出档慢）。\n",
        "\n> ⚠️ 以上为历史回测研究，非投资建议。\n",
    ]
    (RESEARCH / "backtest_long_run_dampener.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
