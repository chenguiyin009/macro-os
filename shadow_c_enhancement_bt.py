# -*- coding: utf-8 -*-
"""
shadow_c_enhancement_bt.py — Research backtest: C vs B vs C_dyn40 vs C_mom12
=============================================================================
Research-only. Does NOT modify daily_allocator.STRATEGY_BOOKS.

Confirmed batch (2026-09-15):
  Primary: C, B, C_dyn40, C_mom12  (spy sleeve)
  Appendix: monthly rebalance; combined_budget≥0.50 skipped if history unavailable
  NOT in batch: C_rsp_tilt

Hard rules:
  - SECTOR_PROXY=off
  - No look-ahead: signal close T → weights earn returns from T+1 (prior w × today's r)
  - Explicit BIL cash residual; cost_one_way default 0.001 (+ 0 / 0.002 sensitivity)
  - Transparent proxy gate when overlay CSVs missing (documented)

Usage:
  SECTOR_PROXY=off /workspace/macro-os/.venv/bin/python shadow_c_enhancement_bt.py
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Force no proxy for this research run
os.environ["SECTOR_PROXY"] = "off"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parent
OUT_DIR = Path("/workspace/output/docs")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- universe (spy sleeve) ----
SECTOR_ETF = {
    "科技": "XLK", "金融": "XLF", "能源": "XLE", "医疗保健": "XLV",
    "工业": "XLI", "材料": "XLB", "必需消费": "XLP", "公用事业": "XLU",
    "房地产": "XLRE", "可选消费": "XLY", "通信服务": "XLC",
}
SECTOR_TICKERS = list(SECTOR_ETF.values())
CORE = "SPY"
CASH = "BIL"
ALL_PX = SECTOR_TICKERS + [CORE, CASH, "QQQ", "SOXX"]  # SOXX/QQQ for gate proxy

# ---- book / gate params (mirror production; do not mutate STRATEGY_BOOKS) ----
TOP3_SINGLE_CAP = 0.15
SCORE_BAN = 45
MOM6 = 126
MOM12 = 252
COST_DEFAULT = 0.001
GATE_PARAMS = {
    "bear_thr": 1.5,
    "warn_lo": 1.0,
    "soft_w": 0.5,
    "eq_acute_smooth": -0.1,
    "eq_acute_raw": -0.1,
    "eq_soft_lo": -0.1,
    "eq_soft_hi": -0.05,
    "denom_w": 1.0,
    "nq_w": 1.0,
    "eq_acute_w": 1.0,
    "sat_full_below": 0.5,
    "sat_zero_at": 1.5,
    "gap_denom_vix": 30.0,
    "gap_denom_vix_dd": 25.0,
    "gap_denom_dd": -0.12,
    "nq_vix_hard": 30.0,
    "nq_vix_soft": 26.0,
    "nq_dd": -0.1,
}
# pine score constants (sector_rotation_daily)
TREND_LEN, SLOW_LEN, VOL_LEN = 20, 50, 20
VOL_CONFIRM = 1.30
CROWD_REL60, CROWD_DIST50 = 12.0, 6.0
# crowd soft for mom (rotation_rank)
CROWD_REL60_START, CROWD_REL60_SCALE, CROWD_PEN_PP_CAP = 15.0, 1.5, 5.0
S3_SOFT_CROWD_PP = 0.05
PINE_HALF_CROWD = 12.5

REPORT_DATE = "2026-09-15"
SAMPLE_START = "2023-01-01"  # ~3y main; extend if data allows after warm-up


def _load_gate_params_file() -> dict:
    p = ROOT / "gate_params.json"
    if p.is_file():
        try:
            d = json.loads(p.read_text())
            return {**GATE_PARAMS, **(d.get("params") or {})}
        except Exception:
            pass
    return dict(GATE_PARAMS)


# ===================== download =====================
def download_closes(tickers: List[str], start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    start_d = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    end_d = (pd.Timestamp(end) + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    raw = yf.download(
        tickers, start=start_d, end=end_d, auto_adjust=True,
        progress=False, threads=False, group_by="column",
    )
    if raw is None or len(raw) == 0:
        raise RuntimeError("yfinance download empty")
    if isinstance(raw.columns, pd.MultiIndex):
        cl = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw["Adj Close"]
        if "Volume" in raw.columns.get_level_values(0):
            vol = raw["Volume"]
        else:
            vol = None
    else:
        cl = raw
        vol = None
    if isinstance(cl, pd.Series):
        cl = cl.to_frame(tickers[0])
    cl = cl.reindex(columns=[t for t in tickers if t in cl.columns or True])
    # keep only requested that exist
    have = [t for t in tickers if t in cl.columns]
    cl = cl[have].dropna(how="all")
    cl.index = pd.to_datetime(cl.index).tz_localize(None)
    cl = cl.sort_index().ffill()
    return cl


def download_close_vol(tickers: List[str], start: str, end: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf

    start_d = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    end_d = (pd.Timestamp(end) + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    raw = yf.download(
        tickers, start=start_d, end=end_d, auto_adjust=True,
        progress=False, threads=False, group_by="column",
    )
    if raw is None or len(raw) == 0:
        raise RuntimeError("yfinance download empty")
    if isinstance(raw.columns, pd.MultiIndex):
        cl = raw["Close"].copy()
        vol = raw["Volume"].copy() if "Volume" in raw.columns.get_level_values(0) else pd.DataFrame()
    else:
        cl = raw[["Close"]].rename(columns={"Close": tickers[0]}) if "Close" in raw.columns else raw
        vol = raw[["Volume"]].rename(columns={"Volume": tickers[0]}) if "Volume" in raw.columns else pd.DataFrame()
    cl.index = pd.to_datetime(cl.index).tz_localize(None)
    if len(vol):
        vol.index = pd.to_datetime(vol.index).tz_localize(None)
    cl = cl.sort_index().ffill()
    vol = vol.reindex(cl.index).ffill() if len(vol) else pd.DataFrame(index=cl.index)
    return cl, vol


def download_vix(start: str, end: str) -> pd.Series:
    import yfinance as yf

    start_d = (pd.Timestamp(start) - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    end_d = (pd.Timestamp(end) + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    for tic in ("^VIX", "VIX"):
        try:
            raw = yf.download(tic, start=start_d, end=end_d, auto_adjust=True, progress=False, threads=False)
            if raw is None or len(raw) == 0:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                px = raw["Close"].iloc[:, 0] if isinstance(raw["Close"], pd.DataFrame) else raw["Close"]
            else:
                px = raw["Close"]
            s = px.dropna().astype(float)
            s.index = pd.to_datetime(s.index).tz_localize(None)
            return s
        except Exception:
            continue
    raise RuntimeError("VIX download failed")


# ===================== proxy gate =====================
def dd20(px: pd.Series) -> pd.Series:
    return px / px.rolling(20, min_periods=5).max() - 1.0


def build_proxy_gate(
    qqq: pd.Series,
    soxx: pd.Series,
    vix: pd.Series,
    params: dict,
) -> pd.DataFrame:
    """Full-sample transparent proxy gate (no equity_overlay CSV).

    Mirrors gate_legs gap-proxy + NQ-proxy + equity acute/soft from
    SOXX/QQQ 20d drawdowns and VIX. Documented as PROXY — not official
    denom overlay / pipeline HARD_VETO series (those CSVs are absent).
    """
    idx = qqq.index.union(soxx.index).union(vix.index).sort_values()
    qqq = qqq.reindex(idx).ffill()
    soxx = soxx.reindex(idx).ffill()
    vix_f = vix.reindex(idx).ffill().fillna(20.0)
    raw_soxx = dd20(soxx)
    raw_qqq = dd20(qqq)
    smooth = pd.concat([raw_soxx, raw_qqq], axis=1).min(axis=1)
    raw = raw_soxx

    denom = (
        (vix_f >= params["gap_denom_vix"])
        | ((vix_f >= params["gap_denom_vix_dd"]) & (smooth <= params["gap_denom_dd"]))
    )
    nq = (vix_f >= params["nq_vix_hard"]) | (
        (vix_f >= params["nq_vix_soft"]) & (smooth <= params["nq_dd"])
    )
    eq_acute = (raw.fillna(smooth) <= params["eq_acute_raw"]) | (smooth <= params["eq_acute_smooth"])
    eq_soft = (smooth >= params["eq_soft_lo"]) & (smooth < params["eq_soft_hi"]) & (~eq_acute)

    score = (
        params["denom_w"] * denom.astype(float)
        + params["nq_w"] * nq.astype(float)
        + params["eq_acute_w"] * eq_acute.astype(float)
        + params["soft_w"] * eq_soft.astype(float)
    )
    # sat_mult from score (no shift yet — shift applied at decision)
    lo, hi = params["sat_full_below"], params["sat_zero_at"]
    sat_m = pd.Series(
        np.where(score <= lo, 1.0, np.where(score >= hi, 0.0, (hi - score) / (hi - lo))),
        index=idx,
        dtype=float,
    )
    state = pd.Series(0, index=idx, dtype=int)
    state = state.mask(score >= params["warn_lo"], 1)
    state = state.mask(score >= params["bear_thr"], 2)
    label = state.map({0: "OFF", 1: "WARN", 2: "BEAR"})

    return pd.DataFrame(
        {
            "score": score,
            "sat_mult": sat_m,
            "state": state,
            "label": label,
            "denom": denom.astype(float),
            "nq": nq.astype(float),
            "eq_acute": eq_acute.astype(float),
            "eq_soft": eq_soft.astype(float),
            "vix": vix_f,
            "smooth": smooth,
        },
        index=idx,
    )



def resolve_gate(
    idx: pd.DatetimeIndex,
    qqq: pd.Series,
    soxx: pd.Series,
    vix: pd.Series,
    params: dict,
    warm_start: str,
    end: str,
) -> Tuple[pd.DataFrame, str, dict]:
    """Prefer gate_legs.build_leg_frame (official overlay + pipeline NQ); else proxy.

    Returns (gate_df, caveat, coverage_meta).
    Decision-day sat_mult/label are unshifted (simulate earns T+1).
    """
    meta: dict = {"mode": "proxy", "overlay_files_found": False}
    try:
        import gate_legs as gl

        overlay_ok = (gl.MACRO / "equity_overlay_2022.csv").is_file() and (
            gl.MACRO / "equity_overlay_backtest.csv"
        ).is_file()
        pipe_ok = (gl.MACRO / "pipeline_backtest_daily_v5.csv").is_file()
        meta["overlay_files_found"] = bool(overlay_ok)
        meta["pipeline_file_found"] = bool(pipe_ok)
        meta["macro_path"] = str(gl.MACRO)
        if not (overlay_ok and pipe_ok):
            raise FileNotFoundError(
                f"missing official CSVs under {gl.MACRO}: overlay={overlay_ok} pipeline={pipe_ok}"
            )

        legs = gl.build_leg_frame(warm_start, end, params)
        score = gl.score_from_legs(legs, params)
        sat_m = gl.sat_mult_series(score, params, continuous=True)
        state = gl.state_from_score(score, params, shift=False)
        label = state.map({0: "OFF", 1: "WARN", 2: "BEAR"})
        gate = pd.DataFrame(
            {
                "score": score,
                "sat_mult": sat_m,
                "state": state,
                "label": label,
                "denom": legs["denom"].astype(float),
                "nq": legs["nq"].astype(float),
                "eq_acute": legs["eq_acute"].astype(float),
                "eq_soft": legs["eq_soft"].astype(float),
                "vix": legs["vix"],
                "smooth": legs["smooth"],
                "denom_source": legs["denom_source"],
                "nq_official": legs["nq_official"].astype(float),
                "nq_proxy": legs["nq_proxy"].astype(float),
            },
            index=legs.index,
        )
        gate = gate.reindex(idx).ffill()
        # coverage on aligned idx
        ds = gate["denom_source"]
        ov_mask = ds == "overlay"
        px_mask = ds == "proxy"
        meta.update(
            {
                "mode": "real_hybrid",
                "legs_start": str(legs.index[0].date()),
                "legs_end": str(legs.index[-1].date()),
                "overlay_denom_days": int(ov_mask.sum()),
                "proxy_denom_days": int(px_mask.sum()),
                "overlay_date_min": str(gate.index[ov_mask][0].date()) if ov_mask.any() else None,
                "overlay_date_max": str(gate.index[ov_mask][-1].date()) if ov_mask.any() else None,
                "proxy_gap_note": (
                    "denom uses official rule_regime on overlay CSV dates "
                    "(2021-01~2022-12 and 2024-10~2026-07); remaining days use VIX/dd proxy "
                    "(no ffill of tight regimes across the 2023 gap)."
                ),
                "nq_official_days": int((gate["nq_official"] > 0.5).sum()) if "nq_official" in gate else 0,
                "nq_span": "2024-10-01→2026-07-30 (pipeline_backtest_daily_v5)",
            }
        )
        caveat = (
            "REAL(er) gate via gate_legs.build_leg_frame: official equity_overlay_*.csv "
            f"denom on {meta['overlay_denom_days']} days "
            f"({meta['overlay_date_min']}→{meta['overlay_date_max']}); "
            f"proxy denom on {meta['proxy_denom_days']} gap days (incl. most of 2023–2024-09). "
            "NQ HARD_VETO from pipeline_backtest_daily_v5 inside its window; VIX/dd proxy outside. "
            "sat_mult/state from production continuous score formula + gate_params.json. "
            "Not identical to live paper-ledger tick-by-tick, but no longer a full VIX-only proxy."
        )
        return gate, caveat, meta
    except Exception as e:
        meta["fallback_error"] = repr(e)
        gate = build_proxy_gate(qqq, soxx, vix, params).reindex(idx).ffill()
        caveat = (
            "FALLBACK FULL transparent proxy gate — gate_legs real path failed: "
            f"{e!r}. Official CSVs missing or unloadable; denom/nq from VIX + SOXX/QQQ 20d dd only. "
            "Do not treat as live paper-ledger gate replay."
        )
        meta["mode"] = "proxy_fallback"
        return gate, caveat, meta


# ===================== pine / mom ranks =====================
def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=max(3, n // 3)).mean()


def _pct_n(s: pd.Series, n: int) -> pd.Series:
    return s / s.shift(n) - 1.0


def pine_panel(close: pd.DataFrame, volume: pd.DataFrame, spy: pd.Series) -> Dict[str, pd.DataFrame]:
    """Daily pine-like score / state / rel60 / over_ext for each sector ticker."""
    scores, states, rel60s, overs = {}, {}, {}, {}
    for tic in SECTOR_TICKERS:
        if tic not in close.columns:
            continue
        idx = close[tic]
        dv = volume[tic] if tic in volume.columns else pd.Series(np.nan, index=close.index)
        c_spy = spy.reindex(idx.index)
        ratio = idx / c_spy.replace(0, np.nan)
        rel1 = _pct_n(ratio, 1)
        rel5 = _pct_n(ratio, 5)
        rel20 = _pct_n(ratio, 20)
        rel60 = _pct_n(ratio, 60)
        abs5 = _pct_n(idx, 5)
        ma20 = _sma(ratio, TREND_LEN)
        ma50 = _sma(ratio, SLOW_LEN)
        vol_r = dv.ffill() / _sma(dv.ffill(), VOL_LEN)
        dist50 = (ratio - ma50) / ma50 * 100.0
        valid = ratio.notna() & c_spy.notna()

        trend_score = (
            (ratio > ma20).astype(float) * 10.0
            + (ratio > ma50).astype(float) * 10.0
            + (ma20 > ma20.shift(5)).astype(float) * 10.0
        )
        flow_score = (
            (rel5 > 0).astype(float) * 10.0
            + (rel20 > 0).astype(float) * 8.0
            + (rel5 > rel5.shift(5)).astype(float) * 8.0
            + (rel5 > rel20 / 4.0).astype(float) * 8.0
        )
        rel1_pos = (rel1 > 0).fillna(False)
        vol_score = pd.Series(0.0, index=ratio.index)
        vol_score = vol_score.mask(rel1_pos & (vol_r >= 1.0).fillna(False), 10.0)
        vol_score = vol_score.mask(rel1_pos & (vol_r >= VOL_CONFIRM).fillna(False), 20.0)
        # ETF self-breadth proxy (price vs own MA20)
        br = (idx > _sma(idx, TREND_LEN)).astype(float) * 100.0
        br_score = pd.Series(0.0, index=ratio.index)
        br_score = br_score.mask((br >= 50.0).fillna(False), 8.0)
        br_score = br_score.mask((br >= 70.0).fillna(False), 15.0)
        over_ext = (rel60 > CROWD_REL60).fillna(False) | (dist50 > CROWD_DIST50).fillna(False)
        crowd_score = pd.Series(8.0, index=ratio.index).mask(over_ext, 0.0)
        raw = trend_score + flow_score + vol_score + br_score + crowd_score
        score = raw.clip(0, 100).where(valid)

        distribution = valid & (ratio < ma20) & (rel5 < 0) & (vol_r > VOL_CONFIRM) & (rel1 < 0)
        state = np.select(
            [
                ~valid,
                distribution.fillna(False),
                (over_ext & (score >= 60)).fillna(False),
                (score >= 75).fillna(False),
                (score >= 60).fillna(False),
                ((score < 45) & (rel5 < 0) & (rel20 < 0)).fillna(False),
            ],
            [9, -2, 3, 2, 1, -1],
            default=0,
        ).astype(int)

        scores[tic] = score
        states[tic] = pd.Series(state, index=ratio.index)
        rel60s[tic] = rel60 * 100.0  # percent space like rotation_rank
        overs[tic] = over_ext.astype(float)

    return {
        "score": pd.DataFrame(scores),
        "state": pd.DataFrame(states),
        "rel60": pd.DataFrame(rel60s),
        "over_ext": pd.DataFrame(overs),
    }


def crowd_pen_pine(rel60_pp: float, over: bool) -> float:
    if rel60_pp == rel60_pp:  # not nan
        pp = min(CROWD_PEN_PP_CAP, max(0.0, (rel60_pp - CROWD_REL60_START) / CROWD_REL60_SCALE))
        return float(PINE_HALF_CROWD) * (pp / CROWD_PEN_PP_CAP)
    return float(PINE_HALF_CROWD) if over else 0.0


def crowd_pen_mom(rel60_pp: float, over: bool) -> float:
    if rel60_pp == rel60_pp:
        pp = min(CROWD_PEN_PP_CAP, max(0.0, (rel60_pp - CROWD_REL60_START) / CROWD_REL60_SCALE))
        return float(S3_SOFT_CROWD_PP) * (pp / CROWD_PEN_PP_CAP)
    return float(S3_SOFT_CROWD_PP) if over else 0.0


def select_top3_pine(day: pd.Timestamp, panel: dict) -> List[Tuple[str, float]]:
    """Return [(ticker, rank_score), ...] Top3 pine_half_crowd."""
    sc = panel["score"].loc[day]
    st = panel["state"].loc[day]
    r60 = panel["rel60"].loc[day]
    ov = panel["over_ext"].loc[day]
    cands = []
    for tic in SECTOR_TICKERS:
        if tic not in sc.index:
            continue
        score = float(sc.get(tic, np.nan))
        if not np.isfinite(score):
            continue
        state = int(st.get(tic, 0) or 0)
        # ban withdraw / score<45
        if state == -1 or score < SCORE_BAN:
            continue
        rel = float(r60.get(tic, np.nan))
        over = bool(ov.get(tic, 0))
        adj = score - crowd_pen_pine(rel, over)
        cands.append((tic, adj, score))
    cands.sort(key=lambda x: x[1], reverse=True)
    return [(t, adj) for t, adj, _ in cands[:3]]


def select_top3_mom12(day: pd.Timestamp, close: pd.DataFrame, panel: dict) -> List[Tuple[str, float]]:
    """Top3 by 252d total return − soft crowd (like tech S3)."""
    if day not in close.index:
        return []
    loc = close.index.get_loc(day)
    if isinstance(loc, slice):
        return []
    if loc < MOM12:
        return []
    px0 = close.iloc[loc - MOM12]
    px1 = close.iloc[loc]
    # bottom 20% mom ban among sectors with valid mom
    moms = {}
    for tic in SECTOR_TICKERS:
        if tic not in close.columns:
            continue
        a, b = float(px0.get(tic, np.nan)), float(px1.get(tic, np.nan))
        if np.isfinite(a) and np.isfinite(b) and a > 0:
            moms[tic] = b / a - 1.0
    if len(moms) < 3:
        return []
    thr = np.nanpercentile(list(moms.values()), 20)
    r60 = panel["rel60"].loc[day] if day in panel["rel60"].index else None
    ov = panel["over_ext"].loc[day] if day in panel["over_ext"].index else None
    st = panel["state"].loc[day] if day in panel["state"].index else None
    cands = []
    for tic, m in moms.items():
        if m <= thr:
            continue
        state = int(st.get(tic, 0) or 0) if st is not None else 0
        if state == -1:
            continue
        rel = float(r60.get(tic, np.nan)) if r60 is not None else np.nan
        over = bool(ov.get(tic, 0)) if ov is not None else False
        rank = m - crowd_pen_mom(rel, over)
        cands.append((tic, rank))
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands[:3]


def top3_weights(picks: List[Tuple[str, float]], sat_share: float) -> Dict[str, float]:
    if sat_share <= 1e-12 or not picks:
        return {}
    raw = [max(float(s), 0.0) for _, s in picks]
    if sum(raw) <= 1e-12:
        w = [sat_share / len(picks)] * len(picks)
    else:
        s = sum(raw)
        w = [sat_share * (x / s) for x in raw]
    # iterative single-name cap
    cap = TOP3_SINGLE_CAP
    n = len(w)
    for _ in range(5):
        over = [i for i, wi in enumerate(w) if wi > cap + 1e-12]
        if not over:
            break
        excess = sum(w[i] - cap for i in over)
        for i in over:
            w[i] = cap
        free = [i for i in range(n) if i not in over]
        if not free or excess <= 1e-12:
            break
        base = sum(w[i] for i in free) or 1.0
        for i in free:
            w[i] += excess * (w[i] / base)
    tw = sum(w) or 1.0
    w = [wi / tw * sat_share for wi in w]
    return {picks[i][0]: w[i] for i in range(n)}


# ===================== book weights =====================
def book_weights(
    book: str,
    core_ticker: str,
    picks: List[Tuple[str, float]],
    sat_mult: float,
    label: str,
) -> Dict[str, float]:
    """Build target weights for C / B / C_dyn40 / C_mom12 (spy sleeve)."""
    sm = float(np.clip(sat_mult, 0.0, 1.0))
    if book in ("C", "C_mom12"):
        core_pct, sat_pct = 0.70, 0.30
    elif book == "B":
        core_pct, sat_pct = 0.60, 0.40
    elif book == "C_dyn40":
        # OFF + sat_mult≈1 → 40% sat; else base 30%
        if label == "OFF" and sm >= 0.99:
            core_pct, sat_pct = 0.60, 0.40
        else:
            core_pct, sat_pct = 0.70, 0.30
    else:
        raise ValueError(book)

    sat_share = sat_pct * sm
    w: Dict[str, float] = {core_ticker: core_pct}
    w.update(top3_weights(picks, sat_share))
    used = sum(w.values())
    bil = max(0.0, 1.0 - used)
    if bil > 1e-9:
        w[CASH] = bil
    # renormalize tiny drift
    s = sum(w.values())
    if abs(s - 1.0) > 1e-8 and s > 0:
        w = {k: v / s for k, v in w.items()}
    return w


# ===================== simulation =====================
def rebalance_dates(idx: pd.DatetimeIndex, freq: str) -> List[pd.Timestamp]:
    """Weekly = last trading day of ISO week; monthly = last trading day of month."""
    s = pd.Series(1, index=idx)
    if freq == "W":
        # Friday or last available in week
        g = s.groupby(idx.to_period("W-FRI"))
        return [g.get_group(k).index[-1] for k in g.groups]
    if freq == "M":
        g = s.groupby(idx.to_period("M"))
        return [g.get_group(k).index[-1] for k in g.groups]
    raise ValueError(freq)


def simulate(
    close: pd.DataFrame,
    targets: Dict[pd.Timestamp, Dict[str, float]],
    cost_one_way: float,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Prior weights earn today's return; cost on turnover when targets change.

    targets keyed by signal date T; weights first earn the return on the next
    session after T (no look-ahead).
    """
    idx = close.index
    rets = close.pct_change()
    ordered_signals = sorted(targets.keys())

    # Build per-day signal weights, then earning weights = prior day (no look-ahead)
    w_signal = []
    cur = {CASH: 1.0}
    si = 0
    for d in idx:
        while si < len(ordered_signals) and ordered_signals[si] <= d:
            cur = dict(targets[ordered_signals[si]])
            si += 1
        w_signal.append(dict(cur))
    w_earn = [{CASH: 1.0}] + w_signal[:-1]

    nav = pd.Series(1.0, index=idx, dtype=float)
    turns = pd.Series(0.0, index=idx, dtype=float)
    sat_w = pd.Series(0.0, index=idx, dtype=float)
    prev_w = dict(w_earn[0])
    for i, d in enumerate(idx):
        w = dict(w_earn[i])
        if i == 0:
            nav.iloc[i] = 1.0
            prev_w = dict(w)
            sat_w.iloc[i] = sum(v for k, v in w.items() if k in SECTOR_TICKERS)
            continue
        keys = set(prev_w) | set(w)
        turn = 0.5 * sum(abs(w.get(k, 0.0) - prev_w.get(k, 0.0)) for k in keys)
        turns.iloc[i] = turn
        r = 0.0
        for t, wt in w.items():
            if abs(wt) < 1e-12:
                continue
            if t not in rets.columns or not np.isfinite(rets.at[d, t]):
                continue
            r += wt * float(rets.at[d, t])
        r -= cost_one_way * turn
        nav.iloc[i] = nav.iloc[i - 1] * (1.0 + r)
        sat_w.iloc[i] = sum(v for k, v in w.items() if k in SECTOR_TICKERS)
        prev_w = dict(w)
    return nav, turns, sat_w


def metrics(nav: pd.Series, bil_rets: pd.Series, turns: pd.Series, sat_w: pd.Series) -> dict:
    ret = nav.pct_change().fillna(0.0)
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    cagr = float((max(nav.iloc[-1], 1e-12) / nav.iloc[0]) ** (1 / years) - 1.0)
    vol = float(ret.std() * np.sqrt(252))
    sharpe0 = float(ret.mean() * 252 / vol) if vol > 1e-12 else 0.0
    # excess vs BIL
    br = bil_rets.reindex(nav.index).fillna(0.0)
    ex = ret - br
    vol_ex = float(ex.std() * np.sqrt(252))
    sharpe_bil = float(ex.mean() * 252 / vol_ex) if vol_ex > 1e-12 else 0.0
    dd = nav / nav.cummax() - 1.0
    mdd = float(dd.min())
    calmar = float(cagr / abs(mdd)) if abs(mdd) > 1e-12 else 0.0
    # annualized one-way turnover ≈ mean daily L1/2 * 252 (we stored half-L1 already as turns)
    # turns is 0.5 * L1; one-way ≈ turns sum / years, or mean*252
    ann_turn = float(turns.sum() / years)  # cumulative half-L1 / years ≈ annualized one-way
    return {
        "total_return": total,
        "cagr": cagr,
        "vol": vol,
        "sharpe_rf0": sharpe0,
        "sharpe_vs_bil": sharpe_bil,
        "max_dd": mdd,
        "calmar": calmar,
        "ann_turnover_one_way": ann_turn,
        "avg_sat_weight": float(sat_w.mean()),
        "avg_equity_weight": float(1.0 - (1.0 - sat_w - 0).mean()) if False else float(
            # equity ≈ 1 - bil; approximate from sat + assume core present
            min(1.0, sat_w.mean() + 0.70)  # placeholder overwritten by caller if needed
        ),
        "n_days": int(len(nav)),
        "start": str(nav.index[0].date()),
        "end": str(nav.index[-1].date()),
    }


def regime_contrib(nav: pd.Series, labels: pd.Series) -> dict:
    """Sum of daily log/simple returns by gate label (aligned)."""
    ret = nav.pct_change().fillna(0.0)
    lab = labels.reindex(nav.index).ffill().fillna("OFF")
    out = {}
    for k in ("OFF", "WARN", "BEAR"):
        m = lab == k
        out[k] = {
            "n_days": int(m.sum()),
            "sum_return": float(ret[m].sum()),
            "mean_return": float(ret[m].mean()) if m.any() else 0.0,
        }
    return out


# ===================== main run =====================
def build_target_series(
    book: str,
    rebal_days: List[pd.Timestamp],
    gate: pd.DataFrame,
    close: pd.DataFrame,
    panel: dict,
    engine: str,
) -> Dict[pd.Timestamp, Dict[str, float]]:
    targets = {}
    for d in rebal_days:
        if d not in gate.index or d not in close.index:
            continue
        # decision uses same-day close signal (applied next day in simulate)
        sm = float(gate.at[d, "sat_mult"])
        lab = str(gate.at[d, "label"])
        if engine == "pine":
            picks = select_top3_pine(d, panel)
        else:
            picks = select_top3_mom12(d, close, panel)
        targets[d] = book_weights(book, CORE, picks, sm, lab)
    return targets


def overlap_stats(
    rebal_days: List[pd.Timestamp],
    close: pd.DataFrame,
    panel: dict,
) -> dict:
    overlaps = []
    for d in rebal_days:
        if d not in close.index:
            continue
        a = {t for t, _ in select_top3_pine(d, panel)}
        b = {t for t, _ in select_top3_mom12(d, close, panel)}
        if len(a) == 3 and len(b) == 3:
            overlaps.append(len(a & b) / 3.0)
    return {
        "n_compared": len(overlaps),
        "mean_overlap_frac": float(np.mean(overlaps)) if overlaps else float("nan"),
        "pct_days_overlap_ge_2": float(np.mean([o >= 2 / 3 for o in overlaps])) if overlaps else float("nan"),
        "pct_days_identical": float(np.mean([o >= 1.0 - 1e-9 for o in overlaps])) if overlaps else float("nan"),
    }


def run_freq(
    freq: str,
    close: pd.DataFrame,
    gate: pd.DataFrame,
    panel: dict,
    sample_idx: pd.DatetimeIndex,
    costs: List[float],
) -> dict:
    rebal = [d for d in rebalance_dates(sample_idx, freq) if d in sample_idx]
    # need gate/panel available
    rebal = [d for d in rebal if d in gate.index and d in panel["score"].index]

    books_engines = {
        "C": ("pine", "C"),
        "B": ("pine", "B"),
        "C_dyn40": ("pine", "C_dyn40"),
        "C_mom12": ("mom12", "C_mom12"),
    }
    bil_rets = close[CASH].pct_change().fillna(0.0)
    spy_nav = (1 + close[CORE].pct_change().fillna(0.0)).cumprod()
    spy_nav = spy_nav.reindex(sample_idx).dropna()
    spy_nav = spy_nav / spy_nav.iloc[0]

    ov = overlap_stats(rebal, close, panel)
    out = {"freq": freq, "n_rebalance": len(rebal), "overlap_pine_vs_mom12": ov, "books": {}}

    # label series for regime (use decision-day label shifted to application — approx same-day label on return day)
    labels = gate["label"].reindex(sample_idx).ffill()

    for cost in costs:
        cost_key = f"cost_{cost:.3f}"
        for bname, (eng, bkey) in books_engines.items():
            targets = build_target_series(bkey, rebal, gate, close, panel, eng)
            # restrict close to sample
            cl = close.reindex(sample_idx).ffill()
            nav, turns, sat_w = simulate(cl, targets, cost)
            # equity weight = 1 - BIL
            # rebuild avg equity from targets path: use sat_w + core estimate
            # Better: recompute bil from nav path — store during sim
            m = metrics(nav, bil_rets, turns, sat_w)
            # fix avg_equity: approximate from mean(1 - bil). Reconstruct bil from weights over time is heavy;
            # use: for C avg equity ≈ core + avg_sat (core fixed 0.7 or 0.6)
            if bkey in ("C", "C_mom12"):
                # dyn not; core mostly 0.7 but BIL absorbs sat cut
                m["avg_equity_weight"] = float(min(1.0, 0.70 + sat_w.mean()))  # overestimates when sat cut... 
            # More honest: re-simulate weight equity
            # Quick pass: equity = sum non-BIL
            # We'll compute from targets held — patch below
            m["regime_contrib"] = regime_contrib(nav, labels)
            m["final_nav"] = float(nav.iloc[-1])
            out["books"].setdefault(bname, {})
            out["books"][bname][cost_key] = {
                "metrics": m,
                "nav": {str(i.date()): float(v) for i, v in nav.items()},
            }

    # Honest avg equity / excess vs C: post-process using earning weights (signal shifted +1)
    for bname, (eng, bkey) in books_engines.items():
        targets = build_target_series(bkey, rebal, gate, close, panel, eng)
        ordered = sorted(targets.keys())
        cur = {CASH: 1.0}
        si = 0
        w_sig = []
        for d in sample_idx:
            while si < len(ordered) and ordered[si] <= d:
                cur = dict(targets[ordered[si]])
                si += 1
            w_sig.append(dict(cur))
        # earning weights = prior day's signal weights
        w_earn = [{CASH: 1.0}] + w_sig[:-1]
        eq_list = [sum(v for k, v in w.items() if k != CASH) for w in w_earn]
        sat_list = [sum(v for k, v in w.items() if k in SECTOR_TICKERS) for w in w_earn]
        for cost in costs:
            cost_key = f"cost_{cost:.3f}"
            out["books"][bname][cost_key]["metrics"]["avg_equity_weight"] = float(np.mean(eq_list))
            out["books"][bname][cost_key]["metrics"]["avg_sat_weight"] = float(np.mean(sat_list))

    # excess vs C at default cost
    c_nav = None
    dk = f"cost_{COST_DEFAULT:.3f}"
    if "C" in out["books"] and dk in out["books"]["C"]:
        c_series = pd.Series(out["books"]["C"][dk]["nav"])
        c_series.index = pd.to_datetime(c_series.index)
        for bname in books_engines:
            s = pd.Series(out["books"][bname][dk]["nav"])
            s.index = pd.to_datetime(s.index)
            excess_total = float(s.iloc[-1] / c_series.iloc[-1] - 1.0) if bname != "C" else 0.0
            te = float((s.pct_change() - c_series.pct_change()).std() * np.sqrt(252))
            out["books"][bname][dk]["metrics"]["excess_vs_C_total"] = excess_total
            out["books"][bname][dk]["metrics"]["tracking_error_vs_C"] = te if bname != "C" else 0.0

    # spy BH metrics at sample
    spy = close[CORE].reindex(sample_idx).dropna()
    spy_nav = spy / spy.iloc[0]
    out["spy_bh"] = metrics(
        spy_nav,
        bil_rets,
        pd.Series(0.0, index=spy_nav.index),
        pd.Series(0.0, index=spy_nav.index),
    )
    out["spy_bh"]["avg_equity_weight"] = 1.0
    out["spy_bh"]["avg_sat_weight"] = 0.0
    return out


def go_nogo(main: dict) -> dict:
    dk = f"cost_{COST_DEFAULT:.3f}"
    c = main["books"]["C"][dk]["metrics"]
    b = main["books"]["B"][dk]["metrics"]
    d = main["books"]["C_dyn40"][dk]["metrics"]
    m = main["books"]["C_mom12"][dk]["metrics"]
    ov = main["overlap_pine_vs_mom12"]

    # H1 C_dyn40
    cagr_ok = d["cagr"] > c["cagr"]
    sh_ok = d["sharpe_rf0"] > c["sharpe_rf0"] + 0.05  # "clearly" higher
    mdd_ok = abs(d["max_dd"]) <= abs(c["max_dd"]) * 1.2 + 1e-12
    vs_b = (abs(d["max_dd"]) < abs(b["max_dd"]) - 1e-6) or (
        d["regime_contrib"]["OFF"]["sum_return"] > b["regime_contrib"]["OFF"]["sum_return"]
    )
    h1_go = (cagr_ok or sh_ok) and mdd_ok and vs_b

    # H2 C_mom12
    excess_ok = m["excess_vs_C_total"] > 0.0
    turn_ok = (abs(m["cagr"] - c["cagr"]) < 0.01) and (m["ann_turnover_one_way"] < c["ann_turnover_one_way"] * 0.9)
    overlap_high = (ov.get("mean_overlap_frac") or 0) > 0.80
    h2_go = (excess_ok or turn_ok) and not (overlap_high and not excess_ok and not turn_ok)
    # If overlap >80% and no excess/turnover win → reject
    if overlap_high and not excess_ok and not turn_ok:
        h2_go = False

    return {
        "H1_C_dyn40": {
            "go": bool(h1_go),
            "cagr_gt_C": bool(cagr_ok),
            "sharpe_clearly_gt_C": bool(sh_ok),
            "mdd_le_1_2x_C": bool(mdd_ok),
            "advantage_vs_B": bool(vs_b),
            "reason": (
                "GO" if h1_go else
                "NO-GO: "
                + ", ".join(
                    [
                        x
                        for x, ok in [
                            ("need higher CAGR or Sharpe vs C", cagr_ok or sh_ok),
                            ("MaxDD within 1.2× C", mdd_ok),
                            ("edge vs fixed B", vs_b),
                        ]
                        if not ok
                    ]
                )
            ),
        },
        "H2_C_mom12": {
            "go": bool(h2_go),
            "excess_vs_C": bool(excess_ok),
            "similar_return_less_turnover": bool(turn_ok),
            "mean_top3_overlap_vs_pine": ov.get("mean_overlap_frac"),
            "overlap_gt_80pct": bool(overlap_high),
            "reason": (
                "GO" if h2_go else
                "NO-GO: "
                + (
                    "Top3 overlap>80% and no excess/turnover gain"
                    if overlap_high and not excess_ok and not turn_ok
                    else "no stable excess vs C and no turnover win"
                )
            ),
        },
    }


def fmt_pct(x: float) -> str:
    return f"{100 * x:+.2f}%"


def fmt_num(x: float) -> str:
    return f"{x:.3f}"


def write_report(payload: dict, path: Path) -> None:
    main = payload["weekly"]
    dk = f"cost_{COST_DEFAULT:.3f}"
    gn = payload["go_nogo"]
    lines = []
    mode = payload.get("gate_mode") or "unknown"
    lines.append(f"# C 增强影子回测报告（{REPORT_DATE}）— gate={mode}\n")
    lines.append("## 中文摘要（交付）\n")
    lines.append(f"- **样本：** {payload['sample_start']} → {payload['sample_end']}（标普袖，主频率=周频调仓）")
    lines.append(f"- **闸：** {payload['gate_caveat']}")
    lines.append(f"- **成本主表：** 单边 {COST_DEFAULT:.3f}；另报 0 / 0.002")
    lines.append(f"- **附录 combined_budget≥0.50：** {payload['budget_appendix']}")
    lines.append(f"- **H1 C_dyn40：** {'✅ GO' if gn['H1_C_dyn40']['go'] else '❌ NO-GO'} — {gn['H1_C_dyn40']['reason']}")
    lines.append(f"- **H2 C_mom12：** {'✅ GO' if gn['H2_C_mom12']['go'] else '❌ NO-GO'} — {gn['H2_C_mom12']['reason']}")
    ov = main["overlap_pine_vs_mom12"]
    lines.append(
        f"- **pine vs mom12 Top3 日重叠率：** mean={ov.get('mean_overlap_frac'):.1%} "
        f"（n={ov.get('n_compared')}）\n"
    )

    lines.append("## English detail\n")
    lines.append("### Setup\n")
    lines.append("| Item | Value |")
    lines.append("|---|---|")
    lines.append(f"| Books | C, B, C_dyn40, C_mom12 (spy sleeve) |")
    lines.append(f"| Rebalance | Weekly primary; monthly appendix |")
    lines.append(f"| Cost | {COST_DEFAULT} one-way (+ 0 / 0.002) |")
    lines.append(f"| Look-ahead | Signal close T, weights earn from T+1 |")
    lines.append(f"| Cash | Explicit BIL residual |")
    lines.append(f"| Gate | {payload.get('gate_mode','see caveat')} (see caveat) |")
    lines.append(f"| Sample | {payload['sample_start']} → {payload['sample_end']} |")
    lines.append(f"| SECTOR_PROXY | off |")
    lines.append("")
    lines.append(f"**Gate caveat:** {payload['gate_caveat']}\n")
    lines.append(f"**Budget appendix:** {payload['budget_appendix']}\n")

    lines.append("### Main metrics (weekly, cost=0.001)\n")
    lines.append(
        "| Book | CAGR | Vol | Sharpe(rf0) | Sharpe(vs BIL) | MaxDD | Calmar | "
        "AnnTurn(1w) | AvgSat | AvgEq | Excess vs C | TE vs C |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for bname in ("C", "B", "C_dyn40", "C_mom12"):
        m = main["books"][bname][dk]["metrics"]
        lines.append(
            f"| {bname} | {fmt_pct(m['cagr'])} | {fmt_pct(m['vol'])} | {fmt_num(m['sharpe_rf0'])} | "
            f"{fmt_num(m['sharpe_vs_bil'])} | {fmt_pct(m['max_dd'])} | {fmt_num(m['calmar'])} | "
            f"{fmt_num(m['ann_turnover_one_way'])} | {fmt_pct(m['avg_sat_weight'])} | "
            f"{fmt_pct(m['avg_equity_weight'])} | {fmt_pct(m.get('excess_vs_C_total', 0))} | "
            f"{fmt_pct(m.get('tracking_error_vs_C', 0))} |"
        )
    sb = main["spy_bh"]
    lines.append(
        f"| SPY BH | {fmt_pct(sb['cagr'])} | {fmt_pct(sb['vol'])} | {fmt_num(sb['sharpe_rf0'])} | "
        f"{fmt_num(sb['sharpe_vs_bil'])} | {fmt_pct(sb['max_dd'])} | {fmt_num(sb['calmar'])} | "
        f"0 | 0 | 100% | — | — |"
    )
    lines.append("")

    lines.append("### Cost sensitivity (CAGR / Sharpe rf0 / MaxDD)\n")
    lines.append("| Book | cost0 CAGR | cost0 Sh | cost0 MDD | cost0.001 CAGR | cost0.002 CAGR | cost0.002 Sh |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for bname in ("C", "B", "C_dyn40", "C_mom12"):
        m0 = main["books"][bname]["cost_0.000"]["metrics"]
        m1 = main["books"][bname]["cost_0.001"]["metrics"]
        m2 = main["books"][bname]["cost_0.002"]["metrics"]
        lines.append(
            f"| {bname} | {fmt_pct(m0['cagr'])} | {fmt_num(m0['sharpe_rf0'])} | {fmt_pct(m0['max_dd'])} | "
            f"{fmt_pct(m1['cagr'])} | {fmt_pct(m2['cagr'])} | {fmt_num(m2['sharpe_rf0'])} |"
        )
    lines.append("")

    lines.append("### Regime contribution (sum of daily returns by gate label, cost=0.001)\n")
    lines.append("| Book | OFF n / sum | WARN n / sum | BEAR n / sum |")
    lines.append("|---|---|---|---|")
    for bname in ("C", "B", "C_dyn40", "C_mom12"):
        rc = main["books"][bname][dk]["metrics"]["regime_contrib"]
        lines.append(
            f"| {bname} | {rc['OFF']['n_days']} / {fmt_pct(rc['OFF']['sum_return'])} | "
            f"{rc['WARN']['n_days']} / {fmt_pct(rc['WARN']['sum_return'])} | "
            f"{rc['BEAR']['n_days']} / {fmt_pct(rc['BEAR']['sum_return'])} |"
        )
    lines.append("")

    if "monthly" in payload:
        mo = payload["monthly"]
        lines.append("### Appendix: monthly rebalance (cost=0.001)\n")
        lines.append("| Book | CAGR | Sharpe(rf0) | MaxDD | AnnTurn | Excess vs C |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for bname in ("C", "B", "C_dyn40", "C_mom12"):
            m = mo["books"][bname][dk]["metrics"]
            lines.append(
                f"| {bname} | {fmt_pct(m['cagr'])} | {fmt_num(m['sharpe_rf0'])} | {fmt_pct(m['max_dd'])} | "
                f"{fmt_num(m['ann_turnover_one_way'])} | {fmt_pct(m.get('excess_vs_C_total', 0))} |"
            )
        lines.append("")

    lines.append("### Go / No-Go vs design success criteria\n")
    lines.append(f"- **H1 C_dyn40:** {'GO' if gn['H1_C_dyn40']['go'] else 'NO-GO'} — {gn['H1_C_dyn40']['reason']}")
    lines.append(f"  - details: {json.dumps(gn['H1_C_dyn40'], ensure_ascii=False)}")
    lines.append(f"- **H2 C_mom12:** {'GO' if gn['H2_C_mom12']['go'] else 'NO-GO'} — {gn['H2_C_mom12']['reason']}")
    lines.append(f"  - details: {json.dumps(gn['H2_C_mom12'], ensure_ascii=False)}")
    lines.append("")
    # vs prior proxy (if payload carries it)
    pp = payload.get("prior_proxy_go_nogo")
    if pp:
        lines.append("### vs prior proxy-gate run\n")
        lines.append(
            f"- Prior proxy H2 C_mom12: {'GO' if pp.get('H2_C_mom12', {}).get('go') else 'NO-GO'} "
            f"— {pp.get('H2_C_mom12', {}).get('reason')}"
        )
        lines.append(
            f"- This run H2 C_mom12: {'GO' if gn['H2_C_mom12']['go'] else 'NO-GO'} "
            f"— {gn['H2_C_mom12']['reason']}"
        )
        lines.append(f"- Gate mode this run: `{payload.get('gate_mode')}`")
        gm = payload.get("gate_meta") or {}
        if gm:
            lines.append(
                f"- Overlay denom days: {gm.get('overlay_denom_days')}; "
                f"proxy denom days: {gm.get('proxy_denom_days')}; "
                f"overlay span: {gm.get('overlay_date_min')}→{gm.get('overlay_date_max')}"
            )
        lines.append("")

    lines.append("### Artifacts\n")
    lines.append(f"- Script: `/workspace/tradingview-desk/shadow_c_enhancement_bt.py`")
    lines.append(f"- Report: `{path}`")
    lines.append(f"- JSON sibling: same stem `.json` under `/workspace/output/docs/`")
    lines.append("")
    lines.append("> Numbers are script outputs only. Production STRATEGY_BOOKS unchanged. C_rsp_tilt not in this batch.\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    print("[shadow_c] SECTOR_PROXY=", os.environ.get("SECTOR_PROXY"))
    params = _load_gate_params_file()
    end = datetime.utcnow().strftime("%Y-%m-%d")
    # Prefer cache warm-up from 2021 for mom12 / pine MA
    warm_start = "2021-01-01"

    tickers = sorted(set(SECTOR_TICKERS + [CORE, CASH, "QQQ", "SOXX"]))
    print("[shadow_c] downloading", tickers)
    close, volume = download_close_vol(tickers, warm_start, end)
    if CASH not in close.columns or close[CASH].isna().all():
        raise RuntimeError("BIL missing — refuse to price cash as 0%")
    vix = download_vix(warm_start, end)

    # Align
    idx = close.index.intersection(vix.index)
    close = close.reindex(idx).ffill()
    volume = volume.reindex(idx).ffill()
    vix = vix.reindex(idx).ffill()

    gate, gate_caveat, gate_meta = resolve_gate(
        idx, close["QQQ"], close["SOXX"], vix, params, warm_start, end
    )
    print("[shadow_c] gate mode=", gate_meta.get("mode"), gate_meta)

    panel = pine_panel(close, volume, close[CORE])
    # align panel
    for k in panel:
        panel[k] = panel[k].reindex(idx)

    # Sample: from SAMPLE_START but need MOM12 history → start sample when mom12 available
    sample_start_ts = pd.Timestamp(SAMPLE_START)
    first_ok = idx[MOM12] if len(idx) > MOM12 else idx[0]
    sample_start_ts = max(sample_start_ts, first_ok)
    # also need pine MA50 warm
    sample_idx = idx[(idx >= sample_start_ts) & (idx <= pd.Timestamp(end))]
    if len(sample_idx) < 100:
        raise RuntimeError(f"sample too short: {len(sample_idx)}")

    budget_appendix = (
        "SKIPPED — combined_budget history only exists in a handful of recent daily_macro_*.json "
        "snapshots (~days, not years); no cheap long history to require budget≥0.50 for C_dyn40."
    )

    costs = [0.0, 0.001, 0.002]
    print(f"[shadow_c] sample {sample_idx[0].date()} → {sample_idx[-1].date()} n={len(sample_idx)}")
    print("[shadow_c] running weekly…")
    weekly = run_freq("W", close, gate, panel, sample_idx, costs)
    print("[shadow_c] running monthly appendix…")
    monthly = run_freq("M", close, gate, panel, sample_idx, costs)

    # strip heavy nav from monthly in JSON? keep but maybe thin — keep weekly nav, monthly metrics only
    monthly_thin = {
        "freq": monthly["freq"],
        "n_rebalance": monthly["n_rebalance"],
        "overlap_pine_vs_mom12": monthly["overlap_pine_vs_mom12"],
        "spy_bh": monthly["spy_bh"],
        "books": {},
    }
    for b, costs_d in monthly["books"].items():
        monthly_thin["books"][b] = {}
        for ck, blob in costs_d.items():
            monthly_thin["books"][b][ck] = {"metrics": blob["metrics"]}

    # thin weekly nav: keep endpoints + monthly marks to shrink JSON
    weekly_out = {
        "freq": weekly["freq"],
        "n_rebalance": weekly["n_rebalance"],
        "overlap_pine_vs_mom12": weekly["overlap_pine_vs_mom12"],
        "spy_bh": weekly["spy_bh"],
        "books": {},
    }
    for b, costs_d in weekly["books"].items():
        weekly_out["books"][b] = {}
        for ck, blob in costs_d.items():
            nav = blob["nav"]
            # downsample nav to ~weekly points already dense; keep all for research (may be large)
            # keep every 5th + last
            keys = list(nav.keys())
            thin_nav = {k: nav[k] for i, k in enumerate(keys) if i % 5 == 0 or i == len(keys) - 1}
            weekly_out["books"][b][ck] = {"metrics": blob["metrics"], "nav_downsampled": thin_nav}

    gn = go_nogo(weekly)
    # gate state mix
    g_sample = gate.reindex(sample_idx)
    gate_mix = {
        "off_pct": float((g_sample["label"] == "OFF").mean()),
        "warn_pct": float((g_sample["label"] == "WARN").mean()),
        "bear_pct": float((g_sample["label"] == "BEAR").mean()),
        "sat_mult_mean": float(g_sample["sat_mult"].mean()),
    }

    # Compare vs prior proxy JSON if present
    prior_proxy_path = OUT_DIR / f"C_enhancement_backtest_{REPORT_DATE}.json"
    prior_proxy = None
    if prior_proxy_path.is_file():
        try:
            prior_proxy = json.loads(prior_proxy_path.read_text(encoding="utf-8"))
        except Exception:
            prior_proxy = None

    tag = "realgate" if gate_meta.get("mode") == "real_hybrid" else "proxy"
    payload = {
        "schema": "C_enhancement_backtest_realgate_v1",
        "report_date": REPORT_DATE,
        "sample_start": str(sample_idx[0].date()),
        "sample_end": str(sample_idx[-1].date()),
        "sleeve": "spy",
        "tech_sleeve": "see tech_mom_compare sibling",
        "gate_mode": gate_meta.get("mode"),
        "gate_meta": gate_meta,
        "gate_caveat": gate_caveat,
        "gate_mix": gate_mix,
        "budget_appendix": budget_appendix,
        "cost_default": COST_DEFAULT,
        "books": ["C", "B", "C_dyn40", "C_mom12"],
        "weekly": weekly_out,
        "monthly": monthly_thin,
        "go_nogo": gn,
        "prior_proxy_go_nogo": (prior_proxy or {}).get("go_nogo"),
        "prior_proxy_sample": {
            "start": (prior_proxy or {}).get("sample_start"),
            "end": (prior_proxy or {}).get("sample_end"),
        } if prior_proxy else None,
        "production_STRATEGY_BOOKS_unchanged": True,
    }

    json_path = OUT_DIR / f"C_enhancement_backtest_{tag}_{REPORT_DATE}.json"
    report_path = OUT_DIR / f"C_enhancement_backtest_{tag}_{REPORT_DATE}.md"
    # write_report needs weekly with full book metrics — weekly_out has metrics
    write_report(
        {
            **payload,
            "weekly": {
                **weekly_out,
                "books": {
                    b: {
                        ck: {"metrics": weekly["books"][b][ck]["metrics"]}
                        for ck in weekly["books"][b]
                    }
                    for b in weekly["books"]
                },
            },
        },
        report_path,
    )
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("[shadow_c] wrote", report_path)
    print("[shadow_c] wrote", json_path)
    print("[shadow_c] go_nogo", json.dumps(gn, ensure_ascii=False))
    # print main table
    dk = f"cost_{COST_DEFAULT:.3f}"
    for b in ("C", "B", "C_dyn40", "C_mom12"):
        m = weekly["books"][b][dk]["metrics"]
        print(
            f"  {b}: CAGR={m['cagr']:.4f} Sh={m['sharpe_rf0']:.3f} MDD={m['max_dd']:.4f} "
            f"sat={m['avg_sat_weight']:.3f} xsC={m.get('excess_vs_C_total', 0):.4f}"
        )


if __name__ == "__main__":
    main()
