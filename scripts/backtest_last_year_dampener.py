"""Last-12-months backtest of the LIVE kernel with the C-grade tech dampener.

Purpose
-------
Answer "run the last year with the newest code and show the effect". The daily
pipeline (backtest_regime_daily.py) does NOT feed ``tech_drawdown`` to the kernel
, so its budget is the dampener-dormant macro budget. This harness runs the SAME
real ``core.decision_kernel.decide`` TWICE per trading day:

  * baseline  : features WITHOUT tech_drawdown -> C-grade dampener dormant (== old behaviour)
  * dampened  : features WITH the real SOXX 20-day peak-to-trough drawdown -> the
                merged C-grade microstructural dampener fires (== newest code live path)

It then builds SOXX- and QQQ-proxy equity curves (budget = gross exposure to the
daily return) and reports total return / max drawdown for both, plus dampener
trigger counts. The 60-day warmup (ROC / z-score) is preserved by computing
features on the FULL frame and slicing to the last year afterwards.

Window: 2025-07-18 .. 2026-07-17 (last ~1 year of trading days).

Run:  python scripts/backtest_last_year_dampener.py
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
from scripts.backtest_regime_daily import build_daily_feature_frame

DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"

LAST_YEAR_START = pd.Timestamp("2025-07-18")
LAST_YEAR_END = pd.Timestamp("2026-07-17")


def _load_eq(ticker: str) -> pd.Series:
    p = DATA / f"_eq_{ticker}.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    s = pd.read_csv(p, parse_dates=["observation_date"]).dropna(subset=["close"])
    return s.set_index("observation_date")["close"].sort_index()


def trailing_drawdown(price: pd.Series, win: int = 20) -> pd.Series:
    """Peak-to-current drawdown over a trailing window (<=0)."""
    if price.empty:
        return pd.Series(dtype=float)
    roll_max = price.rolling(win, min_periods=max(5, win // 2)).max()
    return (price / roll_max - 1.0).clip(upper=0.0)


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
    """Mirror backtest_regime_daily: HY-exemption calm RISK_ON days score >=0.85."""
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


def max_dd(nav: np.ndarray) -> float:
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1.0).min())


def proxy_curve(budget: np.ndarray, ret: np.ndarray) -> np.ndarray:
    return np.cumprod(1.0 + budget * ret)


def main() -> None:
    # 1) Full feature frame (2024-10 -> 2026-07-17) preserves 60d warmup.
    frame = build_daily_feature_frame()

    # 2) Equity prices + SOXX trailing 20d drawdown aligned to the frame index.
    soxx = _load_eq("sox")
    qqq = _load_eq("qqq")
    soxx_dd_full = trailing_drawdown(soxx, 20).reindex(frame.index).ffill().bfill()
    soxx_ret_full = soxx.pct_change().reindex(frame.index).ffill().fillna(0.0)
    qqq_ret_full = qqq.pct_change().reindex(frame.index).ffill().fillna(0.0)

    # 3) Walk day-by-day, run kernel twice (baseline vs dampened), each with its
    #    own budget continuity so the velocity clamp is fair.
    rows = []
    prev_base = 0.0
    prev_damp = 0.0
    for date, row in frame.iterrows():
        feats = _build_features(row)
        dd = float(soxx_dd_full.loc[date])
        base = _decide_for_day(feats, prev_base, tech_dd=None)
        damp = _decide_for_day(feats, prev_damp, tech_dd=dd)
        prev_base = base["risk_budget"]
        prev_damp = damp["risk_budget"]
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "_ts": date,
            "rule_regime": base["rule_regime"],
            "effective_hard_regime": base["effective_hard_regime"],
            "vix": round(feats["vix"], 2),
            "hy_spread": round(feats["hy_credit_spread"], 0),
            "soxx_dd20": round(dd, 4),
            "base_budget": base["risk_budget"],
            "damp_budget": damp["risk_budget"],
            "dampener_active": damp["dampener_active"],
            "dampener_cap": damp["dampener_cap"],
            "soxx_ret": float(soxx_ret_full.loc[date]),
            "qqq_ret": float(qqq_ret_full.loc[date]),
        })

    df = pd.DataFrame(rows)

    # 4) Slice to the last year.
    ly = df[(df["_ts"] >= LAST_YEAR_START) & (df["_ts"] <= LAST_YEAR_END)].reset_index(drop=True)

    # 5) Proxy equity curves (budget = gross exposure to the daily return).
    soxx_r = ly["soxx_ret"].values
    qqq_r = ly["qqq_ret"].values
    base_soxx = proxy_curve(ly["base_budget"].values, soxx_r)
    damp_soxx = proxy_curve(ly["damp_budget"].values, soxx_r)
    base_qqq = proxy_curve(ly["base_budget"].values, qqq_r)
    damp_qqq = proxy_curve(ly["damp_budget"].values, qqq_r)

    def block(base_nav, damp_nav):
        return {
            "baseline_total_return_pct": round(float(base_nav[-1] - 1) * 100, 2),
            "baseline_max_dd_pct": round(max_dd(base_nav) * 100, 2),
            "dampened_total_return_pct": round(float(damp_nav[-1] - 1) * 100, 2),
            "dampened_max_dd_pct": round(max_dd(damp_nav) * 100, 2),
        }

    soxx_block = block(base_soxx, damp_soxx)
    qqq_block = block(base_qqq, damp_qqq)

    n = len(ly)
    risk_on_days = int((ly["rule_regime"] == "RISK_ON").sum())
    trig_days = int(ly["dampener_active"].sum())
    cap_counts = ly[ly["dampener_active"]]["dampener_cap"].value_counts().to_dict()
    # Days where dampened budget actually differs from baseline.
    diff_days = int((ly["damp_budget"] < ly["base_budget"] - 1e-9).sum())

    july = ly[ly["_ts"] >= "2026-07-01"]

    out = {
        "window": [str(LAST_YEAR_START.date()), str(LAST_YEAR_END.date())],
        "trading_days": n,
        "code_version": "live kernel + C-grade tech dampener (-0.13/-0.10/-0.07 -> 0.35/0.50/0.65)",
        "risk_on_days": risk_on_days,
        "dampener_active_days": trig_days,
        "dampener_active_pct": round(100.0 * trig_days / max(1, n), 1),
        "budget_lowered_days": diff_days,
        "dampener_cap_distribution": {str(k): int(v) for k, v in cap_counts.items()},
        "mean_base_budget": round(float(ly["base_budget"].mean()), 4),
        "mean_damp_budget": round(float(ly["damp_budget"].mean()), 4),
        "soxx_proxy": soxx_block,
        "qqq_proxy": qqq_block,
        "july_2026": {
            "dates": [d.strftime("%Y-%m-%d") for d in july["_ts"]],
            "soxx_dd20": [round(x, 4) for x in july["soxx_dd20"]],
            "base_budget": july["base_budget"].tolist(),
            "damp_budget": july["damp_budget"].tolist(),
        },
    }

    RESEARCH.mkdir(parents=True, exist_ok=True)

    # CSV (drop the helper timestamp column)
    csv_cols = ["date", "rule_regime", "effective_hard_regime", "vix", "hy_spread",
                "soxx_dd20", "base_budget", "damp_budget", "dampener_active",
                "dampener_cap", "soxx_ret", "qqq_ret"]
    with open(RESEARCH / "backtest_last_year_dampener.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols)
        w.writeheader()
        for _, r in ly.iterrows():
            w.writerow({c: r[c] for c in csv_cols})

    (RESEARCH / "backtest_last_year_dampener.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown
    md = [
        "# 最近一年回测：最新代码（含 C 档科技减震器） vs 基线\n",
        f"**窗口**：{out['window'][0]} ~ {out['window'][1]}（{n} 交易日）",
        f"**代码版本**：{out['code_version']}",
        "",
        "对比方法：同一套真实 kernel `decide()` 每日跑两遍——基线不注入 `tech_drawdown`（减震器休眠，"
        "等价旧行为），最新代码注入真实 SOXX 20 日峰值回撤（减震器 C 档激活）。净值=预算×代理日收益。",
        "",
        "## 减震器激活统计",
        "",
        f"- RISK_ON 交易日：{risk_on_days}",
        f"- 减震器激活（实际压低预算）：**{trig_days} 天（{out['dampener_active_pct']}%）**",
        f"- 档位命中分布：{out['dampener_cap_distribution']}（0.35=最深 / 0.50 / 0.65）",
        f"- 预算均值：基线 {out['mean_base_budget']} → 减震后 {out['mean_damp_budget']}",
        "",
        "## 代理净值曲线（预算=对市场代理的毛敞口）",
        "",
        "### SOXX 代理（半导体/科技方向，用户实际痛点）",
        "",
        f"- 基线：总收益 **{soxx_block['baseline_total_return_pct']}%**，最大回撤 **{soxx_block['baseline_max_dd_pct']}%**",
        f"- 最新代码：总收益 **{soxx_block['dampened_total_return_pct']}%**，最大回撤 **{soxx_block['dampened_max_dd_pct']}%**",
        "",
        "### QQQ 代理（广谱科技）",
        "",
        f"- 基线：总收益 **{qqq_block['baseline_total_return_pct']}%**，最大回撤 **{qqq_block['baseline_max_dd_pct']}%**",
        f"- 最新代码：总收益 **{qqq_block['dampened_total_return_pct']}%**，最大回撤 **{qqq_block['dampened_max_dd_pct']}%**",
        "",
        "## 2026-07 逐日（SOXX 20 日回撤 → 基线/减震后预算）",
        "",
    ]
    for d, dd, b, dp in zip(out["july_2026"]["dates"], out["july_2026"]["soxx_dd20"],
                            out["july_2026"]["base_budget"], out["july_2026"]["damp_budget"]):
        flag = " 🛡️" if dp < b - 1e-9 else ""
        md.append(f"- {d}: SOXX_DD20={dd*100:.1f}%, 基线={b} → 减震后={dp}{flag}")
    md += [
        "",
        "---",
        "*数据源：VIX/TIPS/名义10Y/HY = FRED 真实日线；DXY = FRED DTWEXBGS 重定基 ICE；"
        "SOXX/QQQ = yfinance 真实日线。减震器为 kernel 内 SOFT 层，绝对服从 HARD_VETO，未碰任何冻结分母参数。*",
        "*本文件由 AI 生成，仅供参考，不构成投资建议。*",
        "",
    ]
    (RESEARCH / "backtest_last_year_dampener.md").write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("\n[written] docs/research/backtest_last_year_dampener.{csv,json,md}")


if __name__ == "__main__":
    main()
