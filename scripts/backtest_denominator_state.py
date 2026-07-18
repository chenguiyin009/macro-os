"""Denominator-state backtest — run the ported Pine v1.6 state machine over the
same 468-day window as the v5 kernel backtest, then cross-check against the
kernel's rule_regime / risk_budget.

Two deliverables:
  (1) denominator_state_backtest.{csv,json,md}  — FULL-CONFIDENCE run.
      Equity/gold/vol confirmation columns (IWM/KRE/QQQ/SOXX/GLD/SPX/VIX3M) are
      fetched via yfinance (cached to data/_eq_*.csv) so the v1.6 four-item
      confidence votes no longer abstain. This is a faithful port of the Pine
      logic (equity-gated branches now active).
  (2) denominator_fusion_dampener.{csv,json,md} — EXPERIMENTAL soft-overlay branch.
      When kernel=RISK_ON (budget 0.80) but Pine sees an ACTIVE stress state
      (信用传导/美元压力/久期压力/仓位主导), cap the budget at 0.50 / 0.60 and
      compare the resulting nav curve + max drawdown vs the undamped kernel.

This is EXPLORATORY (path A of the fusion plan). It does NOT modify the frozen
kernel or thresholds. Output: docs/research/
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(PROJECT_ROOT))

from core.denominator_state import (  # type: ignore
    compute_denominator_states,
    DenominatorParams,
    STATE_EASE, STATE_DURATION, STATE_DOLLAR, STATE_CREDIT,
    STATE_POSITIONING, STATE_SPLIT, STATE_WARMUP, DONT_DO,
)
from scripts.backtest_regime import DXY_MONTHLY  # type: ignore

DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "docs" / "research"
WINDOW_START = "2024-10-01"
WINDOW_END = "2026-07-17"
PREPEND_START = "2024-01-01"   # warmup history so rolling z-scores are valid
EQUITY_END = "2026-07-18"

# Pine state -> kernel regime (for cross-validation only)
PINE_TO_KERNEL = {
    "分母端宽松": "RISK_ON",
    "久期压力": "TRANSITION",
    "美元压力": "TIGHT_LIQUIDITY",
    "信用传导": "LIQUIDITY_SQUEEZE",
    "仓位主导(覆盖)": "CASH_LIQUIDATION",
    "分裂/未确认": "UNKNOWN",
    "预热中": "UNKNOWN",
}
# Illustrative budget a naive "Pine-gates-budget" mapping would imply (NOT used by kernel)
PINE_BUDGET = {
    "分母端宽松": 0.80,
    "久期压力": 0.50,
    "美元压力": 0.30,
    "信用传导": 0.0,
    "分裂/未确认": 0.50,
    "仓位主导(覆盖)": 0.0,
    "预热中": 0.50,
}
# Active stress states that should trigger the soft dampener on RISK_ON days
STRESS_STATES = {STATE_CREDIT, STATE_DOLLAR, STATE_DURATION, STATE_POSITIONING}

# yfinance ticker per confirmation column (Pine symbol in comment)
EQUITY_MAP = {
    "qqq": "QQQ",        # NASDAQ:QQQ
    "sox": "SOXX",       # NASDAQ:SOXX
    "iwm": "IWM",        # AMEX:IWM
    "kre": "KRE",        # AMEX:KRE
    "gold": "GLD",       # AMEX:GLD
    "spx": "^GSPC",      # SP:SPX (price proxy)
    "vix3m": "VIX3M",    # CBOE:VIX3M (may be unavailable)
}


def _load(csv_name, value_col):
    df = pd.read_csv(DATA_DIR / csv_name)
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    df = df.set_index("observation_date").sort_index()
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    return df[value_col]


def _fetch_yf(ticker, col, start, end):
    """Fetch a confirmation series via yfinance; cache to data/_eq_{col}.csv.

    Returns a Series indexed by naive date, or None on failure (caller treats
    absence as 'abstain'). Uses Ticker.history (stable single-column Close).
    yfinance >=1.5 returns tz-aware indices; we strip tz so it reindexes cleanly
    against the naive FRED-derived index.
    """
    path = DATA_DIR / f"_eq_{col}.csv"
    if path.exists():
        s = pd.read_csv(path)
        s["observation_date"] = pd.to_datetime(s["observation_date"])
        if getattr(s["observation_date"].dt, "tz", None) is not None:
            s["observation_date"] = s["observation_date"].dt.tz_localize(None)
        return s.set_index("observation_date")["close"]
    try:
        import yfinance as yf  # imported lazily; harness degrades gracefully if absent
        t = yf.Ticker(ticker)
        hist = t.history(start=start, end=end, auto_adjust=True)
        if hist is None or "Close" not in hist.columns or hist["Close"].dropna().empty:
            print(f"[warn] yfinance {ticker}: no data returned")
            return None
        close = hist["Close"]
        if getattr(close.index, "tz", None) is not None:
            close = close.tz_localize(None)
        saved = close.rename("close").reset_index()
        saved.columns = ["observation_date", "close"]
        saved.to_csv(path, index=False)
        return close
    except Exception as e:  # noqa: BLE001
        print(f"[warn] yfinance fetch {ticker} failed: {e!r}")
        return None


def build_frame():
    vix = _load("_vix_daily.csv", "VIXCLS")
    tips = _load("_tips_daily.csv", "DFII10")
    n10 = _load("_nom10y_daily.csv", "DGS10")
    n02 = _load("_nom2y_daily.csv", "DGS2")
    n30 = _load("_nom30y_daily.csv", "DGS30")
    bei = _load("_bei_daily.csv", "T10YIE")
    hy_pct = _load("_hy_daily.csv", "BAMLH0A0HYM2")
    dxy_raw = _load("_dxy_daily.csv", "DTWEXBGS")

    idx = vix.index
    tips_a = tips.reindex(idx).ffill().bfill()
    n02_a = n02.reindex(idx).ffill().bfill()
    n10_a = n10.reindex(idx).ffill().bfill()
    n30_a = n30.reindex(idx).ffill().bfill()
    bei_a = bei.reindex(idx).ffill().bfill()
    hy_a = (hy_pct.reindex(idx).ffill().bfill()) * 100.0

    # Rebase DTWEXBGS to ICE monthly anchors (same as daily pipeline)
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

    # Build on the FULL index (incl. prepended warmup history) so rolling z-scores
    # are valid across the whole eval window; the window is sliced later via merge.
    frame = pd.DataFrame({
        "vix": vix.reindex(idx).ffill().bfill(),
        "tips_yield": tips_a.reindex(idx),
        "nominal_2y": n02_a.reindex(idx),
        "nominal_10y": n10_a.reindex(idx),
        "nominal_30y": n30_a.reindex(idx),
        "bei_10y": bei_a.reindex(idx),
        "hy_credit_spread": hy_a.reindex(idx),
        "dxy": dxy_daily.reindex(idx),
    })

    # --- Confirmation columns (equity / gold / vol) ---
    present_eq = []
    for col, ticker in EQUITY_MAP.items():
        s = _fetch_yf(ticker, col, PREPEND_START, EQUITY_END)
        if s is not None:
            frame[col] = s.reindex(idx).ffill().bfill()
            present_eq.append(col)
    print(f"[info] confirmation columns present: {present_eq}")

    drop_cols = ["vix", "tips_yield", "nominal_10y", "nominal_30y",
                 "bei_10y", "hy_credit_spread", "dxy"]
    if "qqq" in frame.columns:
        drop_cols.append("qqq")  # qqq also drives the drawdown proxy; keep it clean
    frame = frame.dropna(subset=drop_cols)
    return frame, present_eq


def _max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    dd = (peak - nav) / peak
    return float(dd.max())


def main():
    frame, present_eq = build_frame()
    params = DenominatorParams()
    ds = compute_denominator_states(frame, params)
    ds["pine_kernel_expected"] = ds["state"].map(PINE_TO_KERNEL)
    ds["pine_implied_budget"] = ds["state"].map(PINE_BUDGET)

    # Merge with v5 kernel results
    kernel = pd.read_csv(OUTPUT_DIR / "pipeline_backtest_daily_v5.csv")
    kernel["date"] = pd.to_datetime(kernel["date"])
    kernel = kernel.set_index("date")
    merged = ds.join(kernel[["rule_regime", "risk_budget", "effective_hard_regime",
                             "kernel_authority", "kernel_action"]], how="inner")

    # Agreement: Pine expected regime vs kernel rule_regime
    merged["regime_match"] = merged["pine_kernel_expected"] == merged["rule_regime"]
    merged["pine_uncertain"] = merged["pine_kernel_expected"].isin(["UNKNOWN"])

    # ---------------- (1) FULL-CONFIDENCE cross-validation output ----------------
    out_csv = OUTPUT_DIR / "denominator_state_backtest.csv"
    merged.to_csv(out_csv)

    state_dist = Counter(merged["state"])
    conf_dist = Counter(merged["confidence_label"])
    kernel_dist = Counter(merged["rule_regime"])
    match_rate = merged["regime_match"].mean()
    certain = merged[~merged["pine_uncertain"]]
    certain_match = certain["regime_match"].mean() if len(certain) else float("nan")
    disagrees = merged[(~merged["pine_uncertain"]) & (~merged["regime_match"])].copy().sort_index()

    missing_eq = [c for c in EQUITY_MAP if c not in present_eq]
    limitations = [
        "Pine thresholds are hand-set/unbacktested; this is a logic port only, not a calibrated gate",
        "仓位主导(覆盖) state requires SPX<0 & GLD<0 & TLT<0 + VIX term inversion; "
        + ("emittable now" if ("spx" in present_eq and "gold" in present_eq) else "still limited (SPX/GLD gap)"),
    ]
    if missing_eq:
        limitations.append(f"confirmation columns absent -> votes abstain: {missing_eq}")
    if "vix3m" not in present_eq:
        limitations.append("VIX3M absent -> credit-term-structure vote abstains")

    stats = {
        "window": f"{WINDOW_START}..{WINDOW_END}",
        "n_days": int(len(merged)),
        "pine_state_distribution": dict(state_dist),
        "pine_confidence_distribution": dict(conf_dist),
        "kernel_regime_distribution": dict(kernel_dist),
        "overall_regime_match_rate": round(float(match_rate), 4),
        "certain_days": int(len(certain)),
        "certain_match_rate": round(float(certain_match), 4),
        "uncertain_days (Pine split/warmup)": int(merged["pine_uncertain"].sum()),
        "mean_kernel_budget": round(float(merged["risk_budget"].mean()), 4),
        "mean_pine_implied_budget": round(float(merged["pine_implied_budget"].mean()), 4),
        "disagreement_count": int(len(disagrees)),
        "confirmation_columns_present": present_eq,
        "limitations": limitations,
    }
    (OUTPUT_DIR / "denominator_state_backtest.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    md = []
    md.append("# Denominator State Machine — 与 v5 kernel 交叉验证(完整置信度版)\n")
    md.append(f"窗口: {stats['window']}  样本: {stats['n_days']} 天\n")
    md.append(f"\n## 一、状态分布对比\n")
    md.append(f"| Pine v1.6 状态 | 天数 | Kernel regime | 天数 |")
    md.append(f"|---|---|---|---|")
    all_states = list(state_dist.keys()) + [k for k in kernel_dist if k not in state_dist]
    for s in all_states:
        k = PINE_TO_KERNEL.get(s, "")
        md.append(f"| {s} | {state_dist.get(s,0)} | {k} | {kernel_dist.get(k,0)} |")
    md.append(f"\n## 二、一致性\n")
    md.append(f"- 整体 regime 匹配率: **{stats['overall_regime_match_rate']*100:.1f}%**")
    md.append(f"- Pine 确定日(非分裂/预热): {stats['certain_days']} 天, 匹配率 **{stats['certain_match_rate']*100:.1f}%**")
    md.append(f"- Pine 不确定日(分裂/预热): {stats['uncertain_days (Pine split/warmup)']} 天")
    md.append(f"- Kernel 平均预算: {stats['mean_kernel_budget']} | Pine 隐含预算(仅示意): {stats['mean_pine_implied_budget']}")
    md.append(f"\n## 三、置信度分布(v1.6 四项)\n")
    for lbl in ["高", "中", "低"]:
        md.append(f"- {lbl}: {conf_dist.get(lbl,0)} 天")
    md.append(f"\n## 四、关键分歧日 (Pine 确定但 ≠ Kernel regime) — 证伪清单\n")
    md.append(f"共 {stats['disagreement_count']} 天。\n")
    md.append(f"| 日期 | Pine 状态 | Kernel regime | Kernel 预算 | tips_z5 | dxy_z5 | cs_z5 | n30_z5 |")
    md.append(f"|---|---|---|---|---|---|---|---|")
    for d, row in disagrees.iterrows():
        md.append(f"| {d.date()} | {row['state']} | {row['rule_regime']} | {row['risk_budget']:.2f} | "
                  f"{row['tips_z5']:.2f} | {row['dxy_z5']:.2f} | {row['cs_z5']:.2f} | {row['n30_z5']:.2f} |")
    md.append(f"\n## 五、确认列状态\n")
    md.append(f"- 已装载: {present_eq}")
    if missing_eq:
        md.append(f"- 缺失(弃权): {missing_eq}")
    md.append(f"\n## 六、局限(必须明示)\n")
    for lim in limitations:
        md.append(f"- {lim}")
    md.append(f"\n> ⚠️ 以上为 AI 基于公开信息整理的框架对照研究, 仅供方法学讨论, 不构成投资建议。\n")
    (OUTPUT_DIR / "denominator_state_backtest.md").write_text("\n".join(md), encoding="utf-8")

    # ---------------- (2) DAMPENER EXPERIMENT ----------------
    ron = merged["rule_regime"] == "RISK_ON"
    stress = merged["state"].isin(STRESS_STATES)
    damp_mask = ron & stress
    merged["damped_50"] = merged["risk_budget"]
    merged["damped_60"] = merged["risk_budget"]
    merged.loc[damp_mask, "damped_50"] = merged.loc[damp_mask, "risk_budget"].clip(upper=0.50)
    merged.loc[damp_mask, "damped_60"] = merged.loc[damp_mask, "risk_budget"].clip(upper=0.60)

    has_qqq = "qqq" in frame.columns
    dd = {}
    if has_qqq:
        qqq_ret = frame["qqq"].reindex(merged.index).pct_change().fillna(0.0)
        merged["qqq_ret"] = qqq_ret

        def nav_of(bcol):
            return (1.0 + merged[bcol] * qqq_ret).cumprod()

        for b in ["risk_budget", "damped_50", "damped_60"]:
            merged[f"nav_{b}"] = nav_of(b)
        n = len(merged)
        for b in ["risk_budget", "damped_50", "damped_60"]:
            nav = merged[f"nav_{b}"]
            tot = float(nav.iloc[-1] - 1.0)
            ann = float((1.0 + tot) ** (252.0 / n) - 1.0)
            dd[b] = {
                "max_drawdown": round(_max_drawdown(nav), 4),
                "total_return": round(tot, 4),
                "annualized": round(ann, 4),
                "final_nav": round(float(nav.iloc[-1]), 4),
            }
        qqq_ret_on_damped = float(qqq_ret[damp_mask].mean()) if damp_mask.any() else float("nan")
    else:
        qqq_ret_on_damped = float("nan")
        for b in ["risk_budget", "damped_50", "damped_60"]:
            dd[b] = {"max_drawdown": None, "total_return": None, "annualized": None, "final_nav": None}

    trigger_breakdown = Counter(merged.loc[damp_mask, "state"])

    # Forward-looking check: does Pine stress on RISK_ON days predict FUTURE QQQ weakness?
    fwd = {}
    if has_qqq:
        qret = frame["qqq"].reindex(merged.index).pct_change()
        trig_idx = merged.index[damp_mask]
        non_idx = merged.index[ron & ~stress]

        def _fwd_mean(idx, h):
            vals = []
            for d in idx:
                sub = qret[qret.index > d].head(h)
                if len(sub):
                    vals.append(sub.sum())
            return float(np.mean(vals)) if vals else float("nan")

        fwd = {
            "trig_days": int(len(trig_idx)),
            "non_trig_risk_on_days": int(len(non_idx)),
            "trig_fwd5_mean": round(_fwd_mean(trig_idx, 5), 5),
            "non_fwd5_mean": round(_fwd_mean(non_idx, 5), 5),
            "trig_fwd20_mean": round(_fwd_mean(trig_idx, 20), 5),
            "non_fwd20_mean": round(_fwd_mean(non_idx, 20), 5),
        }
    damp_stats = {
        "window": stats["window"],
        "n_risk_on": int(ron.sum()),
        "n_damped": int(damp_mask.sum()),
        "damp_trigger_breakdown_by_pine_state": dict(trigger_breakdown),
        "qqq_mean_ret_on_damped_days": round(qqq_ret_on_damped, 5) if not pd.isna(qqq_ret_on_damped) else None,
        "baseline (no dampener)": dd["risk_budget"],
        "damped_cap_0.50": dd["damped_50"],
        "damped_cap_0.60": dd["damped_60"],
        "forward_qqq_check": fwd,
        "note": "Dampener only affects RISK_ON days where Pine emits an active stress state; "
                "other regimes keep their frozen budget. This is a soft overlay, NOT a kernel edit.",
        "drawdown_proxy": "nav = cumprod(1 + budget * QQQ_daily_return); illustrative only, not a real strategy.",
    }
    (OUTPUT_DIR / "denominator_fusion_dampener.json").write_text(
        json.dumps(damp_stats, ensure_ascii=False, indent=2), encoding="utf-8")

    # dampener CSV
    damp_cols = ["rule_regime", "state", "confidence_label", "risk_budget",
                 "damped_50", "damped_60", "qqq_ret", "nav_risk_budget",
                 "nav_damped_50", "nav_damped_60"] if has_qqq else \
                ["rule_regime", "state", "confidence_label", "risk_budget",
                 "damped_50", "damped_60"]
    merged[damp_cols].to_csv(OUTPUT_DIR / "denominator_fusion_dampener.csv")

    # dampener MD
    dmd = []
    dmd.append("# 阻尼软叠加实验 — 带阻尼 vs 不带阻尼\n")
    dmd.append(f"窗口: {damp_stats['window']}  |  样本: {stats['n_days']} 天\n")
    dmd.append(f"\n## 触发统计\n")
    dmd.append(f"- Kernel RISK_ON 天数(预算 0.80): **{damp_stats['n_risk_on']}**")
    dmd.append(f"- 其中被阻尼触发(RISK_ON 且 Pine 活跃压力态): **{damp_stats['n_damped']}** 天")
    dmd.append(f"- 触发日 QQQ 平均日收益: {damp_stats['qqq_mean_ret_on_damped_days']}")
    dmd.append(f"\n### 触发日按 Pine 状态拆分\n")
    dmd.append(f"| Pine 状态 | 触发天数 |")
    dmd.append(f"|---|---|")
    for s, c in trigger_breakdown.most_common():
        dmd.append(f"| {s} | {c} |")
    dmd.append(f"\n## 回撤代理对比 (nav = cumprod(1 + budget × QQQ日收益))\n")
    if has_qqq:
        dmd.append(f"| 方案 | 最大回撤 | 总收益 | 年化 | 期末净值 |")
        dmd.append(f"|---|---|---|---|---|")
        for label, key in [("基准(不阻尼)", "risk_budget"), ("阻尼 cap=0.50", "damped_50"), ("阻尼 cap=0.60", "damped_60")]:
            d = dd[key]
            dmd.append(f"| {label} | {d['max_drawdown']} | {d['total_return']} | {d['annualized']} | {d['final_nav']} |")
    else:
        dmd.append(f"⚠️ QQQ 数据缺失, 跳过回撤对比(仅给出预算序列)。")
    dmd.append(f"\n## 前瞻检验: Pine 压力态是否预示 QQQ 后续走弱?\n")
    if fwd:
        dmd.append(f"- 触发日(RISK_ON + Pine 压力态)后 **5 日** QQQ 累计收益均值: **{fwd['trig_fwd5_mean']}**")
        dmd.append(f"- 非触发 RISK_ON 日后 5 日 QQQ 累计收益均值: {fwd['non_fwd5_mean']}")
        dmd.append(f"- 触发日后 **20 日** QQQ 累计收益均值: **{fwd['trig_fwd20_mean']}**")
        dmd.append(f"- 非触发 RISK_ON 日后 20 日 QQQ 累计收益均值: {fwd['non_fwd20_mean']}")
        better = fwd["trig_fwd20_mean"] < fwd["non_fwd20_mean"]
        dmd.append(f"- 判断: 触发日后 QQQ " + (
            "显著更弱 → 信号有领先性, 阻尼方向正确(但应在压力态后 N 日减仓而非当日)" if better
            else "并不更弱 → 信号对 QQQ 无领先预测力, 当日阻尼只是净牺牲收益"))
    else:
        dmd.append(f"⚠️ QQQ 数据缺失, 跳过前瞻检验。")
    dmd.append(f"\n## 说明\n")
    dmd.append(f"- 阻尼仅在 RISK_ON 日生效: kernel 预算 0.80 被压到上限 0.50 / 0.60; 其余 regime 不受影响(已≤0.50)。")
    dmd.append(f"- 触发条件 = Pine 处于 {sorted(STRESS_STATES)} 之一(分裂/未确认 不触发, 避免过度)。")
    dmd.append(f"- 这是软叠加层实验, 未改动冻结内核; Pine 阈值未经回测, 进 kernel 前需重新校准。")
    dmd.append(f"\n> ⚠️ 以上为 AI 基于公开信息整理的框架对照研究, 仅供方法学讨论, 不构成投资建议。\n")
    (OUTPUT_DIR / "denominator_fusion_dampener.md").write_text("\n".join(dmd), encoding="utf-8")

    print(f"[ok] days={len(merged)} match={match_rate*100:.1f}% certain_match={certain_match*100:.1f}%")
    print(f"[ok] Pine states: {dict(state_dist)}")
    print(f"[ok] dampened RISK_ON days: {int(damp_mask.sum())} / {int(ron.sum())}  present_eq={present_eq}")
    if has_qqq:
        print(f"[ok] maxDD baseline={dd['risk_budget']['max_drawdown']} "
              f"cap50={dd['damped_50']['max_drawdown']} cap60={dd['damped_60']['max_drawdown']}")
    print(f"[ok] wrote denominator_state_backtest.* and denominator_fusion_dampener.*")


if __name__ == "__main__":
    main()
