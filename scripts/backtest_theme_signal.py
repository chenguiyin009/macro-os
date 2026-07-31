"""Theme risk_bias -> kernel SOFT cap — BACKTEST + CALIBRATION (Macro OS v5).

WHY
---
Item D of the 1-year backtest review: the theme state machine (v3.1r port) produces
`risk_bias` (risk_off / risk_on / mixed / none) + `pressure_override` (themes 9/10),
but today they are REPORT-ONLY. This script tests promoting them to a third SOFT kernel
input (mirroring the C-grade tech dampener): when the denominator says RISK_ON but the
theme machine says risk_off / pressure, apply a structural cap to the budget.

METHOD
------
1. Reproduce the theme signal at EVERY historical date by vectorizing the theme script's
   z/b/n/g computation over full price history (instead of calling compute_state per-day,
   which is O(N) per call). For DXY we use FRED DTWEXBGS (the DX-Y.NYB yfinance feed is
   currently delisted/broken; DTWEXBGS keeps the dollar SHAPE the denominator uses).
2. Reuse the REAL v5 kernel budget series prepared by backtest_equity_overlay_calibrate
   (prepare_2022 + prepare_468) so the theme cap is layered on top of production logic.
3. Calibrate a risk_bias -> cap mapping grid. Apply only on RISK_ON days (subordinate to
   HARD_VETO, exactly like the tech dampener).
4. DUAL-GATE (same as the proven C-grade gate):
     (a) 2022 window: theme trigger RATE among RISK_ON days <= 25%
     (b) 468-day window: SOXX proxy maxDD improvement >= 40%
   PLUS a stress-window validity check: the cap must cut SOXX/QQQ drawdown in real
   co-selloffs (2020 COVID, 2018-Q4, 2022 bear) and add minimal drag in calm 2023-24.

Run:  python scripts/backtest_theme_signal.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import theme_state_machine_daily as tsm  # noqa: E402

logger = logging.getLogger("backtest_theme_signal")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

DEFAULT_PROXY = "http://127.0.0.1:7890"
DATA = ROOT / "data"
RESEARCH = ROOT / "docs" / "research"
CACHE = DATA / "_theme_backtest"
CACHE.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data: max-history closes for the 14 theme symbols (DXY via FRED DTWEXBGS)
# ---------------------------------------------------------------------------
def _ensure_proxy() -> None:
    if any(os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY")):
        return
    os.environ.setdefault("HTTPS_PROXY", DEFAULT_PROXY)
    os.environ.setdefault("HTTP_PROXY", DEFAULT_PROXY)


def _fred_one_window(series_id: str, start: str, end: str, timeout: float = 45) -> pd.Series:
    """Fetch ONE bounded FRED window (small CSV -> no proxy read-timeout)."""
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id={series_id}&cosd={start}&coed={end}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # Strategy: proxy first; if it times out, fall back to a TRUE direct
    # connection (proxy env must be UNSET or urllib still routes via proxy).
    last_err = None
    # 1) via proxy (honors HTTPS_PROXY env)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8")
        return _parse_fred_csv_text(text, series_id, start, end)
    except Exception as e:
        last_err = e
        logger.info("FRED %s %s..%s proxy failed (%s); trying direct",
                    series_id, start, end, type(e).__name__)
    # 2) TRUE direct (unset proxy env for this call only)
    saved = {k: os.environ.pop(k, None) for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}
    import ssl
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            text = r.read().decode("utf-8")
        return _parse_fred_csv_text(text, series_id, start, end)
    except Exception as e:
        last_err = e
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    raise RuntimeError(f"FRED window {series_id} {start}..{end} failed: {last_err}")


def _parse_fred_csv_text(text: str, series_id: str, start: str, end: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    datecol, valcol = df.columns[0], df.columns[1]
    idx = pd.to_datetime(df[datecol], errors="coerce")
    vals = pd.to_numeric(df[valcol], errors="coerce")
    s = vals.where(idx.notna())
    s.index = idx.dropna()
    return s.dropna().sort_index()


def _fred_series(series_id: str, target_year: int = 2006) -> pd.Series:
    """Full-history FRED series via ADAPTIVE bounded windows.

    Large FRED CSV responses break through this proxy/TLS (timeout or
    UNEXPECTED_EOF). We try a 2y window first (fast for recent data), then
    downgrade to 6mo, then 30d on failure. Tiny windows succeed via direct
    connection. All fetched slices are stitched together.
    """
    end = dt.date.today()
    start = dt.date(target_year, 1, 1)
    parts: List[pd.Series] = []
    cur = start
    step_pref = [365 * 2, 182, 30]  # 2y -> 6mo -> 1mo
    while cur <= end:
        nxt = cur + dt.timedelta(days=step_pref[0])
        win_end = min(nxt - dt.timedelta(days=1), end)
        s = None
        for step in step_pref:
            wc = cur
            we = min(wc + dt.timedelta(days=step) - dt.timedelta(days=1), end)
            try:
                s = _fred_one_window(series_id, wc.isoformat(), we.isoformat())
                if s is not None and len(s):
                    break
            except Exception:
                s = None
                continue
        if s is not None and len(s):
            parts.append(s)
        cur = nxt
    if not parts:
        raise RuntimeError(f"FRED {series_id}: no windows fetched")
    full = pd.concat(parts).sort_index()
    full = full[~full.index.duplicated(keep="last")]
    logger.info("FRED %s full: %d rows %s..%s", series_id, len(full),
                full.index[0].date(), full.index[-1].date())
    return full
    df.columns = [c.strip() for c in df.columns]
    datecol, valcol = df.columns[0], df.columns[1]
    s = pd.to_numeric(df[valcol], errors="coerce")
    idx = pd.to_datetime(df[datecol], errors="coerce")
    s = s.where(idx.notna())
    s.index = idx.dropna()
    return s.dropna().sort_index()


def _yf_max(ticker: str) -> pd.Series:
    import yfinance as yf
    raw = yf.download(ticker, period="max", auto_adjust=True, progress=False, threads=False)
    if raw is None or getattr(raw, "empty", True):
        return pd.Series(dtype=float)
    cols = raw.columns
    if getattr(cols, "nlevels", 1) > 1:
        if ("Close", ticker) in cols:
            close = raw[("Close", ticker)]
        elif (ticker, "Close") in cols:
            close = raw[(ticker, "Close")]
        elif "Close" in cols.get_level_values(0):
            cd = raw.xs("Close", axis=1, level=0)
            close = cd[ticker] if ticker in cd.columns else cd.iloc[:, 0]
        else:
            return pd.Series(dtype=float)
    else:
        close = raw["Close"] if "Close" in cols else (raw[ticker] if ticker in cols else pd.Series(dtype=float))
    s = pd.to_numeric(close, errors="coerce").dropna()
    return s if len(s) >= 30 else pd.Series(dtype=float)


def load_theme_history(force: bool = False) -> Dict[str, pd.Series]:
    """Download max history once per ticker; DXY via FRED DTWEXBGS. Cache to data/_theme_backtest."""
    closes: Dict[str, pd.Series] = {}
    _ensure_proxy()
    for key, yf_tkr, kind, label in tsm.SYMBOLS:
        if key == "x05":  # DXY -> FRED DTWEXBGS (avoid broken DX-Y.NYB)
            cache = CACHE / "DTWEXBGS.csv"
            s = None
            if cache.exists() and not force:
                s = pd.read_csv(cache, index_col=0, parse_dates=True)["Close"]
            if s is None or len(s) < 30:
                logger.info("FRED DTWEXBGS (chunked, full history) ...")
                s = _fred_series("DTWEXBGS", target_year=2006)
                s.to_frame("Close").to_csv(cache)
            closes[key] = s
            continue
        cache = CACHE / f"{yf_tkr}.csv"
        s = None
        if cache.exists() and not force:
            s = pd.read_csv(cache, index_col=0, parse_dates=True)["Close"]
        if s is None or len(s) < 30:
            logger.info("yf %s (%s) ...", label, yf_tkr)
            s = _yf_max(yf_tkr)
            if len(s):
                s.to_frame("Close").to_csv(cache)
        if s is None or len(s) < 30:
            logger.warning("无数据: %s", label)
        else:
            closes[key] = s
    return closes


# ---------------------------------------------------------------------------
# Vectorized theme history (replicates compute_state over full history)
# ---------------------------------------------------------------------------
def _persist_count_series(h: pd.Series) -> pd.Series:
    """Consecutive True count ENDING at each row (vectorized)."""
    s = h.fillna(False).astype(int)
    grp = (s != s.shift(1)).cumsum()
    cnt = s.groupby(grp).cumcount() + 1
    return cnt * s


def theme_history_frame(closes: Dict[str, pd.Series]) -> pd.DataFrame:
    df, _ = tsm.align_closes(closes, as_of=None)
    for key, _, _, _ in tsm.SYMBOLS:
        if key not in df.columns:
            df[key] = np.nan
    if len(df) < 210:
        raise RuntimeError(f"历史数据不足: {len(df)} 根 (<210)")
    z: Dict[str, pd.Series] = {}
    for key, _, kind, _ in tsm.SYMBOLS:
        series = df[key].astype(float)
        u = tsm.u1(series)
        z[key] = -u if kind in ("inv", "neg") else u
        z[key] = z[key].fillna(0.0)

    x15 = z["x02"] - z["x04"]
    x18 = tsm.u2(x15)
    x21 = z["x04"]
    x24 = (z["x11"] + z["x12"] + z["x13"]) / 3.0
    x25 = z["x12"] - z["x11"]
    x26 = z["x13"] - z["x11"]
    x27 = (z["x08"] + z["x07"]) / 2.0
    x29 = (z["x08"] > 0.5) & (x21 > 0.5)

    b: Dict[int, pd.Series] = {}
    b[1] = (x21 > 0.5) & ((z["x01"] > 0.5) | (z["x02"] > 0.5))
    b[2] = (x18 > 0.5) & (z["x09"] > 0.5) & (z["x02"] > 0.5)
    b[3] = (x18 > 0.75) & (z["x09"] < 0.5)
    b[4] = (z["x03"] > 0.5) & (z["x03"] - z["x01"] > 0.25)
    b[5] = (z["x03"] > 0.5) & (z["x05"] < -0.5) & (z["x08"] > 0.5)
    b[6] = (z["x01"] < -0.5) & (z["x01"] < z["x03"]) & (x24 > -0.5)
    b[7] = (z["x02"] < -0.5) & (x24 < -0.5) & (z["x09"] < -0.5)
    b[8] = (z["x02"] < -0.5) & (x24 < -0.5) & (z["x08"] > 0.5) & (z["x09"] > 0.5)
    b[9] = (z["x05"] > 1.0) & (x21 > 0.5) & (z["x08"] < -0.5) & (x24 < -0.5)
    b[10] = (z["x07"] > 1.0) & (z["x12"] < -0.5) & (z["x12"] < z["x11"])
    b[11] = (x24 > 0.5)
    b[12] = (z["x12"] > 0.5) & (x25 > 0.5)
    b[13] = (z["x12"] < -0.5) & ((z["x11"] > -0.25) | (z["x13"] > -0.25))
    b[14] = ((x21 > 0.5) | (z["x03"] > 0.5)) & (
        ((z["x12"] >= -0.25) & (z["x14"] > 0)) | ((z["x12"] > 0) & (z["x11"] >= -0.25)))
    b[15] = (z["x11"] < -0.5) & (z["x12"] < -0.5) & (z["x14"] < -0.5)
    b[16] = x29
    b[17] = (z["x14"].abs() > 1.5) & (x24.abs() < 0.5) & (z["x05"].abs() < 0.5) & (z["x02"].abs() < 0.5)

    n: Dict[int, pd.Series] = {}
    n[1] = (z["x08"] < 0).astype(int) + (z["x05"] > 0.5).astype(int) + (x18 < 0.5).astype(int)
    n[2] = (z["x10"] > 0.5).astype(int) + (z["x08"] > -0.5).astype(int) + (z["x03"] > 0.5).astype(int)
    n[3] = (z["x10"] < 0.5).astype(int) + (z["x02"] > 0).astype(int) + (z["x01"] > 0.5).astype(int)
    n[4] = (z["x08"] > -0.25).astype(int) + (z["x05"].abs() < 0.5).astype(int) + (x18 < 0.5).astype(int)
    n[5] = (z["x06"] > 0.5).astype(int) + (x24 < 0).astype(int) + (z["x03"] > z["x01"]).astype(int)
    n[6] = (x24 > 0.5).astype(int) + (z["x08"] > 0).astype(int) + (z["x05"] < 0).astype(int)
    n[7] = (z["x10"] < -0.5).astype(int) + (z["x01"] < -0.5).astype(int) + (z["x14"] < 0).astype(int)
    n[8] = (z["x07"] > 0.5).astype(int) + (z["x03"] < z["x01"]).astype(int) + (z["x14"] < 0).astype(int)
    n[9] = (z["x14"] < -0.5).astype(int) + (z["x09"] < 0).astype(int) + (z["x06"] < -0.5).astype(int)
    n[10] = (z["x14"] < -0.5).astype(int) + (z["x08"] < 0.25).astype(int) + (x24 < -0.5).astype(int)
    n[11] = (z["x10"] > 0.5).astype(int) + (z["x14"] > 0.5).astype(int) + (z["x05"] < 0).astype(int)
    n[12] = (z["x11"] < 0.5).astype(int) + (z["x13"] < 0.25).astype(int) + (z["x14"] > 0).astype(int)
    n[13] = (z["x13"] > 0.25).astype(int) + (x26 > 0.5).astype(int) + (x24 > -0.25).astype(int)
    n[14] = (z["x13"] > -0.25).astype(int) + (z["x10"] > -0.25).astype(int) + (x24 > 0).astype(int)
    n[15] = (z["x13"] < -0.5).astype(int) + (z["x10"] < -0.5).astype(int) + (x27 > 0.5).astype(int)
    n[16] = (z["x05"] > 0).astype(int) + (z["x03"] > 0.5).astype(int) + (z["x09"] < 0.5).astype(int)
    n[17] = (z["x08"].abs() < 0.5).astype(int) + (z["x09"].abs() < 0.5).astype(int) + (x21.abs() < 0.5).astype(int)

    g = {i: _persist_count_series(b[i]) for i in range(1, 18)}
    H = pd.DataFrame({i: b[i].astype(bool) for i in range(1, 18)}, index=df.index)
    N = pd.DataFrame({i: n[i].astype(int) for i in range(1, 18)}, index=df.index)
    G = pd.DataFrame({i: g[i].astype(int) for i in range(1, 18)}, index=df.index)

    y1 = pd.Series(index=df.index, dtype=int)
    idx_list = list(df.index)
    Hv = H.values
    Nv = N.values
    Gv = G.values
    for r, t in enumerate(df.index):
        h = {i + 1: bool(Hv[r, i]) for i in range(17)}
        nn = {i + 1: int(Nv[r, i]) for i in range(17)}
        gg = {i + 1: int(Gv[r, i]) for i in range(17)}
        y1.iloc[r] = tsm.select_dominant(h, nn, gg)

    out = pd.DataFrame(index=df.index)
    out["y1"] = y1
    out["risk_bias"] = y1.map(tsm.theme_risk_bias)
    out["pressure_override"] = y1.map(lambda y: tsm.theme_pressure_override(y))
    return out


# ---------------------------------------------------------------------------
# Candidate cap maps (risk_on/none always 1.0)
# ---------------------------------------------------------------------------
CANDIDATES: Dict[str, Dict[str, float]] = {
    "control":  {"risk_off": 1.00, "pressure": 1.00, "mixed": 1.00},
    "RO45_P25_M65": {"risk_off": 0.45, "pressure": 0.25, "mixed": 0.65},
    "RO50_P30_M70": {"risk_off": 0.50, "pressure": 0.30, "mixed": 0.70},
    "RO40_P20_M60": {"risk_off": 0.40, "pressure": 0.20, "mixed": 0.60},
    "RO55_P35_M75": {"risk_off": 0.55, "pressure": 0.35, "mixed": 0.75},
    "RO60_P40_M80": {"risk_off": 0.60, "pressure": 0.40, "mixed": 0.80},
    "P_only":  {"risk_off": 1.00, "pressure": 0.25, "mixed": 1.00},
    "RO_only": {"risk_off": 0.45, "pressure": 1.00, "mixed": 1.00},
}


def theme_cap(rb, po, caps: Dict[str, float]) -> float:
    if po:
        return caps.get("pressure", 1.0)
    return caps.get(rb, 1.0)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def proxy_curve(budget, ret):
    return np.cumprod(1.0 + budget * ret)


def max_dd(nav):
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1.0).min())


def metrics_kernel(kv: pd.DataFrame, th: pd.DataFrame, soxx_ret: pd.Series,
                   qqq_ret: pd.Series, qqq_full: pd.Series, caps: Dict[str, float]) -> Dict[str, Any]:
    """Layer theme cap onto REAL kernel budgets (RISK_ON only), per the tech-dampener pattern."""
    rb = th["risk_bias"].reindex(kv.index).fillna("none")
    po = th["pressure_override"].reindex(kv.index).fillna(False).astype(bool)
    cap_daily = np.array([theme_cap(r, p, caps) for r, p in zip(rb, po)], dtype=float)
    risk_on = (kv["rule_regime"] == "RISK_ON").values
    base_b = kv["risk_budget"].values.astype(float)
    new_b = np.where(risk_on, np.minimum(base_b, cap_daily), base_b)

    triggered = risk_on & (cap_daily < 1.0)
    risk_on_n = int(risk_on.sum())
    trig_n = int(triggered.sum())
    trigger_rate = 100.0 * trig_n / max(1, risk_on_n)

    soxx_r = soxx_ret.reindex(kv.index).ffill().fillna(0.0).values
    qqq_r = qqq_ret.reindex(kv.index).ffill().fillna(0.0).values
    base_soxx = proxy_curve(base_b, soxx_r)
    new_soxx = proxy_curve(new_b, soxx_r)
    base_qqq = proxy_curve(base_b, qqq_r)
    new_qqq = proxy_curve(new_b, qqq_r)

    # false trigger: triggered day where next-20d QQQ sum > 0
    qidx = qqq_full.index
    qvals = qqq_full.values
    fwd = []
    for d in kv.index[triggered]:
        pos = qidx.get_indexer([d])[0]
        if pos >= 0 and pos + 1 < len(qidx):
            fut = qvals[pos + 1: pos + 21]
            fwd.append(float(fut.sum()))
    fwd = [x for x in fwd if not pd.isna(x)]
    false_rate = 100.0 * sum(1 for x in fwd if x > 0) / max(1, len(fwd)) if fwd else float("nan")

    return {
        "risk_on_days": risk_on_n,
        "triggered_days": trig_n,
        "trigger_rate_pct": round(trigger_rate, 1),
        "false_trig_pct": round(false_rate, 1) if not pd.isna(false_rate) else None,
        "soxx_maxdd_base_pct": round(max_dd(base_soxx) * 100, 2),
        "soxx_maxdd_new_pct": round(max_dd(new_soxx) * 100, 2),
        "soxx_dd_improve_pct": round((max_dd(new_soxx) - max_dd(base_soxx)) / abs(max_dd(base_soxx)) * 100, 1),
        "qqq_maxdd_base_pct": round(max_dd(base_qqq) * 100, 2),
        "qqq_maxdd_new_pct": round(max_dd(new_qqq) * 100, 2),
        "soxx_total_base_pct": round(float(base_soxx[-1] - 1) * 100, 2),
        "soxx_total_new_pct": round(float(new_soxx[-1] - 1) * 100, 2),
    }


def metrics_stress(soxx: pd.Series, qqq: pd.Series, th: pd.DataFrame,
                   caps: Dict[str, float], base_budget: float = 0.8) -> Dict[str, Any]:
    """Isolate theme-signal value: flat RISK_ON baseline (0.8) cut by theme cap.
    Shows whether the narrative signal would have reduced drawdown in a real selloff."""
    idx = soxx.index.intersection(qqq.index)
    soxx = soxx.reindex(idx).ffill().bfill()
    qqq = qqq.reindex(idx).ffill().bfill()
    rb = th["risk_bias"].reindex(idx).fillna("none")
    po = th["pressure_override"].reindex(idx).fillna(False).astype(bool)
    cap_daily = np.array([theme_cap(r, p, caps) for r, p in zip(rb, po)], dtype=float)
    soxx_r = soxx.pct_change().fillna(0.0).values
    qqq_r = qqq.pct_change().fillna(0.0).values
    base_soxx = proxy_curve(np.full(len(idx), base_budget), soxx_r)
    new_soxx = proxy_curve(base_budget * cap_daily, soxx_r)
    base_qqq = proxy_curve(np.full(len(idx), base_budget), qqq_r)
    new_qqq = proxy_curve(base_budget * cap_daily, qqq_r)
    return {
        "days": int(len(idx)),
        "trigger_rate_pct": round(100.0 * float((cap_daily < 1.0).mean()), 1),
        "soxx_maxdd_base_pct": round(max_dd(base_soxx) * 100, 2),
        "soxx_maxdd_new_pct": round(max_dd(new_soxx) * 100, 2),
        "soxx_dd_improve_pct": round((max_dd(new_soxx) - max_dd(base_soxx)) / abs(max_dd(base_soxx)) * 100, 1),
        "qqq_maxdd_base_pct": round(max_dd(base_qqq) * 100, 2),
        "qqq_maxdd_new_pct": round(max_dd(new_qqq) * 100, 2),
        "soxx_total_base_pct": round(float(base_soxx[-1] - 1) * 100, 2),
        "soxx_total_new_pct": round(float(new_soxx[-1] - 1) * 100, 2),
    }


def load_eq_max(ticker: str) -> pd.Series:
    cache = CACHE / f"eq_{ticker}.csv"
    if cache.exists():
        s = pd.read_csv(cache, index_col=0, parse_dates=True)["Close"]
        if len(s) >= 30:
            return s
    _ensure_proxy()
    import yfinance as yf
    raw = yf.download(ticker, period="max", auto_adjust=True, progress=False, threads=False)
    if raw is None or getattr(raw, "empty", True):
        return pd.Series(dtype=float)
    cols = raw.columns
    close = raw[("Close", ticker)] if (getattr(cols, "nlevels", 1) > 1 and ("Close", ticker) in cols) else raw["Close"]
    s = pd.to_numeric(close, errors="coerce").dropna()
    s.to_frame("Close").to_csv(cache)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--skip-stress", action="store_true", help="skip 2020/2018 stress + calm drag (faster)")
    args = ap.parse_args()

    print("=== Theme signal -> kernel SOFT cap BACKTEST ===", flush=True)
    print("[1/4] loading max-history theme closes (DXY=FRED DTWEXBGS) ...", flush=True)
    closes = load_theme_history(force=args.force_refresh)
    print(f"    got {len(closes)}/{len(tsm.SYMBOLS)} symbols", flush=True)

    print("[2/4] computing full-history theme signal ...", flush=True)
    th = theme_history_frame(closes)
    print(f"    theme history rows: {len(th)} ({th.index[0].date()} .. {th.index[-1].date()})", flush=True)

    from scripts.backtest_equity_overlay_calibrate import prepare_2022, prepare_468  # noqa: E402
    print("[3/4] preparing REAL kernel budget windows (2022 + 468d) ...", flush=True)
    kv22, soxx_r22, qqq_r22, qqq_full22 = prepare_2022()
    kv468, soxx_r468, qqq_r468, qqq_full468 = prepare_468()
    print(f"    2022 RISK_ON days: {(kv22['rule_regime']=='RISK_ON').sum()};  "
          f"468d RISK_ON days: {(kv468['rule_regime']=='RISK_ON').sum()}", flush=True)

    kernel_results = {}
    for name, caps in CANDIDATES.items():
        kernel_results[name] = {
            "caps": caps,
            "w2022": metrics_kernel(kv22, th, soxx_r22, qqq_r22, qqq_full22, caps),
            "w468": metrics_kernel(kv468, th, soxx_r468, qqq_r468, qqq_full468, caps),
        }

    stress_results = {}
    if not args.skip_stress:
        print("[4/4] stress + calm windows ...", flush=True)
        soxx = load_eq_max("SOXX")
        qqq = load_eq_max("QQQ")
        # Align to a common trading calendar BEFORE window masking, so the
        # boolean mask has identical length for both series.
        common = soxx.index.intersection(qqq.index)
        soxx = soxx.reindex(common).ffill().bfill()
        qqq = qqq.reindex(common).ffill().bfill()
        windows = {
            "2020_COVID": (pd.Timestamp("2020-01-02"), pd.Timestamp("2020-12-31")),
            "2018Q4_selloff": (pd.Timestamp("2018-10-01"), pd.Timestamp("2018-12-31")),
            "2022_bear": (pd.Timestamp("2022-01-03"), pd.Timestamp("2022-12-31")),
            "calm_2023_2024": (pd.Timestamp("2023-01-02"), pd.Timestamp("2024-09-30")),
        }
        for wname, (a, b) in windows.items():
            mask = (soxx.index >= a) & (soxx.index <= b)
            stress_results[wname] = {
                name: metrics_stress(soxx[mask], qqq[mask], th, caps)
                for name, caps in CANDIDATES.items()
            }

    # ---- Dual-gate selection (same as proven C-grade gate) ----
    best = None
    for name, r in kernel_results.items():
        if name == "control":
            continue
        w22, w468 = r["w2022"], r["w468"]
        if w22["trigger_rate_pct"] <= 25.0 and w468["soxx_dd_improve_pct"] >= 40.0:
            score = (-w468["soxx_dd_improve_pct"], w22["trigger_rate_pct"])
            if best is None or score < best[0]:
                best = (score, name, r)

    # ---- Console ----
    print("\n=== KERNEL WINDOWS (real v5 budgets) ===")
    hdr = f"{'cand':16} | {'win':5} | {'trig%':6} {'false%':7} | {'SOXX_DDb':11} {'SOXX_DDn':11} {'impr%':7} | {'QQQ_DDb':10} {'QQQ_DDn':10}"
    print(hdr)
    for name, r in kernel_results.items():
        for win, w in (("2022", r["w2022"]), ("468d", r["w468"])):
            print(f"{name:16} | {win:5} | {w['trigger_rate_pct']:6} {str(w['false_trig_pct']):>7} | "
                  f"{w['soxx_maxdd_base_pct']:11} {w['soxx_maxdd_new_pct']:11} {w['soxx_dd_improve_pct']:7} | "
                  f"{w['qqq_maxdd_base_pct']:10} {w['qqq_maxdd_new_pct']:10}")

    if stress_results:
        print("\n=== STRESS / CALM (flat 0.8 baseline, theme-only value) ===")
        for wname, per in stress_results.items():
            print(f"-- {wname} --")
            for name, w in per.items():
                print(f"  {name:16} trig%={w['trigger_rate_pct']:5} SOXX_DD {w['soxx_maxdd_base_pct']:7}->{w['soxx_maxdd_new_pct']:7} "
                      f"(impr {w['soxx_dd_improve_pct']:5}%) QQQ_DD {w['qqq_maxdd_base_pct']:7}->{w['qqq_maxdd_new_pct']:7}")

    print(f"\nRECOMMENDED (dual-gate): {best[1] if best else 'NONE qualified'}")
    if best:
        print(f"  2022 trig%={best[2]['w2022']['trigger_rate_pct']}  468d SOXX impr={best[2]['w468']['soxx_dd_improve_pct']}%")
        print(f"  caps={best[2]['caps']}")

    # ---- JSON dump ----
    out = {
        "selection_rule": "2022 trigger_rate_pct <= 25 AND 468 soxx_dd_improve_pct >= 40 (same as C-grade gate). "
                          "theme cap applied on RISK_ON days only, subordinate to HARD_VETO.",
        "recommended": best[1] if best else None,
        "recommended_detail": best[2] if best else "NONE qualified",
        "kernel_results": kernel_results,
        "stress_results": stress_results,
    }
    (RESEARCH / "theme_signal_calibration.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- Markdown ----
    md = ["# 主题信号 → kernel SOFT cap · 回测与校准\n",
          f"> 候选档 `risk_on`/`none` 恒为 1.0；仅 `risk_off`/`pressure_override`/`mixed` 映射 cap。\n"
          f"> 门禁（同 C 档）：2022 **触发率 ≤ 25%** + 468 天 **SOXX maxDD 改善 ≥ 40%**。主题 cap 仅在分母 RISK_ON 日生效，从属于 HARD_VETO。\n"
          f"> DXY 用 FRED DTWEXBGS（DX-Y.NYB 此刻失准）。\n",
          f"> **推荐档：{out['recommended'] if out['recommended'] else '无达标候选'}**\n",
          "\n## 内核窗口（真实 v5 预算序列）\n",
          "| 候选 | 窗口 | 触发率% | 误触发% | SOXX基DD% | SOXX新DD% | 改善% | QQQ基DD% | QQQ新DD% |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, r in kernel_results.items():
        for win, w in (("2022", r["w2022"]), ("468d", r["w468"])):
            md.append(f"| {name} | {win} | {w['trigger_rate_pct']} | {w['false_trig_pct']} | "
                      f"{w['soxx_maxdd_base_pct']} | {w['soxx_maxdd_new_pct']} | {w['soxx_dd_improve_pct']} | "
                      f"{w['qqq_maxdd_base_pct']} | {w['qqq_maxdd_new_pct']} |")
    if stress_results:
        md.append("\n## 压力 / 平静窗（flat 0.8 基线，仅看主题信号价值）\n")
        for wname, per in stress_results.items():
            md.append(f"\n### {wname}\n")
            md.append("| 候选 | 触发率% | SOXX基DD% | SOXX新DD% | 改善% | QQQ基DD% | QQQ新DD% |")
            md.append("|---|---:|---:|---:|---:|---:|---:|")
            for name, w in per.items():
                md.append(f"| {name} | {w['trigger_rate_pct']} | {w['soxx_maxdd_base_pct']} | "
                          f"{w['soxx_maxdd_new_pct']} | {w['soxx_dd_improve_pct']} | {w['qqq_maxdd_base_pct']} | {w['qqq_maxdd_new_pct']} |")
    md.append("\n> 免责声明：历史回测研究，非投资建议。2022 HY 信用利差为重建代理；其余为真实 FRED / yfinance 数据。")
    (RESEARCH / "theme_signal_calibration.md").write_text("\n".join(md), encoding="utf-8")
    print("\n[written] docs/research/theme_signal_calibration.json + .md")


if __name__ == "__main__":
    main()
