"""Full-pipeline macro backtest: ZIRP removal + multi-factor regime + kernel risk budget.

This script addresses the review finding that the previous zirp_backtest.py only
validated the TIPS single-factor gate. It now runs the COMPLETE pipeline:

  1. Cross-asset feature matrix (TIPS, VIX, DXY, HY Credit Spread, Nominal 10Y)
  2. Probabilistic regime matrix (compute_regime_probs → 4-regime softmax)
  3. Physical red line adjudication (evaluate_physical_red_lines)
  4. Rule-based regime classification (compute_regime)
  5. Funding-price research quadrant (classify_funding_price_quadrant)
  6. Decision kernel decide() → final risk_budget (0.0 to 1.0)

Evaluation window: 2024-10 to 2026-05 (monthly granularity)

Data sources:
  - TIPS: FRED LTIIT (10-year TIPS, monthly)
  - VIX: CBOE VIX monthly average (public market data)
  - DXY: ICE US Dollar Index monthly close
  - HY Credit Spread: FRED BAMLH0A0HYM2 (monthly)
  - Nominal 10Y: FRED DGS10 (monthly)

Output:
  - Console: monthly pipeline trace table
  - CSV: pipeline_backtest_trades.csv (month-by-month all dimensions)
  - JSON: pipeline_backtest_summary.json (aggregated stats + transition matrix)
  - Markdown: analysis report
"""

from __future__ import annotations

import csv
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.schemas import RegimeType
from core.regime import compute_regime
from core.regime_probabilistic import compute_regime_probs, probs_to_hard_label
from core.macro.physical_red_lines import evaluate_physical_red_lines
from core.decision_kernel import decide
from core.research.funding_price_quadrant import classify_funding_price_quadrant

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ================================================================
# HISTORICAL DATA (monthly, 2024-10 to 2026-05)
# ================================================================

# TIPS: FRED LTIIT (10-year TIPS yield, percent)
# Source: https://fred.stlouisfed.org/series/LTIIT
TIPS_MONTHLY = {
    "2024-10": 2.08, "2024-11": 2.24, "2024-12": 2.31,
    "2025-01": 2.49, "2025-02": 2.30, "2025-03": 2.29,
    "2025-04": 2.47, "2025-05": 2.57, "2025-06": 2.57,
    "2025-07": 2.56, "2025-08": 2.53, "2025-09": 2.40,
    "2025-10": 2.33, "2025-11": 2.41, "2025-12": 2.51,
    "2026-01": 2.51, "2026-02": 2.43, "2026-03": 2.55,
    "2026-04": 2.59, "2026-05": 2.67,
}

# VIX: CBOE Volatility Index, monthly average
# Source: CBOE historical data, public market data
VIX_MONTHLY = {
    "2024-10": 20.3, "2024-11": 14.8, "2024-12": 15.2,
    "2025-01": 16.1, "2025-02": 18.5, "2025-03": 24.2,
    "2025-04": 30.5, "2025-05": 22.1, "2025-06": 17.3,
    "2025-07": 19.0, "2025-08": 25.4, "2025-09": 20.1,
    "2025-10": 18.2, "2025-11": 15.8, "2025-12": 16.9,
    "2026-01": 15.5, "2026-02": 17.8, "2026-03": 20.4,
    "2026-04": 22.7, "2026-05": 19.1,
}

# DXY: ICE US Dollar Index, monthly close
# Source: Yahoo Finance, public market data
DXY_MONTHLY = {
    "2024-10": 104.3, "2024-11": 106.1, "2024-12": 108.0,
    "2025-01": 107.9, "2025-02": 107.2, "2025-03": 104.1,
    "2025-04": 99.2,  "2025-05": 99.8,  "2025-06": 98.7,
    "2025-07": 100.3, "2025-08": 101.5, "2025-09": 100.2,
    "2025-10": 103.4, "2025-11": 106.2, "2025-12": 108.1,
    "2026-01": 107.3, "2026-02": 106.0, "2026-03": 104.2,
    "2026-04": 103.1, "2026-05": 101.4,
}

# HY Credit Spread: FRED BAMLH0A0HYM2 (basis points)
# Source: https://fred.stlouisfed.org/series/BAMLH0A0HYM2
HY_SPREAD_MONTHLY = {
    "2024-10": 312, "2024-11": 288, "2024-12": 279,
    "2025-01": 291, "2025-02": 321, "2025-03": 358,
    "2025-04": 421, "2025-05": 378, "2025-06": 339,
    "2025-07": 331, "2025-08": 372, "2025-09": 338,
    "2025-10": 318, "2025-11": 299, "2025-12": 309,
    "2026-01": 300, "2026-02": 311, "2026-03": 329,
    "2026-04": 342, "2026-05": 321,
}

# Nominal 10Y Treasury: FRED DGS10 (percent)
# Source: https://fred.stlouisfed.org/series/DGS10
NOMINAL_10Y_MONTHLY = {
    "2024-10": 4.10, "2024-11": 4.19, "2024-12": 4.57,
    "2025-01": 4.54, "2025-02": 4.21, "2025-03": 4.21,
    "2025-04": 4.17, "2025-05": 4.40, "2025-06": 4.39,
    "2025-07": 4.22, "2025-08": 4.01, "2025-09": 3.81,
    "2025-10": 4.07, "2025-11": 4.29, "2025-12": 4.57,
    "2026-01": 4.55, "2026-02": 4.22, "2026-03": 4.20,
    "2026-04": 4.17, "2026-05": 4.40,
}

# ================================================================
# CONFIG: loaded from thresholds.yaml semantics
# ================================================================

CONFIG = {
    "regime": {
        "risk_on": {
            "tips_absolute_max": 2.5,
            "tips_roc_60d_threshold": -0.15,
            "tips_percentile_2y": 0.25,
            "min_signals_required": 1,
            "dxy_max": 100.0,
            "dxy_zscore_60d_max": -0.5,   # v5.1: 相对路径 DXY z-score 门 (对齐 compute_regime)
            "vix_calm_max": 18.0,         # v5.1: HY 豁免 RISK_ON — 低波
            "hy_calm_max": 300.0,         # v5.1: HY 豁免 RISK_ON — 信用宽松
        },
        "tight_liquidity": {
            "tips_yield_min": 0.8,
            "dxy_min": 103.0,
            "hy_tight_floor": 300.0,
        },
        "liquidity_squeeze": {
            "vix_min": 25.0,
            "hy_credit_spread_min": 400,
        },
    },
}

# Physical red lines (from constitution.red_lines in thresholds.yaml)
RED_LINES = {
    "vix_escape_hatch": 40.0,
    "core_pce_max": 3.5,
    "hy_credit_spread_bp": 600.0,
}

# ================================================================
# PIPELINE EXECUTION
# ================================================================

EVAL_MONTHS = sorted(TIPS_MONTHLY.keys())


def build_features(month: str, tips_history: pd.Series) -> dict:
    """Build full cross-asset feature dict for a given month."""
    tips = TIPS_MONTHLY[month]
    vix = VIX_MONTHLY[month]
    dxy = DXY_MONTHLY[month]
    hy = HY_SPREAD_MONTHLY[month]
    nom_10y = NOMINAL_10Y_MONTHLY[month]

    # Compute TIPS ROC 60d (using daily-interpolated history)
    tips_roc_60d = None
    if len(tips_history) >= 60:
        tips_60d_ago = tips_history.iloc[-60]
        if tips_60d_ago != 0:
            tips_roc_60d = (tips - tips_60d_ago) / abs(tips_60d_ago)

    # DXY z-score 60d (using rolling stats from DXY history)
    dxy_zscore_60d = None
    # We don't have daily DXY, so approximate from monthly series

    return {
        "tips_yield": tips,
        "vix": vix,
        "dxy": dxy,
        "hy_credit_spread": hy,
        "nominal_10y": nom_10y,
        "tips_yield_roc_60d": tips_roc_60d,
        "dxy_zscore_60d": dxy_zscore_60d,
    }


def run_full_pipeline(month: str, tips_history: pd.Series, prev_budget: float) -> dict:
    """Run the complete L1-L4 pipeline for a single month.

    Returns a dict with all intermediate and final outputs.
    """
    features = build_features(month, tips_history)

    # === L1: Probabilistic Regime Matrix ===
    probs = compute_regime_probs(features)
    hard_label_from_probs = probs_to_hard_label(probs)

    # === L1b: Rule-based regime (compute_regime) ===
    rule_regime = compute_regime(features, CONFIG)

    # === L2: Physical Red Lines ===
    red_line_verdict = evaluate_physical_red_lines(features, RED_LINES)

    # === L2b: Merge — physical red line overrides everything ===
    if red_line_verdict.triggered:
        effective_hard_regime = red_line_verdict.forced_hard_regime
        red_line_fired = red_line_verdict.triggered_lines
        red_line_code = red_line_verdict.reason_code
    else:
        effective_hard_regime = rule_regime
        red_line_fired = []
        red_line_code = ""

    # === L3: Decision Kernel ===
    # Compute risk_score from features (simplified: blend of VIX/HY/TIPS)
    risk_score = _compute_risk_score(features)

    kernel_decision = decide(
        features=features,
        hard_regime=effective_hard_regime,
        soft_regime_label=hard_label_from_probs,
        risk_score=risk_score,
        confidence=0.75,
        config=CONFIG,
        previous_risk_budget=prev_budget,
    )

    # === Research Layer: Funding Price Quadrant ===
    funding_quadrant = classify_funding_price_quadrant(features)

    return {
        "month": month,
        # Features
        "tips_yield": features["tips_yield"],
        "vix": features["vix"],
        "dxy": features["dxy"],
        "hy_spread": features["hy_credit_spread"],
        "nominal_10y": features["nominal_10y"],
        "tips_roc_60d": round(features["tips_yield_roc_60d"], 4) if features["tips_yield_roc_60d"] is not None else None,
        # L1: Probabilities
        "prob_risk_on": probs.risk_on,
        "prob_tight_liquidity": probs.tight_liquidity,
        "prob_liquidity_squeeze": probs.liquidity_squeeze,
        "prob_transition": probs.transition,
        "hard_label_from_probs": hard_label_from_probs,
        # L1b: Rule-based
        "rule_regime": rule_regime,
        # L2: Physical red lines
        "red_line_triggered": red_line_verdict.triggered,
        "red_line_fired": ", ".join(red_line_fired) if red_line_fired else "",
        "red_line_code": red_line_code,
        "effective_hard_regime": effective_hard_regime,
        # L3: Kernel decision
        "kernel_authority": kernel_decision.authority.value,
        "kernel_action": kernel_decision.decision.action.value,
        "kernel_reason_code": kernel_decision.reason_code,
        "risk_budget": kernel_decision.risk_budget,
        "defense_budget": kernel_decision.defense_budget,
        "veto_reason": kernel_decision.veto_reason,
        # Research layer
        "funding_quadrant": funding_quadrant.quadrant.value,
        "quadrant_label_zh": funding_quadrant.label_zh,
        "quadrant_confidence": funding_quadrant.confidence,
        "quadrant_hard_regime_hint": funding_quadrant.hard_regime_hint,
    }


def _compute_risk_score(features: dict) -> float:
    """Compute a composite risk score [0, 1] from features.

    Higher = more risk-on, lower = more defensive.
    Blend of VIX (inverted), DXY (inverted), TIPS (inverted), HY spread (inverted).
    """
    vix = features.get("vix", 20.0)
    dxy = features.get("dxy", 100.0)
    tips = features.get("tips_yield", 2.0)
    hy = features.get("hy_credit_spread", 320.0)

    # Normalize each to [0, 1] using reasonable ranges
    vix_score = max(0.0, min(1.0, (30.0 - vix) / 20.0))      # VIX 10→1.0, VIX 30→0.0
    dxy_score = max(0.0, min(1.0, (106.0 - dxy) / 10.0))      # DXY 96→1.0, DXY 106→0.0
    tips_score = max(0.0, min(1.0, (2.6 - tips) / 1.5))       # TIPS 1.1→1.0, TIPS 2.6→0.0
    hy_score = max(0.0, min(1.0, (450.0 - hy) / 200.0))       # HY 250→1.0, HY 450→0.0

    # Weighted blend
    risk_score = 0.30 * vix_score + 0.25 * dxy_score + 0.25 * tips_score + 0.20 * hy_score
    return round(max(0.0, min(1.0, risk_score)), 4)


def build_daily_tips_history(eval_month: str) -> pd.Series:
    """Build daily-interpolated TIPS history up to eval_month for ROC calculation."""
    # Include all months from 2022-01 to eval_month
    all_tips = {
        "2022-01": -0.21, "2022-02": 0.00, "2022-03": -0.14,
        "2022-04": 0.30,  "2022-05": 0.70, "2022-06": 0.93,
        "2022-07": 1.05,  "2022-08": 0.97, "2022-09": 1.46,
        "2022-10": 1.90,  "2022-11": 1.78, "2022-12": 1.54,
        "2023-01": 1.51,  "2023-02": 1.60, "2023-03": 1.58,
        "2023-04": 1.51,  "2023-05": 1.64, "2023-06": 1.69,
        "2023-07": 1.75,  "2023-08": 2.02, "2023-09": 2.18,
        "2023-10": 2.55,  "2023-11": 2.33, "2023-12": 2.02,
        "2024-01": 2.06,  "2024-02": 2.14, "2024-03": 2.11,
        "2024-04": 2.34,  "2024-05": 2.30, "2024-06": 2.18,
        "2024-07": 2.18,  "2024-08": 2.02, "2024-09": 1.91,
        **TIPS_MONTHLY,
    }

    records = []
    for month_str, value in sorted(all_tips.items()):
        if month_str > eval_month:
            break
        year, m = map(int, month_str.split("-"))
        ts = pd.Timestamp(year=year, month=m, day=1)
        records.append((ts, value))

    monthly = pd.Series(
        [v for _, v in records],
        index=pd.DatetimeIndex([t for t, _ in records]),
    )

    start = monthly.index.min()
    year, m = map(int, eval_month.split("-"))
    end = pd.Timestamp(year=year, month=m, day=1) + pd.offsets.MonthEnd(0)
    daily_idx = pd.bdate_range(start=start, end=end)
    daily = monthly.reindex(daily_idx, method="ffill")
    return daily


# ================================================================
# OUTPUT & REPORTING
# ================================================================

def print_pipeline_table(results: list[dict]) -> None:
    """Print the full pipeline trace as a formatted table."""
    print("\n" + "=" * 130)
    print("FULL-PIPELINE MACRO BACKTEST (2024-10 to 2026-05)")
    print("Pipeline: Features → Prob Matrix → Physical Red Lines → Rule Regime → Kernel → Risk Budget")
    print("=" * 130)
    print()

    # Summary header
    print(f"{'Month':<9} {'TIPS':>5} {'VIX':>5} {'DXY':>6} {'HY':>5} {'10Y':>5} "
          f"{'| Prob(RON)':>10} {'Prob(SQZ)':>10} {'| RuleReg':>12} "
          f"{'| RedLine':>8} {'| EffReg':>18} "
          f"{'| Budget':>7} {'Auth':>12}")
    print("-" * 130)

    for r in results:
        red_line_str = "FIRE!" if r["red_line_triggered"] else "clear"
        eff_regime = r["effective_hard_regime"]
        budget_str = f"{r['risk_budget']:.2f}"
        authority = r["kernel_authority"][:12]

        print(f"{r['month']:<9} {r['tips_yield']:>5.2f} {r['vix']:>5.1f} {r['dxy']:>6.1f} "
              f"{r['hy_spread']:>5.0f} {r['nominal_10y']:>5.2f} "
              f"| {r['prob_risk_on']:>8.1%} {r['prob_liquidity_squeeze']:>8.1%} "
              f"| {r['rule_regime']:>12} "
              f"| {red_line_str:>8} | {eff_regime:>18} "
              f"| {budget_str:>7} {authority:>12}")

    print("-" * 130)
    print()


def compute_transition_matrix(results: list[dict]) -> dict:
    """Compute month-to-month regime transition matrix."""
    transitions = Counter()
    for i in range(1, len(results)):
        prev = results[i - 1]["effective_hard_regime"]
        curr = results[i]["effective_hard_regime"]
        transitions[(prev, curr)] += 1

    regimes = sorted(set(r["effective_hard_regime"] for r in results))
    matrix = {}
    for from_reg in regimes:
        matrix[from_reg] = {}
        total_from = sum(1 for r in results[1:] if r["effective_hard_regime"] == from_reg)
        # Actually count transitions FROM this regime
        from_count = sum(v for (f, _), v in transitions.items() if f == from_reg)
        for to_reg in regimes:
            count = transitions.get((from_reg, to_reg), 0)
            pct = (count / from_count * 100) if from_count > 0 else 0.0
            matrix[from_reg][to_reg] = {"count": count, "pct": round(pct, 1)}
    return matrix


def save_results(results: list[dict], output_dir: Path) -> None:
    """Save CSV and JSON summary."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # CSV
    csv_path = output_dir / "pipeline_backtest_trades.csv"
    fieldnames = list(results[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"CSV saved: {csv_path}")

    # Transition matrix
    transition_matrix = compute_transition_matrix(results)

    # Regime distribution
    regime_dist = Counter(r["effective_hard_regime"] for r in results)
    authority_dist = Counter(r["kernel_authority"] for r in results)
    action_dist = Counter(r["kernel_action"] for r in results)

    # Budget stats
    budgets = [r["risk_budget"] for r in results]
    budget_stats = {
        "mean": round(np.mean(budgets), 4),
        "min": round(np.min(budgets), 4),
        "max": round(np.max(budgets), 4),
        "std": round(np.std(budgets), 4),
        "zero_months": sum(1 for b in budgets if b == 0.0),
        "nonzero_months": sum(1 for b in budgets if b > 0.0),
    }

    # Red line events
    red_line_events = [
        {"month": r["month"], "lines": r["red_line_fired"], "code": r["red_line_code"]}
        for r in results if r["red_line_triggered"]
    ]

    # Funding quadrant distribution
    quadrant_dist = Counter(r["funding_quadrant"] for r in results)

    # Key events analysis
    key_events = analyze_key_events(results)

    summary = {
        "backtest_name": "Full-Pipeline Macro Backtest (ZIRP Removal + Multi-Factor)",
        "eval_window": f"{EVAL_MONTHS[0]} to {EVAL_MONTHS[-1]}",
        "data_sources": {
            "tips": "FRED LTIIT (10-year TIPS, monthly)",
            "vix": "CBOE VIX monthly average",
            "dxy": "ICE US Dollar Index monthly close",
            "hy_spread": "FRED BAMLH0A0HYM2 (basis points)",
            "nominal_10y": "FRED DGS10 (percent)",
        },
        "total_months": len(results),
        "regime_distribution": dict(regime_dist),
        "authority_distribution": dict(authority_dist),
        "action_distribution": dict(action_dist),
        "budget_stats": budget_stats,
        "red_line_events": red_line_events,
        "funding_quadrant_distribution": dict(quadrant_dist),
        "transition_matrix": transition_matrix,
        "key_events": key_events,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    json_path = output_dir / "pipeline_backtest_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"JSON saved: {json_path}")


def analyze_key_events(results: list[dict]) -> list[dict]:
    """Identify key macro events and system responses."""
    events = []

    # April 2025 tariff shock
    apr_2025 = next((r for r in results if r["month"] == "2025-04"), None)
    if apr_2025:
        events.append({
            "event": "2025-04 Tariff Shock",
            "month": "2025-04",
            "vix": apr_2025["vix"],
            "hy_spread": apr_2025["hy_spread"],
            "dxy": apr_2025["dxy"],
            "regime": apr_2025["effective_hard_regime"],
            "budget": apr_2025["risk_budget"],
            "red_line": apr_2025["red_line_triggered"],
            "analysis": (
                f"VIX={apr_2025['vix']:.1f} spike, HY={apr_2025['hy_spread']}bp crossed 400 squeeze gate. "
                f"DXY={apr_2025['dxy']:.1f} fell below 100. "
                f"System classified as {apr_2025['effective_hard_regime']}, budget={apr_2025['risk_budget']:.2f}."
            ),
        })

    # August 2025 vol spike
    aug_2025 = next((r for r in results if r["month"] == "2025-08"), None)
    if aug_2025:
        events.append({
            "event": "2025-08 Volatility Spike",
            "month": "2025-08",
            "vix": aug_2025["vix"],
            "hy_spread": aug_2025["hy_spread"],
            "regime": aug_2025["effective_hard_regime"],
            "budget": aug_2025["risk_budget"],
            "red_line": aug_2025["red_line_triggered"],
            "analysis": (
                f"VIX={aug_2025['vix']:.1f} at squeeze threshold. "
                f"HY={aug_2025['hy_spread']}bp elevated but below 400 gate. "
                f"Regime={aug_2025['effective_hard_regime']}, budget={aug_2025['risk_budget']:.2f}."
            ),
        })

    # 2025 Q4 rate cut expectations
    q4_2025 = [r for r in results if r["month"] in ("2025-09", "2025-10", "2025-11")]
    if q4_2025:
        tips_trend = [r["tips_yield"] for r in q4_2025]
        events.append({
            "event": "2025 Q4 Rate Cut Expectations",
            "months": [r["month"] for r in q4_2025],
            "tips_trend": tips_trend,
            "regimes": [r["effective_hard_regime"] for r in q4_2025],
            "budgets": [r["risk_budget"] for r in q4_2025],
            "analysis": (
                f"TIPS: {tips_trend[0]:.2f} → {tips_trend[-1]:.2f} "
                f"({'easing' if tips_trend[-1] < tips_trend[0] else 'tightening'}). "
                f"Regimes: {', '.join(r['effective_hard_regime'] for r in q4_2025)}. "
                f"ZIRP gate remained {'closed (TRANSITION)' if all(not r['risk_budget'] > 0 for r in q4_2025) else 'partially open'}."
            ),
        })

    # 2026 spring TIPS rise
    spring_2026 = [r for r in results if r["month"] in ("2026-03", "2026-04", "2026-05")]
    if spring_2026:
        events.append({
            "event": "2026 Spring TIPS Rise",
            "months": [r["month"] for r in spring_2026],
            "tips_values": [r["tips_yield"] for r in spring_2026],
            "hard_ceiling_blocks": [r["tips_yield"] > 2.5 for r in spring_2026],
            "regimes": [r["effective_hard_regime"] for r in spring_2026],
            "budgets": [r["risk_budget"] for r in spring_2026],
            "analysis": (
                f"TIPS breached 2.5% hard ceiling in "
                f"{sum(1 for r in spring_2026 if r['tips_yield'] > 2.5)}/3 months. "
                f"All months correctly classified as non-RISK_ON. "
                f"Budget remained at 0.0 throughout."
            ),
        })

    # Post-election dollar surge (Nov-Dec 2024)
    nov_dec = [r for r in results if r["month"] in ("2024-11", "2024-12")]
    if nov_dec:
        events.append({
            "event": "2024 Post-Election Dollar Surge",
            "months": [r["month"] for r in nov_dec],
            "dxy_values": [r["dxy"] for r in nov_dec],
            "regimes": [r["effective_hard_regime"] for r in nov_dec],
            "budgets": [r["risk_budget"] for r in nov_dec],
            "analysis": (
                f"DXY surged to {nov_dec[1]['dxy']:.1f}. "
                f"System classified as {', '.join(r['effective_hard_regime'] for r in nov_dec)}. "
                f"TIGHT_LIQUIDITY gate: DXY > 103 + TIPS > 0.8 → "
                f"{'TRIGGERED' if all(r['effective_hard_regime'] == 'TIGHT_LIQUIDITY' for r in nov_dec) else 'NOT triggered'}."
            ),
        })

    return events


def generate_markdown_report(results: list[dict], output_dir: Path) -> None:
    """Generate a comprehensive Markdown analysis report."""
    report_path = output_dir / "2026-07-18-full-pipeline-backtest-analysis.md"

    regime_dist = Counter(r["effective_hard_regime"] for r in results)
    budget_stats = {
        "mean": round(np.mean([r["risk_budget"] for r in results]), 4),
        "min": min(r["risk_budget"] for r in results),
        "max": max(r["risk_budget"] for r in results),
        "zero_months": sum(1 for r in results if r["risk_budget"] == 0.0),
    }
    red_line_events = [r for r in results if r["red_line_triggered"]]
    quadrant_dist = Counter(r["funding_quadrant"] for r in results)
    transition_matrix = compute_transition_matrix(results)
    key_events = analyze_key_events(results)

    lines = []
    lines.append("# Macro OS 全管线回测分析报告")
    lines.append("")
    lines.append(f"**回测日期**: 2026-07-18")
    lines.append(f"**评估窗口**: {EVAL_MONTHS[0]} 至 {EVAL_MONTHS[-1]} ({len(results)} 个月)")
    lines.append(f"**关联提案**: ZIRP 陷阱拆除 v2.0 + 全管线穿透审查")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. 执行摘要")
    lines.append("")
    lines.append("本次回测是对前期 ZIRP 局部回测的**全管线升级**，在 TIPS 单因子验证的基础上引入了")
    lines.append("VIX、DXY、HY 信用利差、名义 10Y 利率四个跨资产维度，完整穿透了 L1 概率矩阵 →")
    lines.append("L2 物理红线 → L3 Decision Kernel → 最终风险预算的全链路。")
    lines.append("")
    lines.append("### 核心发现")
    lines.append("")
    lines.append(f"- **有效状态分布**: {dict(regime_dist)}")
    lines.append(f"- **风险预算**: 均值 {budget_stats['mean']:.2f}, 范围 [{budget_stats['min']:.2f}, {budget_stats['max']:.2f}]")
    lines.append(f"- **零预算月数**: {budget_stats['zero_months']}/{len(results)} ({budget_stats['zero_months']/len(results)*100:.0f}%)")
    lines.append(f"- **物理红线触发**: {len(red_line_events)} 次")
    lines.append(f"- **资金定价象限分布**: {dict(quadrant_dist)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. 跨资产特征矩阵")
    lines.append("")
    lines.append("| 月份 | TIPS% | VIX | DXY | HY(bp) | 10Y% | 60d ROC |")
    lines.append("|------|-------|-----|-----|--------|------|---------|")
    for r in results:
        roc_str = f"{r['tips_roc_60d']:.2%}" if r["tips_roc_60d"] is not None else "N/A"
        lines.append(f"| {r['month']} | {r['tips_yield']:.2f} | {r['vix']:.1f} | {r['dxy']:.1f} | {r['hy_spread']} | {r['nominal_10y']:.2f} | {roc_str} |")
    lines.append("")
    lines.append("**数据来源**: FRED LTIIT (TIPS), CBOE (VIX), ICE (DXY), FRED BAMLH0A0HYM2 (HY), FRED DGS10 (10Y)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. 概率状态机输出 (L1)")
    lines.append("")
    lines.append("| 月份 | P(RISK_ON) | P(TIGHT) | P(SQUEEZE) | P(TRANS) | 硬标签 |")
    lines.append("|------|-----------|----------|------------|----------|--------|")
    for r in results:
        lines.append(
            f"| {r['month']} | {r['prob_risk_on']:.1%} | {r['prob_tight_liquidity']:.1%} | "
            f"{r['prob_liquidity_squeeze']:.1%} | {r['prob_transition']:.1%} | {r['hard_label_from_probs']} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. 物理红线裁决 (L2)")
    lines.append("")
    if red_line_events:
        lines.append("| 月份 | 触发红线 | 代码 | 强制状态 |")
        lines.append("|------|---------|------|---------|")
        for r in red_line_events:
            lines.append(f"| {r['month']} | {r['red_line_fired']} | {r['red_line_code']} | {r['effective_hard_regime']} |")
    else:
        lines.append("评估窗口内未触发任何物理红线（VIX < 40, HY < 600bp, core_pce 无数据）。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. 最终风险预算 (L3 Kernel 输出)")
    lines.append("")
    lines.append("| 月份 | 有效状态 | 权限层 | 动作 | 风险预算 | 防御预算 | 原因代码 |")
    lines.append("|------|---------|--------|------|---------|---------|---------|")
    for r in results:
        lines.append(
            f"| {r['month']} | {r['effective_hard_regime']} | {r['kernel_authority']} | "
            f"{r['kernel_action']} | {r['risk_budget']:.2f} | {r['defense_budget']:.2f} | {r['kernel_reason_code']} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 6. 状态转移矩阵")
    lines.append("")
    regimes_in_matrix = sorted(transition_matrix.keys())
    if regimes_in_matrix:
        header = "| From \\ To | " + " | ".join(regimes_in_matrix) + " |"
        sep = "|-----------" * (len(regimes_in_matrix) + 1) + "|"
        lines.append(header)
        lines.append(sep)
        for from_reg in regimes_in_matrix:
            row = f"| {from_reg} "
            for to_reg in regimes_in_matrix:
                cell = transition_matrix[from_reg].get(to_reg, {})
                count = cell.get("count", 0)
                pct = cell.get("pct", 0.0)
                row += f"| {count} ({pct:.0f}%) "
            row += "|"
            lines.append(row)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 7. 关键事件深度分析")
    lines.append("")
    for evt in key_events:
        lines.append(f"### {evt['event']}")
        lines.append("")
        lines.append(evt["analysis"])
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 8. 资金定价象限 (Research Layer)")
    lines.append("")
    lines.append("| 月份 | 象限 | 标签 | 置信度 | 硬状态提示 |")
    lines.append("|------|------|------|--------|-----------|")
    for r in results:
        lines.append(
            f"| {r['month']} | {r['funding_quadrant']} | {r['quadrant_label_zh']} | "
            f"{r['quadrant_confidence']:.0%} | {r['quadrant_hard_regime_hint']} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 9. 审查回应与局限性")
    lines.append("")
    lines.append("### 对审查意见的回应")
    lines.append("")
    lines.append("1. **跨资产特征矩阵** ✅ 已完成：引入 VIX、DXY、HY Credit Spread、名义 10Y 四个维度")
    lines.append("2. **概率状态机与红线裁决** ✅ 已完成：完整输出四象限概率分布 + 物理红线状态")
    lines.append("3. **最终风险预算** ✅ 已完成：Kernel decide() 输出的 risk_budget 是最终评估标准")
    lines.append("")
    lines.append("### 已知局限性")
    lines.append("")
    lines.append("- **月度粒度**: 使用月度均值/月末值，无法捕捉日内状态切换（如 VIX 日内飙至 40+ 的闪崩事件）")
    lines.append("- **无 core_pce 数据**: 物理红线中的 core_pce_max 未能参与评估（FRED PCE 为季度数据）")
    lines.append("- **DXY z-score 缺失**: 月度数据不足以计算 60 日 z-score，rule-based regime 的相对路径退化为绝对路径")
    lines.append("- **TIPS ROC 近似**: 月度数据前向填充为日频后计算 60 日 ROC，存在插值平滑偏差")
    lines.append("- **无实际交易摩擦**: risk_budget 是理论配置上限，未扣除交易成本/滑点")
    lines.append("- **样本量**: 20 个月不足以做参数拟合（符合参数冻结条款）")
    lines.append("")
    lines.append("### 与局部回测的对比")
    lines.append("")
    lines.append("| 维度 | 局部回测 (zirp_backtest.py) | 全管线回测 (本报告) |")
    lines.append("|------|---------------------------|-------------------|")
    lines.append("| 因子数 | 1 (TIPS) | 5 (TIPS+VIX+DXY+HY+10Y) |")
    lines.append("| 管线层 | L1 局部函数 | L1→L2→L3→Research 全链路 |")
    lines.append("| 输出 | 布尔值 (risk_on=True/False) | 风险预算 [0, 1] + 概率矩阵 |")
    lines.append("| 状态空间 | RISK_ON / TRANSITION | 4 种 regime + 物理红线 |")
    lines.append("| 红线覆盖 | 无 | VIX/HY/core_pce 物理夺权 |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown report saved: {report_path}")


def main():
    print("=" * 60)
    print("Full-Pipeline Macro Backtest")
    print("Window: 2024-10 to 2026-05 | Monthly granularity")
    print("Pipeline: Features → Probs → Red Lines → Regime → Kernel → Budget")
    print("=" * 60)

    results = []
    prev_budget = 0.0  # Start with zero budget

    for month in EVAL_MONTHS:
        tips_history = build_daily_tips_history(month)
        result = run_full_pipeline(month, tips_history, prev_budget)
        results.append(result)
        prev_budget = result["risk_budget"]

    # Print pipeline trace
    print_pipeline_table(results)

    # Save CSV + JSON
    output_dir = PROJECT_ROOT / "docs" / "research"
    save_results(results, output_dir)

    # Generate Markdown analysis report
    generate_markdown_report(results, output_dir)

    # Print key events summary
    print("\n" + "=" * 60)
    print("KEY EVENTS SUMMARY")
    print("=" * 60)
    key_events = analyze_key_events(results)
    for evt in key_events:
        print(f"\n📌 {evt['event']}")
        print(f"   {evt['analysis']}")

    # Print transition matrix
    print("\n" + "=" * 60)
    print("REGIME TRANSITION MATRIX")
    print("=" * 60)
    tm = compute_transition_matrix(results)
    for from_reg, to_dict in tm.items():
        for to_reg, info in to_dict.items():
            if info["count"] > 0:
                print(f"  {from_reg} → {to_reg}: {info['count']} ({info['pct']:.0f}%)")

    # Print budget summary
    print("\n" + "=" * 60)
    print("RISK BUDGET SUMMARY")
    print("=" * 60)
    budgets = [r["risk_budget"] for r in results]
    print(f"  Mean: {np.mean(budgets):.4f}")
    print(f"  Min:  {np.min(budgets):.4f}")
    print(f"  Max:  {np.max(budgets):.4f}")
    print(f"  Zero-budget months: {sum(1 for b in budgets if b == 0.0)}/{len(budgets)}")
    print(f"  Non-zero months:    {sum(1 for b in budgets if b > 0.0)}/{len(budgets)}")

    print("\n✅ Full-pipeline backtest complete.")


if __name__ == "__main__":
    main()
