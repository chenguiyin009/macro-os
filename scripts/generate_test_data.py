"""Generate realistic synthetic macro data with known crisis event signatures."""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def generate_macro_data(start="2023-01-01", end="2026-04-18"):
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    dates = pd.date_range(start_dt, end_dt, freq="B")
    n = len(dates)
    rng = np.random.default_rng(42)

    qqq = np.ones(n) * 280.0
    vix = np.ones(n) * 18.0
    dxy = np.ones(n) * 103.0
    tips = np.ones(n) * 1.5
    credit = np.ones(n) * 380.0
    gold = np.ones(n) * 1820.0

    regimes = ["RISK_ON"] * n
    phases = ["NONE"] * n

    for i in range(1, n):
        frac = i / n

        if frac < 0.18:
            # 2023 Q1: rate hikes, rising rates
            vol = 0.012; drift_q = 0.0005; drift_v = -0.0003
            regimes[i] = "TIGHT_LIQUIDITY"; phases[i] = "EARLY"
        elif frac < 0.22:
            # March 2023: SVB collapse
            vol = 0.025; drift_q = -0.005; drift_v = 0.02
            regimes[i] = "LIQUIDITY_SQUEEZE"; phases[i] = "CRISIS"
        elif frac < 0.40:
            # Recovery rally
            vol = 0.010; drift_q = 0.002; drift_v = -0.002
            regimes[i] = "RISK_ON"; phases[i] = "NONE"
        elif frac < 0.55:
            # 2023 Q4: elevated rates, tight liquidity
            vol = 0.015; drift_q = -0.001; drift_v = 0.001
            regimes[i] = "TIGHT_LIQUIDITY"; phases[i] = "MID"
        elif frac < 0.62:
            # Early 2024: AI rally
            vol = 0.011; drift_q = 0.003; drift_v = -0.001
            regimes[i] = "RISK_ON"; phases[i] = "NONE"
        elif frac < 0.75:
            # Mid 2024: yen carry unwind, volatility
            vol = 0.018; drift_q = -0.002; drift_v = 0.003
            regimes[i] = "TIGHT_LIQUIDITY"; phases[i] = "LATE"
        else:
            # Late 2024-2026: recovery and uncertainty
            vol = 0.013; drift_q = 0.001; drift_v = -0.0005
            regimes[i] = "RISK_ON"; phases[i] = "NONE"

        qqq[i] = qqq[i-1] * (1.0 + rng.normal(drift_q, vol))
        vix[i] = max(10, min(50, vix[i-1] * (1.0 + rng.normal(drift_v, vol*0.5))))
        dxy[i] = dxy[i-1] * (1.0 + rng.normal(0.0001, 0.004))
        tips[i] = tips[i-1] * (1.0 + rng.normal(0.0002, 0.005))
        credit[i] = max(200, credit[i-1] * (1.0 + rng.normal(0.0003 if regimes[i]!='RISK_ON' else -0.0002, 0.006)))
        gold[i] = gold[i-1] * (1.0 + rng.normal(0.0004, 0.007))

    df = pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in dates],
        "QQQ_close": np.round(qqq, 2),
        "vix": np.round(vix, 2),
        "dxy_proxy": np.round(dxy, 2),
        "tips_proxy": np.round(tips, 4),
        "credit_proxy": np.round(credit, 2),
        "gold_close": np.round(gold, 2),
        "hard_regime": regimes,
        "divergence_phase": phases,
        "risk_score": [0.7 if r == "RISK_ON" else (0.3 if r == "TIGHT_LIQUIDITY" else 0.15) for r in regimes],
        "proposed_risk": 0.8,
    })
    df["recovery_active"] = (df["QQQ_close"].pct_change() > 0).rolling(3).sum() >= 3
    df["dxy_roc_20d"] = df["dxy_proxy"].pct_change(20)
    df["tips_roc_20d"] = df["tips_proxy"].pct_change(20)
    df = df.bfill()
    out = "macro_history_synthetic.csv"
    df.to_csv(out, index=False)
    print(f"Generated {len(df)} days of realistic synthetic data -> {out}")
    return df

if __name__ == "__main__":
    generate_macro_data()
