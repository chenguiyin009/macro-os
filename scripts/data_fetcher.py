"""Macro OS v5.0 ? Historical data fetcher from Yahoo Finance.
Downloads macro proxies (QQQ, VIX, UUP, TIP, HYG, GLD)
and generates regime labels suitable for MacroBacktestEngine.
"""

from __future__ import annotations

import yfinance as yf
import pandas as pd
import numpy as np
from scripts.generate_test_data import generate_macro_data

def fetch_macro_history(start_date="2023-01-01", end_date="2026-04-18"):
    """
    Fetch macro data from Yahoo Finance.
    Falls back to synthetic data if Yahoo Finance rate limits.
    """
    import os
    print(f"Downloading real macro data from Yahoo Finance ({start_date} to {end_date})...")
    tickers = ["QQQ", "^VIX", "UUP", "TIP", "HYG", "GLD"]
    try:
        data = yf.download(tickers, start=start_date, end=end_date, progress=False)
        close = data["Close"] if "Close" in data.columns else data
    if isinstance(close.columns, pd.MultiIndex):
        close.columns = [c[0] if isinstance(c, tuple) else c for c in close.columns]
    df = close.rename(columns={
        "QQQ": "QQQ_close",
        "^VIX": "vix",
        "UUP": "dxy_proxy",
        "TIP": "tips_proxy",
        "HYG": "credit_proxy",
        "GLD": "gold_close",
    }).dropna().copy()
    df["dxy_roc_20d"] = df["dxy_proxy"].pct_change(20)
    df["tips_roc_20d"] = df["tips_proxy"].pct_change(20)
    df["hard_regime"] = np.where(
        (df["vix"] > 25) | (df["credit_proxy"].pct_change(20) < -0.05),
        "LIQUIDITY_SQUEEZE",
        np.where((df["dxy_roc_20d"] > 0.02) & (df["tips_roc_20d"] < -0.02),
                 "TIGHT_LIQUIDITY", "RISK_ON"),
    )
    df["divergence_phase"] = np.where(
        df["vix"] > 30, "CRISIS",
        np.where(df["vix"] > 20, "LATE",
        np.where(df["vix"] > 15, "MID", "NONE")))
    df["recovery_active"] = (df["QQQ_close"].pct_change() > 0).rolling(3).sum() >= 3
    df["risk_score"] = np.where(df["hard_regime"] == "RISK_ON", 0.7, 0.3)
    df["proposed_risk"] = 0.8
    df = df.bfill().reset_index()
    # Handle both yfinance versions: column may be "Date", "index", or datetime index
    if "Date" in df.columns:
        df.rename(columns={"Date": "date"}, inplace=True)
    elif "date" in df.columns:
        pass
    else:
        # DatetimeIndex from reset_index becomes "index"
        df.rename(columns={"index": "date"}, inplace=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    out = "macro_history_2023_2026.csv"
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} trading days to {out}")
    return df

if __name__ == "__main__":
    fetch_macro_history()
