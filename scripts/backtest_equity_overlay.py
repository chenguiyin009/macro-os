"""Equity-stress overlay experiment for Macro OS v5 kernel.

Problem statement
----------------
The kernel budget is driven ONLY by denominator variables (DXY, VIX, TIPS, HY).
A sector/equity selloff (e.g. semis -13% in July 2026) that does NOT trip the
denominator state machine leaves the budget at full 0.80 while prices crash.
Equity data (yfinance) is also fresher than FRED (T-1..T-7 lag).

This script adds a SOFT equity-stress overlay (new layer, NOT touching frozen
denominator params): when the kernel says RISK_ON (0.80) but SOXX/QQQ show an
acute trailing drawdown, cap the budget. It then backtests the overlay against
the v5 kernel budget using a QQQ-proxy equity curve and reports false-trigger
rate over the full 468-day window.

Run:  python scripts/backtest_equity_overlay.py
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"

WINDOW_START = pd.Timestamp("2024-10-01")
WINDOW_END = pd.Timestamp("2026-07-17")

# Overlay tiers: (soxx_20d_dd, qqq_20d_dd, cap). First matching tier applies.
# dd is negative (drawdown). Graduated: deeper equity stress -> lower cap.
OVERLAY_TIERS = [
    (-0.10, -0.07, 0.35),   # acute semi/tech crash
    (-0.07, -0.05, 0.50),   # clear tech stress
    (-0.05, -0.03, 0.65),   # mild tech softness
]


def _load_eq(ticker: str) -> pd.Series:
    p = DATA / f"_eq_{ticker}.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    s = pd.read_csv(p, parse_dates=["observation_date"])
    s = s.dropna(subset=["close"])
    return s.set_index("observation_date")["close"].sort_index()


def trailing_drawdown(price: pd.Series, win: int = 20) -> pd.Series:
    """Peak-to-current drawdown over a trailing window (negative)."""
    if price.empty:
        return pd.Series(dtype=float)
    roll_max = price.rolling(win, min_periods=max(5, win // 2)).max()
    return (price / roll_max - 1.0).clip(upper=0.0)


def z_score_5d(ret: pd.Series) -> pd.Series:
    if ret.empty:
        return pd.Series(dtype=float)
    r5 = ret.rolling(5).sum()
    vol = ret.rolling(60).std()
    return (r5 / (vol * np.sqrt(5))).where(vol > 0)


def main() -> None:
    # 1) kernel v5 budget
    kv5 = pd.read_csv(RESEARCH / "pipeline_backtest_daily_v5.csv", parse_dates=["date"])
    kv5 = kv5[(kv5["date"] >= WINDOW_START) & (kv5["date"] <= WINDOW_END)].copy()

    # 2) equity prices
    soxx = _load_eq("sox")
    qqq = _load_eq("qqq")
    idx = kv5.set_index("date").index

    def align(s: pd.Series) -> pd.Series:
        return s.reindex(idx).ffill().bfill()

    soxx_dd = align(trailing_drawdown(soxx, 20))
    qqq_dd = align(trailing_drawdown(qqq, 20))
    soxx_ret = align(soxx.pct_change())
    qqq_ret = align(qqq.pct_change())
    soxx_z5 = align(z_score_5d(soxx_ret))

    kv5 = kv5.set_index("date")
    kv5["soxx_dd20"] = soxx_dd
    kv5["qqq_dd20"] = qqq_dd
    kv5["soxx_z5"] = soxx_z5

    # 3) apply overlay (only dampens RISK_ON)
    def cap_for(dd_soxx, dd_qqq):
        for t_soxx, t_qqq, cap in OVERLAY_TIERS:
            if (not pd.isna(dd_soxx) and dd_soxx <= t_soxx) or \
               (not pd.isna(dd_qqq) and dd_qqq <= t_qqq):
                return cap
        return 1.0

    kv5["overlay_cap"] = [cap_for(dsox, dqq) for dsox, dqq in zip(kv5["soxx_dd20"], kv5["qqq_dd20"])]
    kv5["new_budget"] = np.where(
        kv5["rule_regime"] == "RISK_ON",
        np.minimum(kv5["risk_budget"], kv5["overlay_cap"]),
        kv5["risk_budget"],
    )
    kv5 = kv5.reset_index()

    # 4) equity-proxy curves: budget as gross exposure to daily return.
    #    QQQ-proxy (broad) AND SOXX-proxy (the actual tech/semi risk the user flags).
    qqq_r = qqq_ret.reindex(kv5.set_index("date").index).ffill().fillna(0.0).values
    soxx_r = soxx_ret.reindex(kv5.set_index("date").index).ffill().fillna(0.0).values

    def proxy_curve(budget, ret):
        return np.cumprod(1.0 + budget * ret)

    def max_dd(nav):
        peak = np.maximum.accumulate(nav)
        return float((nav / peak - 1.0).min())

    base_qqq, new_qqq = proxy_curve(kv5["risk_budget"].values, qqq_r), proxy_curve(kv5["new_budget"].values, qqq_r)
    base_soxx, new_soxx = proxy_curve(kv5["risk_budget"].values, soxx_r), proxy_curve(kv5["new_budget"].values, soxx_r)

    # 5) stats
    risk_on = kv5[kv5["rule_regime"] == "RISK_ON"]
    triggered = risk_on[risk_on["overlay_cap"] < 1.0]
    july = kv5[kv5["date"] >= "2026-07-01"]

    def proxy_block(base, new):
        return {
            "mean_budget": round(float(kv5["risk_budget"].mean()), 4),
            "max_budget": round(float(kv5["risk_budget"].max()), 4),
            "total_return_pct": round(float(base[-1] - 1) * 100, 2),
            "max_dd_pct": round(max_dd(base) * 100, 2),
            "with_overlay_total_return_pct": round(float(new[-1] - 1) * 100, 2),
            "with_overlay_max_dd_pct": round(max_dd(new) * 100, 2),
        }

    out = {
        "window": [str(WINDOW_START.date()), str(WINDOW_END.date())],
        "overlay_tiers": [{"soxx_dd20": t[0], "qqq_dd20": t[1], "cap": t[2]} for t in OVERLAY_TIERS],
        "risk_on_days": int(len(risk_on)),
        "risk_on_triggered_by_equity": int(len(triggered)),
        "trigger_rate_pct": round(100.0 * len(triggered) / max(1, len(risk_on)), 1),
        "baseline_vs_overlay_qqq_proxy": proxy_block(base_qqq, new_qqq),
        "baseline_vs_overlay_soxx_proxy": proxy_block(base_soxx, new_soxx),
        "july_baseline_budget": july["risk_budget"].tolist(),
        "july_overlay_budget": july["new_budget"].tolist(),
        "july_soxx_dd20": [None if pd.isna(x) else round(x, 4) for x in july["soxx_dd20"]],
        "july_dates": [str(d.date()) for d in july["date"]],
    }

    # false-trigger check: triggered days where QQQ next-20d return was POSITIVE
    # (overlay cut exposure but market kept rising -> opportunity cost)
    trig_dates = set(triggered["date"])
    fwd = []
    qqq_full = qqq.pct_change()
    for d in trig_dates:
        fut = qqq_full[qqq_full.index > d][:20]
        fwd.append(float(fut.sum()) if len(fut) else float("nan"))
    out["triggered_next20d_qqq_mean_pct"] = round(float(np.nanmean(fwd)) * 100, 2) if fwd else None

    # 6) write outputs
    kv5_out = kv5[["date", "rule_regime", "risk_budget", "new_budget",
                   "overlay_cap", "soxx_dd20", "qqq_dd20", "soxx_z5"]].copy()
    kv5_out.to_csv(RESEARCH / "equity_overlay_backtest.csv", index=False)
    (RESEARCH / "equity_overlay_backtest.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # markdown
    md = [f"# Equity-Stress Overlay 回测（vs v5 kernel）\n",
          f"窗口：{out['window'][0]} ~ {out['window'][1]}（468 交易日）\n",
          f"叠加规则：kernel=RISK_ON(0.80) 时，SOXX/QQQ 20日峰值回撤越深，预算 cap 越低（{OVERLAY_TIERS[0][2]}/{OVERLAY_TIERS[1][2]}/{OVERLAY_TIERS[2][2]}）。\n",
          "## 全窗口统计\n",
          f"- RISK_ON 天数：{out['risk_on_days']}",
          f"- 被 equity overlay 触发压预算：{out['risk_on_triggered_by_equity']} 天（{out['trigger_rate_pct']}%）",
          f"- 触发日后 20 日 QQQ 均值回报：{out['triggered_next20d_qqq_mean_pct']}%（正=误触发机会成本）\n",
          "## 代理净值曲线（预算=对市场代理的毛敞口）\n",
          "### QQQ 代理（广谱）\n",
          f"- 基准(v5)：总收益 {out['baseline_vs_overlay_qqq_proxy']['total_return_pct']}%，最大回撤 {out['baseline_vs_overlay_qqq_proxy']['max_dd_pct']}%",
          f"- 叠加层：总收益 {out['baseline_vs_overlay_qqq_proxy']['with_overlay_total_return_pct']}%，最大回撤 {out['baseline_vs_overlay_qqq_proxy']['with_overlay_max_dd_pct']}%\n",
          "### SOXX 代理（半导体/科技方向，用户实际痛点）\n",
          f"- 基准(v5)：总收益 {out['baseline_vs_overlay_soxx_proxy']['total_return_pct']}%，最大回撤 {out['baseline_vs_overlay_soxx_proxy']['max_dd_pct']}%",
          f"- 叠加层：总收益 {out['baseline_vs_overlay_soxx_proxy']['with_overlay_total_return_pct']}%，最大回撤 {out['baseline_vs_overlay_soxx_proxy']['with_overlay_max_dd_pct']}%\n",
          "## 2026-07 逐日（SOXX 20日回撤 → 旧/新预算）\n"]
    for d, dd, ob, nb in zip(out["july_dates"], out["july_soxx_dd20"],
                              out["july_baseline_budget"], out["july_overlay_budget"]):
        md.append(f"- {d}: SOXX_DD20={dd}, 旧={ob} → 新={nb}")
    (RESEARCH / "equity_overlay_backtest.md").write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
