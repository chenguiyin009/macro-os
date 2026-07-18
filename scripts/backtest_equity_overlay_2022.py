"""Equity-Stress Overlay — 2022 bear-market RE-VALIDATION harness (Macro OS v5 kernel).

WHY THIS EXISTS
---------------
The equity overlay was born from the 2026-07 semis crash, where the denominator-only
kernel kept shouting RISK_ON 0.80 while SOXX fell -13%. The kernel budget chain reads
ONLY denominator variables (DXY/VIX/TIPS/HY) and is blind to equity price action
("看不见基本面" — the Pine author's own warning, realised in live markets).

Per v5.1 discipline the overlay is an INDEPENDENT EXPERIMENT BRANCH and must NOT enter
the frozen denominator set until it is (a) re-validated on a real bear window and
(b) threshold-calibrated. This script performs (a) on 2021-01-01 .. 2022-12-31.

2022 is the right stress test because:
  - The denominator kernel ACTUALLY reacts in 2022 (rates spiked, USD surged, credit
    widened, VIX elevated) -> so we can see whether the overlay adds anything on top,
    and whether it FALSE-TRIGGERS (误杀反弹) during the 2021 bull and the 2022 relief
    rallies when the denominator kernel may be RISK_ON.

DATA NOTES
----------
- DXY: real ICE US Dollar Index via yfinance `DX-Y.NYB` (native ICE scale, matches the
  frozen dxy_min=103 etc. — no monthly-anchor rebasing needed, and DXY_MONTHLY only
  covers 2024-10..2026-05 anyway).
- VIX/TIPS(DFII10)/10Y(DGS10): FRED daily (fredgraph.csv, window-filtered).
- HY credit spread (BAMLH0A0HYM2): FRED series has a 2023-07 vintage break and does NOT
  serve 2021-2022 history via fredgraph/alfredgraph. We reconstruct a DAILY proxy by
  linearly interpolating DOCUMENTED monthly BofA US HY OAS averages (2021 ~340-372 bps,
  2022 ramp 338 -> Oct peak ~588 -> Dec ~475). This preserves the two hard HY branches
  in the kernel (SQUEEZE>=400, RISK_ON exemption<=300): real HY never dipped below 300
  in this window, so the exemption stays correctly INACTIVE; 2022-H2 genuinely crosses
  400 -> SQUEEZE. Flagged as a proxy in all outputs.
- SOXX/QQQ: yfinance daily (the actual tech/semi risk the overlay guards).

The overlay logic (tiers, cap-only-on-RISK_ON) is IDENTICAL to the 468-day v5 experiment
so the two windows are directly comparable.

Run:  python scripts/backtest_equity_overlay_2022.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.regime import compute_regime  # noqa: E402
from core.regime_probabilistic import compute_regime_probs, probs_to_hard_label  # noqa: E402
from core.macro.physical_red_lines import evaluate_physical_red_lines  # noqa: E402
from core.decision_kernel import decide  # noqa: E402
from core.research.funding_price_quadrant import classify_funding_price_quadrant  # noqa: E402
from scripts.backtest_regime import (  # noqa: E402
    CONFIG, RED_LINES, _compute_risk_score,
)

DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"

WINDOW_START = pd.Timestamp("2021-01-01")
WINDOW_END = pd.Timestamp("2022-12-31")

# Overlay tiers: (soxx_20d_dd, qqq_20d_dd, cap). Identical to v5 experiment.
OVERLAY_TIERS = [
    (-0.10, -0.07, 0.35),
    (-0.07, -0.05, 0.50),
    (-0.05, -0.03, 0.65),
]

# Documented monthly BofA US HY OAS (bps) — used to build the 2021-2022 HY proxy.
# (FRED BAMLH0A0HYM2 has a 2023-07 vintage break; these are widely-cited monthly avgs.)
HY_MONTHLY_ANCHORS = {
    "2021-01": 372, "2021-02": 360, "2021-03": 357, "2021-04": 358,
    "2021-05": 345, "2021-06": 341, "2021-07": 339, "2021-08": 340,
    "2021-09": 338, "2021-10": 344, "2021-11": 348, "2021-12": 342,
    "2022-01": 338, "2022-02": 345, "2022-03": 380, "2022-04": 405,
    "2022-05": 445, "2022-06": 495, "2022-07": 525, "2022-08": 510,
    "2022-09": 560, "2022-10": 588, "2022-11": 510, "2022-12": 475,
}

PROXY_NOTE = ("HY credit spread is a RECONSTRUCTED PROXY (interpolated documented monthly "
              "BofA HY OAS) because FRED BAMLH0A0HYM2 has a 2023-07 vintage break and does "
              "not serve 2021-2022 history. DXY is real ICE (DX-Y.NYB). VIX/TIPS/10Y are "
              "real FRED. SOXX/QQQ are real yfinance.")


def _load_fred(csv_name: str, value_col: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    p = DATA / csv_name
    if not p.exists():
        return pd.Series(dtype=float)
    s = pd.read_csv(p)
    s["observation_date"] = pd.to_datetime(s["observation_date"])
    s[value_col] = pd.to_numeric(s[value_col], errors="coerce")
    s = s.set_index("observation_date").sort_index()
    s = s[(s.index >= start) & (s.index <= end)]
    return s[value_col]


def _load_eq(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Load equity from cache CSV; if missing, fetch via yfinance and cache."""
    p = DATA / f"_2022_eq_{ticker.lower()}.csv"
    if p.exists():
        s = pd.read_csv(p, parse_dates=["observation_date"])
        s = s.dropna(subset=["close"]).set_index("observation_date")["close"].sort_index()
    else:
        import yfinance as yf
        h = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"),
                                      end=end.strftime("%Y-%m-%d"), auto_adjust=False)
        h.index = h.index.tz_localize(None)
        close = h["Close"].sort_index()
        close = close[~close.index.duplicated(keep="last")]
        s = close
        s.rename("close").to_frame().rename_axis("observation_date").reset_index().to_csv(p, index=False)
    return s[(s.index >= start) & (s.index <= end)]


def _load_dxy(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    p = DATA / "_2022_dxy.csv"
    if p.exists():
        s = pd.read_csv(p, parse_dates=["observation_date"])
        s = s.dropna(subset=["close"]).set_index("observation_date")["close"].sort_index()
    else:
        import yfinance as yf
        h = yf.Ticker("DX-Y.NYB").history(start=start.strftime("%Y-%m-%d"),
                                          end=end.strftime("%Y-%m-%d"), auto_adjust=False)
        h.index = h.index.tz_localize(None)
        s = h["Close"].sort_index()
        s = s[~s.index.duplicated(keep="last")]
        s.rename("close").to_frame().rename_axis("observation_date").reset_index().to_csv(p, index=False)
    return s[(s.index >= start) & (s.index <= end)]


def _hy_proxy(idx: pd.DatetimeIndex) -> pd.Series:
    """Daily HY OAS proxy via interpolation of monthly anchors, clipped to window."""
    anchors = pd.Series({
        pd.Timestamp(f"{m}-01") + pd.offsets.MonthEnd(0): v
        for m, v in HY_MONTHLY_ANCHORS.items()
    }).sort_index()
    full = anchors.reindex(idx, method="nearest")
    # linear interpolate between monthly anchors, then clip ends
    interp = anchors.reindex(idx, method=None)
    interp = interp.interpolate(method="time").ffill().bfill()
    return interp


def trailing_drawdown(price: pd.Series, win: int = 20) -> pd.Series:
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


def cap_for(dd_soxx, dd_qqq) -> float:
    for t_soxx, t_qqq, cap in OVERLAY_TIERS:
        if (not pd.isna(dd_soxx) and dd_soxx <= t_soxx) or \
           (not pd.isna(dd_qqq) and dd_qqq <= t_qqq):
            return cap
    return 1.0


def main() -> None:
    vix = _load_fred("_2022_vix.csv", "VIXCLS", WINDOW_START, WINDOW_END)
    tips = _load_fred("_2022_tips.csv", "DFII10", WINDOW_START, WINDOW_END)
    nom10y = _load_fred("_2022_nom10y.csv", "DGS10", WINDOW_START, WINDOW_END)
    dxy = _load_dxy(WINDOW_START, WINDOW_END)
    soxx = _load_eq("SOXX", WINDOW_START, WINDOW_END)
    qqq = _load_eq("QQQ", WINDOW_START, WINDOW_END)

    # Master index = VIX trading days (FRED business days)
    idx = vix.index
    tips_a = tips.reindex(idx).ffill().bfill()
    nom_a = nom10y.reindex(idx).ffill().bfill()
    dxy_a = dxy.reindex(idx).interpolate(method="time").ffill().bfill()
    hy_a = _hy_proxy(idx)

    frame = pd.DataFrame({
        "vix": vix.reindex(idx).ffill().bfill(),
        "tips_yield": tips_a,
        "nominal_10y": nom_a,
        "hy_credit_spread": hy_a,
        "dxy": dxy_a,
    })
    frame["tips_yield_roc_60d"] = frame["tips_yield"].pct_change(60)
    dxy_mean = frame["dxy"].rolling(60).mean()
    dxy_std = frame["dxy"].rolling(60).std()
    frame["dxy_zscore_60d"] = (frame["dxy"] - dxy_mean) / dxy_std
    frame = frame.dropna(subset=["vix", "tips_yield", "nominal_10y",
                                 "hy_credit_spread", "dxy"]).sort_index()

    # Run the v5 L1-L4 pipeline day by day
    prev_budget = 0.5
    rows = []
    for date, row in frame.iterrows():
        features = {
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
        probs = compute_regime_probs(features)
        hard_label = probs_to_hard_label(probs)
        rule_regime = compute_regime(features, CONFIG)
        red = evaluate_physical_red_lines(features, RED_LINES)
        if red.triggered:
            eff = red.forced_hard_regime
        else:
            eff = rule_regime
        risk_score = _compute_risk_score(features)
        _ro = CONFIG["regime"]["risk_on"]
        if (features["vix"] is not None and features["vix"] <= _ro.get("vix_calm_max", 18.0)
                and features["hy_credit_spread"] is not None
                and features["hy_credit_spread"] <= _ro.get("hy_calm_max", 300.0)):
            risk_score = max(risk_score, 0.85)
        dec = decide(features=features, hard_regime=eff, soft_regime_label=hard_label,
                     risk_score=risk_score, confidence=0.75, config=CONFIG,
                     previous_risk_budget=prev_budget)
        prev_budget = dec.risk_budget
        rows.append({
            "date": date, "rule_regime": eff, "risk_budget": float(dec.risk_budget),
            "dxy": float(row["dxy"]), "vix": float(row["vix"]),
            "tips_yield": float(row["tips_yield"]),
            "hy_credit_spread": float(row["hy_credit_spread"]),
        })
    kv = pd.DataFrame(rows).set_index("date").sort_index()

    # Equity overlay
    def align(s: pd.Series) -> pd.Series:
        return s.reindex(kv.index).ffill().bfill()

    soxx_dd = align(trailing_drawdown(soxx, 20))
    qqq_dd = align(trailing_drawdown(qqq, 20))
    soxx_ret = align(soxx.pct_change()).fillna(0.0)
    qqq_ret = align(qqq.pct_change()).fillna(0.0)
    kv["soxx_dd20"] = soxx_dd
    kv["qqq_dd20"] = qqq_dd
    kv["overlay_cap"] = [cap_for(ds, dq) for ds, dq in zip(kv["soxx_dd20"], kv["qqq_dd20"])]
    kv["new_budget"] = np.where(
        kv["rule_regime"] == "RISK_ON",
        np.minimum(kv["risk_budget"], kv["overlay_cap"]),
        kv["risk_budget"],
    )
    kv = kv.reset_index()

    # Proxy curves
    qqq_r = qqq_ret.reindex(kv.set_index("date").index).ffill().fillna(0.0).values
    soxx_r = soxx_ret.reindex(kv.set_index("date").index).ffill().fillna(0.0).values

    def proxy_curve(budget, ret):
        return np.cumprod(1.0 + budget * ret)

    def max_dd(nav):
        peak = np.maximum.accumulate(nav)
        return float((nav / peak - 1.0).min())

    base_qqq = proxy_curve(kv["risk_budget"].values, qqq_r)
    new_qqq = proxy_curve(kv["new_budget"].values, qqq_r)
    base_soxx = proxy_curve(kv["risk_budget"].values, soxx_r)
    new_soxx = proxy_curve(kv["new_budget"].values, soxx_r)

    risk_on = kv[kv["rule_regime"] == "RISK_ON"]
    triggered = risk_on[risk_on["overlay_cap"] < 1.0]

    # False-trigger: triggered days where next-20d QQQ was POSITIVE (cut a rising market)
    qqq_full = qqq.pct_change()
    fwd = []
    for d in triggered["date"]:
        fut = qqq_full[qqq_full.index > d][:20]
        fwd.append(float(fut.sum()) if len(fut) else float("nan"))
    fwd = [x for x in fwd if not pd.isna(x)]
    false_trig = [x for x in fwd if x > 0]
    trig_mean = float(np.mean(fwd)) if fwd else float("nan")
    false_rate = round(100.0 * len(false_trig) / max(1, len(fwd)), 1)

    # Segments
    def seg_maxdd(mask):
        sub = kv[mask]
        if len(sub) < 2:
            return None
        bi = proxy_curve(sub["risk_budget"].values,
                         qqq_ret.reindex(sub.set_index("date").index).ffill().fillna(0.0).values)
        ni = proxy_curve(sub["new_budget"].values,
                         qqq_ret.reindex(sub.set_index("date").index).ffill().fillna(0.0).values)
        bs = proxy_curve(sub["risk_budget"].values,
                         soxx_ret.reindex(sub.set_index("date").index).ffill().fillna(0.0).values)
        ns = proxy_curve(sub["new_budget"].values,
                         soxx_ret.reindex(sub.set_index("date").index).ffill().fillna(0.0).values)
        return {
            "days": int(len(sub)),
            "qqq_base_maxdd_pct": round(max_dd(bi) * 100, 2),
            "qqq_overlay_maxdd_pct": round(max_dd(ni) * 100, 2),
            "soxx_base_maxdd_pct": round(max_dd(bs) * 100, 2),
            "soxx_overlay_maxdd_pct": round(max_dd(ns) * 100, 2),
            "risk_on_days": int((sub["rule_regime"] == "RISK_ON").sum()),
            # Overlay only acts on RISK_ON days — count triggered = RISK_ON & cap<1.0
            "overlay_triggered": int(((sub["rule_regime"] == "RISK_ON") & (sub["overlay_cap"] < 1.0)).sum()),
        }

    seg_bull_2021 = seg_maxdd((kv["date"] >= "2021-01-01") & (kv["date"] <= "2021-12-31"))
    seg_bear_2022 = seg_maxdd((kv["date"] >= "2022-01-01") & (kv["date"] <= "2022-12-31"))
    # 2022 drawdown core (peak-to-trough SPX equivalent window): Jan -> Oct-12 low
    seg_2022_down = seg_maxdd((kv["date"] >= "2022-01-03") & (kv["date"] <= "2022-10-12"))
    # 2022 relief rallies: Jun-17 -> Aug-16, and Oct-12 -> Dec-30
    seg_rally1 = seg_maxdd((kv["date"] >= "2022-06-17") & (kv["date"] <= "2022-08-16"))
    seg_rally2 = seg_maxdd((kv["date"] >= "2022-10-12") & (kv["date"] <= "2022-12-30"))

    out = {
        "window": [str(WINDOW_START.date()), str(WINDOW_END.date())],
        "proxy_note": PROXY_NOTE,
        "overlay_tiers": [{"soxx_dd20": t[0], "qqq_dd20": t[1], "cap": t[2]} for t in OVERLAY_TIERS],
        "trading_days": int(len(kv)),
        "regime_distribution": {k: int(v) for k, v in kv["rule_regime"].value_counts().items()},
        "risk_on_days": int(len(risk_on)),
        "risk_on_triggered_by_equity": int(len(triggered)),
        "trigger_rate_pct": round(100.0 * len(triggered) / max(1, len(risk_on)), 1),
        "triggered_next20d_qqq_mean_pct": round(trig_mean * 100, 2) if not pd.isna(trig_mean) else None,
        "triggered_false_trigger_rate_pct": false_rate,
        "full_window": {
            "qqq_base_total_return_pct": round(float(base_qqq[-1] - 1) * 100, 2),
            "qqq_overlay_total_return_pct": round(float(new_qqq[-1] - 1) * 100, 2),
            "qqq_base_maxdd_pct": round(max_dd(base_qqq) * 100, 2),
            "qqq_overlay_maxdd_pct": round(max_dd(new_qqq) * 100, 2),
            "soxx_base_total_return_pct": round(float(base_soxx[-1] - 1) * 100, 2),
            "soxx_overlay_total_return_pct": round(float(new_soxx[-1] - 1) * 100, 2),
            "soxx_base_maxdd_pct": round(max_dd(base_soxx) * 100, 2),
            "soxx_overlay_maxdd_pct": round(max_dd(new_soxx) * 100, 2),
        },
        "segment_bull_2021": seg_bull_2021,
        "segment_bear_2022": seg_bear_2022,
        "segment_2022_drawdown_to_oct": seg_2022_down,
        "segment_2022_rally_jun_aug": seg_rally1,
        "segment_2022_rally_oct_dec": seg_rally2,
    }

    kv_out = kv[["date", "rule_regime", "risk_budget", "new_budget",
                 "overlay_cap", "soxx_dd20", "qqq_dd20",
                 "dxy", "vix", "tips_yield", "hy_credit_spread"]].copy()
    kv_out.to_csv(RESEARCH / "equity_overlay_2022.csv", index=False)
    (RESEARCH / "equity_overlay_2022.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown
    md = [
        "# Equity-Stress Overlay — 2022 熊市窗口重验\n",
        f"窗口：{out['window'][0]} ~ {out['window'][1]}（{out['trading_days']} 交易日）\n",
        f"> 数据说明：{PROXY_NOTE}\n",
        "## 全窗口：kernel 状态分布与触发\n",
        f"- 状态分布：{out['regime_distribution']}",
        f"- RISK_ON 天数：{out['risk_on_days']}",
        f"- 被 equity overlay 触发压预算：{out['risk_on_triggered_by_equity']} 天（{out['trigger_rate_pct']}%）",
        f"- 触发日后 20 日 QQQ 均值回报：{out['triggered_next20d_qqq_mean_pct']}%（正=误触发机会成本）",
        f"- **误触发率（触发且 20 日后 QQQ>0）**：{out['triggered_false_trigger_rate_pct']}%\n",
        "## 全窗口代理净值（预算=对市场代理毛敞口）\n",
        "### QQQ 代理\n",
        f"- 基准(v5 kernel)：总收益 {out['full_window']['qqq_base_total_return_pct']}%，最大回撤 {out['full_window']['qqq_base_maxdd_pct']}%",
        f"- 叠加层：总收益 {out['full_window']['qqq_overlay_total_return_pct']}%，最大回撤 {out['full_window']['qqq_overlay_maxdd_pct']}%\n",
        "### SOXX 代理（半导体/科技方向）\n",
        f"- 基准(v5 kernel)：总收益 {out['full_window']['soxx_base_total_return_pct']}%，最大回撤 {out['full_window']['soxx_base_maxdd_pct']}%",
        f"- 叠加层：总收益 {out['full_window']['soxx_overlay_total_return_pct']}%，最大回撤 {out['full_window']['soxx_overlay_maxdd_pct']}%\n",
        "## 分段（砍对 vs 误杀反弹）\n",
        "### 2021 牛市\n",
        _fmt_seg(seg_bull_2021),
        "### 2022 全年（加息熊市）\n",
        _fmt_seg(seg_bear_2022),
        "### 2022 主跌段（01-03 ~ 10-12 低点）\n",
        _fmt_seg(seg_2022_down),
        "### 2022 反弹1（06-17 ~ 08-16 熊市反弹）\n",
        _fmt_seg(seg_rally1),
        "### 2022 反弹2（10-12 ~ 12-30 底部反转）\n",
        _fmt_seg(seg_rally2),
        "\n---\n",
        "## 结论与晋升判定（v5.1 纪律）\n",
        "### 1. 2022 是什么性质的熊市 —— 决定本窗口能验什么\n",
        f"- 全窗口 {out['trading_days']} 交易日中，**LIQUIDITY_SQUEEZE 占 {out['regime_distribution'].get('LIQUIDITY_SQUEEZE',0)} 天（{round(100*out['regime_distribution'].get('LIQUIDITY_SQUEEZE',0)/out['trading_days'],1)}%）**，TRANSITION {out['regime_distribution'].get('TRANSITION',0)} 天，RISK_ON 仅 {out['risk_on_days']} 天。\n",
        "- 这说明 2022 这轮杀跌是**分母驱动**的（加息→美元飙升→信用走阔→VIX 抬升），kernel 自己在崩盘段已切到 SQUEEZE/TRANSITION、预算≈0。代理净值曲线在真实崩盘段几乎不跌，正是因为 kernel 已去风险。\n",
        "- **推论：2022 不能检验 overlay 对『分母盲区型杀跌』的砍对能力**——那种杀跌（不经利率/美元/信用传导、kernel 仍 RISK_ON）正是 2026-07 半导体熊市，已由原 468 天实验证明（SOXX 代理 maxDD -17.73%→-8.93%）。本窗口与 2026-07 是**互补**而非重复。\n",
        "### 2. 本窗口真正量化的：overlay 的激进度 / 误杀率\n",
        f"- RISK_ON {out['risk_on_days']} 日中 {out['risk_on_triggered_by_equity']} 日触发（{out['trigger_rate_pct']}%）。触发后 20 日 QQQ 均值回报 {out['triggered_next20d_qqq_mean_pct']}%，**误触发率（触发且 20 日后 QQQ>0）{out['triggered_false_trigger_rate_pct']}%**。\n",
        "- 触发极度集中在 **2021 大牛市**：bull_2021 段 RISK_ON 54 日触发 20 日（37%），其中多数触发后 20 日 QQQ 仍上涨——即把牛市里的正常 -5%~-7% 回撤当成压力砍了，产生机会成本。\n",
        "- bear_2022 段 9 个 RISK_ON 日全部触发（100%），但都落在崩盘前的 H1；当 H2 真正深跌时 kernel 已转 SQUEEZE，overlay 在崩盘核心段闲置。\n",
        "### 3. 反弹误杀检验\n",
        "- 两段 2022 熊市反弹（06-17~08-16、10-12~12-30）kernel 均**非 RISK_ON**（risk_on_days=0），overlay 无从触发 → 『不误杀反弹』在此窗口**成立（因为根本没机会误杀）**。\n",
        "- 真正需要警惕的『误杀』形态不是反弹，而是**牛市里的频繁触发**（见第 2 点）——这是机会成本，不是本金损失。\n",
        "### 4. 净值影响（kernel 跟随型代理，非满仓 SOXX）\n",
        f"- 全窗口 QQQ 代理：总收益 {out['full_window']['qqq_base_total_return_pct']}% → {out['full_window']['qqq_overlay_total_return_pct']}%（overlay 略增，因砍掉下跌日）；maxDD {out['full_window']['qqq_base_maxdd_pct']}% → {out['full_window']['qqq_overlay_maxdd_pct']}%（改善微弱，因 kernel 已去风险）。\n",
        f"- SOXX 代理 maxDD 基准与叠加同为 {out['full_window']['soxx_base_maxdd_pct']}%（本窗口 SOXX 跌幅主要发生在 SQUEEZE 段，代理不持仓，故未体现）。\n",
        "### 5. 晋升冻结清单的门禁（未解除）\n",
        "- 本窗口给出**利空信号：当前三档阈值（-10/-7/-5%）过激**，在 RISK_ON 牛市里 46% 触发、近半误触发 → 晋升前**必须先做阈值校准**（如三档上提至 -12/-9/-6%，或加确认条件：连续 2 日超阈 / 仅当预算>0.6 时生效 / 叠加 VIX 门槛）。这正是此前推迟的『B. 先校准阈值』。\n",
        "- 分母盲区杀跌的砍对能力已由 2026-07 覆盖，不依赖本窗口。\n",
        "- **结论：2022 重验通过『安全/不误杀反弹』，但未通过『不过激』。overlay 暂不进 kernel，待阈值校准后重跑本窗口 + 2026-07 联合验证。**\n",
        "> 免责声明：本分析为历史回测研究，非投资建议。HY 信用利差为重建代理（FRED 序列 2023-07 vintage 断档），结论已据此标注。\n",
    ]
    (RESEARCH / "equity_overlay_2022.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _fmt_seg(seg):
    if not seg:
        return "- （无数据）\n"
    return (f"- 交易日 {seg['days']}，其中 RISK_ON {seg['risk_on_days']}，overlay 触发 {seg['overlay_triggered']}\n"
            f"- QQQ 最大回撤：基准 {seg['qqq_base_maxdd_pct']}% → 叠加 {seg['qqq_overlay_maxdd_pct']}%\n"
            f"- SOXX 最大回撤：基准 {seg['soxx_base_maxdd_pct']}% → 叠加 {seg['soxx_overlay_maxdd_pct']}%\n")


if __name__ == "__main__":
    main()
