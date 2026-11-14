# -*- coding: utf-8 -*-
"""
gate_legs.py — 分数闸腿全样本构建 + 阈值标定
============================================
问题根因：
  equity_overlay 仅覆盖 2021-01~2022-12 与 2024-10~2026-07，
  中间（含 2023 全年）ffill 会把 2022 末紧缩/深回撤状态灌满 → 假 BEAR 踏空。

本模块：
  1) 用 yfinance 重建全样本 smooth/raw dd、VIX
  2) overlay 有的日期用官方 rule_regime；缺口用 VIX/dd 代理 denom
  3) NQ：pipeline 窗口内用官方 hard；窗外用 VIX 硬代理
  4) 提供 sat_mult / state 与网格标定

与 daily_allocator 对齐的生产默认（标定后可改常量）：
  score = 1*denom + 1*nq + 1*eq_acute + 0.5*eq_soft
  BEAR≥bear_thr · WARN≥warn_lo
  连续 sat_mult: score∈[full_below, zero_at] 线性
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
MACRO = ROOT / "macro-os" / "docs" / "research"

TIGHT = {"TIGHT_LIQUIDITY", "LIQUIDITY_SQUEEZE"}

# ---- 可标定默认（calibrate_gate 写入 gate_params.json 后可覆盖）----
DEFAULT_PARAMS = {
    "bear_thr": 1.5,
    "warn_lo": 1.0,
    "soft_w": 0.5,
    "eq_acute_smooth": -0.07,
    "eq_acute_raw": -0.10,
    "eq_soft_lo": -0.07,
    "eq_soft_hi": -0.05,
    "denom_w": 1.0,
    "nq_w": 1.0,
    "eq_acute_w": 1.0,
    # 连续 sat_mult
    "sat_full_below": 0.5,
    "sat_zero_at": 1.5,
    # 缺口 denom 代理
    "gap_denom_vix": 28.0,
    "gap_denom_vix_dd": 25.0,
    "gap_denom_dd": -0.12,
    # NQ proxy
    "nq_vix_hard": 30.0,
    "nq_vix_soft": 26.0,
    "nq_dd": -0.10,
}


def _dl_close(ticker: str, start: str, end: str) -> pd.Series:
    raw = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False, threads=False)
    if raw is None or len(raw) == 0:
        return pd.Series(dtype=float)
    if isinstance(raw.columns, pd.MultiIndex):
        px = raw["Close"].iloc[:, 0] if isinstance(raw["Close"], pd.DataFrame) else raw["Close"]
    else:
        px = raw["Close"]
    s = px.dropna().copy()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.astype(float)


def dd20(px: pd.Series) -> pd.Series:
    return px / px.rolling(20, min_periods=5).max() - 1.0


def load_overlay_raw() -> pd.DataFrame:
    a = pd.read_csv(MACRO / "equity_overlay_2022.csv", parse_dates=["date"]).set_index("date")
    b = pd.read_csv(MACRO / "equity_overlay_backtest.csv", parse_dates=["date"]).set_index("date")
    o = pd.concat([a, b])
    o = o[~o.index.duplicated(keep="last")].sort_index()
    o.index = pd.to_datetime(o.index).tz_localize(None)
    return o


def load_nq_official() -> pd.Series:
    p = pd.read_csv(MACRO / "pipeline_backtest_daily_v5.csv", parse_dates=["date"]).set_index("date")
    hard = (p["kernel_authority"].astype(str) == "HARD_VETO") | (p["red_line_triggered"] == True)
    hard = hard.astype(bool)
    hard.index = pd.to_datetime(hard.index).tz_localize(None)
    return hard


def build_leg_frame(
    start: str = "2018-01-01",
    end: str = "2026-09-05",
    params: Optional[dict] = None,
) -> pd.DataFrame:
    """全样本腿。缺口不再 ffill 紧缩状态。"""
    p = {**DEFAULT_PARAMS, **(params or {})}

    soxx = _dl_close("SOXX", start, end)
    qqq = _dl_close("QQQ", start, end)
    try:
        vix = _dl_close("^VIX", start, end)
    except Exception:
        vix = _dl_close("VIX", start, end)

    idx = soxx.index.union(qqq.index).sort_values()
    soxx = soxx.reindex(idx).ffill()
    qqq = qqq.reindex(idx).ffill()
    vix = vix.reindex(idx).ffill()

    raw_soxx = dd20(soxx)
    raw_qqq = dd20(qqq)
    # smooth = min of available（与旧逻辑一致）
    smooth = pd.concat([raw_soxx, raw_qqq], axis=1).min(axis=1)
    raw = raw_soxx  # acute raw 主用 SOXX

    ov = load_overlay_raw()
    # 官方 denom 仅在 overlay 有点的日子
    denom_off = pd.Series(np.nan, index=idx, dtype=float)
    if "rule_regime" in ov.columns:
        d = ov["rule_regime"].astype(str).isin(TIGHT).astype(float)
        denom_off.loc[d.index.intersection(idx)] = d.reindex(idx).dropna()
        # only set where ov has rows
        common = ov.index.intersection(idx)
        denom_off.loc[common] = ov.loc[common, "rule_regime"].astype(str).isin(TIGHT).astype(float)

    # 缺口 denom 代理（不 ffill 旧值）
    gap = denom_off.isna()
    vix_f = vix.fillna(20.0)
    denom_proxy = (
        (vix_f >= p["gap_denom_vix"])
        | ((vix_f >= p["gap_denom_vix_dd"]) & (smooth <= p["gap_denom_dd"]))
    )
    denom = denom_off.copy()
    denom.loc[gap] = denom_proxy.loc[gap].astype(float)
    denom = denom.fillna(0.0).astype(bool)
    denom_source = pd.Series("proxy", index=idx)
    denom_source.loc[~gap] = "overlay"

    # NQ
    nq_off_s = load_nq_official()
    pipe_s, pipe_e = nq_off_s.index.min(), nq_off_s.index.max()
    in_pipe = (idx >= pipe_s) & (idx <= pipe_e)
    nq_official = pd.Series(False, index=idx)
    nq_official.loc[in_pipe] = nq_off_s.reindex(idx[in_pipe]).fillna(False).astype(bool).values
    nq_proxy = ((vix_f >= p["nq_vix_hard"]) | ((vix_f >= p["nq_vix_soft"]) & (smooth <= p["nq_dd"]))) & ~in_pipe
    nq = nq_official | nq_proxy

    eq_acute = (raw.fillna(smooth) <= p["eq_acute_raw"]) | (smooth <= p["eq_acute_smooth"])
    eq_soft = (smooth >= p["eq_soft_lo"]) & (smooth < p["eq_soft_hi"]) & (~eq_acute)

    df = pd.DataFrame(
        {
            "smooth": smooth,
            "raw": raw,
            "vix": vix_f,
            "denom": denom,
            "denom_source": denom_source,
            "nq": nq,
            "nq_official": nq_official,
            "nq_proxy": nq_proxy,
            "eq_acute": eq_acute,
            "eq_soft": eq_soft,
        },
        index=idx,
    )
    return df


def score_from_legs(df: pd.DataFrame, params: Optional[dict] = None) -> pd.Series:
    p = {**DEFAULT_PARAMS, **(params or {})}
    return (
        p["denom_w"] * df["denom"].astype(float)
        + p["nq_w"] * df["nq"].astype(float)
        + p["eq_acute_w"] * df["eq_acute"].astype(float)
        + p["soft_w"] * df["eq_soft"].astype(float)
    )


def state_from_score(score: pd.Series, params: Optional[dict] = None, shift: bool = True) -> pd.Series:
    p = {**DEFAULT_PARAMS, **(params or {})}
    st = pd.Series(0, index=score.index, dtype=int)
    st = st.mask(score >= p["warn_lo"], 1)
    st = st.mask(score >= p["bear_thr"], 2)
    if shift:
        st = st.shift(1).fillna(0).astype(int)
    return st


def sat_mult_series(score: pd.Series, params: Optional[dict] = None, continuous: bool = True) -> pd.Series:
    p = {**DEFAULT_PARAMS, **(params or {})}
    s = score.astype(float)
    if not continuous:
        st = state_from_score(s, p, shift=False)
        return st.map({0: 1.0, 1: 0.5, 2: 0.0}).astype(float)
    lo, hi = p["sat_full_below"], p["sat_zero_at"]
    m = (hi - s) / (hi - lo)
    m = m.clip(0.0, 1.0)
    m = m.where(s > lo, 1.0)
    m = m.where(s < hi, 0.0)
    # exact bounds
    m = pd.Series(
        np.where(s <= lo, 1.0, np.where(s >= hi, 0.0, (hi - s) / (hi - lo))),
        index=s.index,
        dtype=float,
    )
    return m


def diagnose_year(df: pd.DataFrame, score: pd.Series, state: pd.Series, year: int) -> dict:
    sl = slice(f"{year}-01-01", f"{year}-12-31")
    d = df.loc[sl]
    if len(d) == 0:
        return {"year": year, "n": 0}
    sc = score.reindex(d.index).ffill()
    st = state.reindex(d.index).fillna(0)
    return {
        "year": year,
        "n": int(len(d)),
        "denom_pct": float(d["denom"].mean()),
        "nq_pct": float(d["nq"].mean()),
        "acute_pct": float(d["eq_acute"].mean()),
        "soft_pct": float(d["eq_soft"].mean()),
        "overlay_denom_days": int((d["denom_source"] == "overlay").sum()),
        "proxy_denom_days": int((d["denom_source"] == "proxy").sum()),
        "score_mean": float(sc.mean()),
        "off_pct": float((st == 0).mean()),
        "warn_pct": float((st == 1).mean()),
        "bear_pct": float((st == 2).mean()),
        "sat_mult_mean": float(sat_mult_series(sc).mean()),
    }


@dataclass
class CalibResult:
    params: dict
    metrics: dict


def _bh_nav(px: pd.Series, start, end=None) -> pd.Series:
    s = px.loc[start:end].dropna()
    nav = (1 + s.pct_change().fillna(0)).cumprod()
    return nav / nav.iloc[0]


def _sim_book_c(r_core, r_sat, r_bil, sat_m, cost=0.001):
    """日频：core 70% + sat 30%*sat_m + bil rest。sat_m 已 shift 决策。"""
    c = 0.70
    s = 0.30 * sat_m
    b = (1.0 - c - s).clip(lower=0)
    port = c * r_core + s * r_sat + b * r_bil
    # 换手近似
    w = pd.concat([pd.Series(c, index=sat_m.index), s, b], axis=1)
    turn = 0.5 * w.diff().abs().sum(axis=1).fillna(0.0)
    port = port - cost * turn
    nav = (1 + port).cumprod()
    return nav / nav.iloc[0]


def _metrics(nav, bench):
    ret = nav.pct_change().fillna(0)
    total = float(nav.iloc[-1] / nav.iloc[0] - 1)
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    cagr = float((max(nav.iloc[-1], 1e-12) / nav.iloc[0]) ** (1 / years) - 1)
    vol = float(ret.std() * np.sqrt(252))
    sh = float(ret.mean() * 252 / vol) if vol > 1e-12 else 0.0
    mdd = float((nav / nav.cummax() - 1).min())
    b = bench.reindex(nav.index).ffill()
    b = b / b.iloc[0]
    return {
        "total": total,
        "cagr": cagr,
        "sharpe": sh,
        "mdd": mdd,
        "vs_bench_pp": (total - float(b.iloc[-1] - 1)) * 100,
    }


def calibrate_gate(
    r_s3: pd.Series,
    r_qqq: pd.Series,
    r_bil: pd.Series,
    start: str = "2018-05-16",
    end: Optional[str] = None,
) -> dict:
    """网格标定：在修腿后最大化「2022 保护 + 2023 不踏空 + FULL 不崩」。

    目标（加权）：
      FULL sharpe
      + 惩罚 2022 MDD 差于基准过多
      + 奖励 2023 vs QQQ（减少踏空）
      + 惩罚 BEAR% 过高
    """
    df0 = build_leg_frame("2018-01-01", end or "2026-09-05")
    idx = df0.index.intersection(r_s3.index).intersection(r_qqq.index)
    if end:
        idx = idx[idx <= end]
    idx = idx[idx >= start]

    # S3 作为卫星收益代理
    r_s3 = r_s3.reindex(idx).fillna(0.0)
    r_qqq = r_qqq.reindex(idx).fillna(0.0)
    # Prefer time interpolate for BIL gaps; fillna(0) would silently price cash as 0%
    r_bil = r_bil.reindex(idx)
    try:
        r_bil = r_bil.interpolate(method="time")
    except Exception:
        r_bil = r_bil.ffill()
    n_gap = int(r_bil.isna().sum())
    if n_gap:
        print(f"[gate_legs] BIL still {n_gap} NaN after interpolate; remaining filled 0 (warn)")
    r_bil = r_bil.fillna(0.0)
    df = df0.reindex(idx).ffill()

    grid_bear = [1.5, 2.0, 2.5]
    grid_acute_s = [-0.07, -0.08, -0.10]
    grid_denom_w = [0.5, 1.0]
    grid_soft_w = [0.25, 0.5]
    # gap denom 松紧
    grid_gap_vix = [26.0, 28.0, 30.0]

    best = None
    rows = []
    for bear_thr in grid_bear:
        for acute_s in grid_acute_s:
            for denom_w in grid_denom_w:
                for soft_w in grid_soft_w:
                    for gap_vix in grid_gap_vix:
                        params = {
                            **DEFAULT_PARAMS,
                            "bear_thr": bear_thr,
                            "eq_acute_smooth": acute_s,
                            "eq_soft_lo": acute_s,  # soft 上沿贴 acute
                            "eq_soft_hi": -0.05,
                            "denom_w": denom_w,
                            "soft_w": soft_w,
                            "gap_denom_vix": gap_vix,
                            "sat_zero_at": bear_thr,  # 连续清仓对齐 BEAR 阈
                        }
                        # rebuild acute/soft/denom gap with params
                        d = df.copy()
                        d["eq_acute"] = (d["raw"].fillna(d["smooth"]) <= params["eq_acute_raw"]) | (
                            d["smooth"] <= params["eq_acute_smooth"]
                        )
                        d["eq_soft"] = (
                            (d["smooth"] >= params["eq_soft_lo"])
                            & (d["smooth"] < params["eq_soft_hi"])
                            & (~d["eq_acute"])
                        )
                        # re-proxy denom on gap days only
                        gap = d["denom_source"] == "proxy"
                        d.loc[gap, "denom"] = (
                            (d.loc[gap, "vix"] >= params["gap_denom_vix"])
                            | (
                                (d.loc[gap, "vix"] >= params["gap_denom_vix_dd"])
                                & (d.loc[gap, "smooth"] <= params["gap_denom_dd"])
                            )
                        )
                        sc = score_from_legs(d, params)
                        st = state_from_score(sc, params, shift=True)
                        sm = sat_mult_series(sc.shift(1).fillna(0), params, continuous=True)
                        nav = _sim_book_c(r_qqq, r_s3, r_bil, sm)
                        bnav = (1 + r_qqq).cumprod()
                        bnav = bnav / bnav.iloc[0]

                        def win(a, b):
                            n = nav.loc[a:b]
                            if len(n) < 5:
                                return None
                            n = n / n.iloc[0]
                            bb = bnav.loc[n.index]
                            bb = bb / bb.iloc[0]
                            return _metrics(n, bb)

                        full = win(start, None)
                        y22 = win("2022-01-03", "2022-12-30")
                        y23 = win("2023-01-03", "2023-12-29")
                        y3 = win("2023-09-01", None)
                        if not full or not y22 or not y23:
                            continue
                        bear_pct = float((st == 2).mean())
                        bear_23 = float((st.loc["2023-01-01":"2023-12-31"] == 2).mean()) if len(st.loc["2023-01-01":"2023-12-31"]) else 1.0
                        # objective
                        obj = (
                            full["sharpe"] * 20.0
                            + full["vs_bench_pp"] * 0.15
                            + y23["vs_bench_pp"] * 0.35  # 重点修踏空
                            + min(y22["vs_bench_pp"], 25) * 0.25  # 2022 保护（vsQQQ 正越好）
                            - max(0.0, (-0.35 - y22["mdd"]) * 100) * 0.5  # MDD 差于 -35% 惩罚
                            - max(0.0, bear_pct - 0.25) * 40.0
                            - max(0.0, bear_23 - 0.15) * 50.0
                        )
                        rec = {
                            "obj": obj,
                            "params": params,
                            "full": full,
                            "y22": y22,
                            "y23": y23,
                            "y3": y3,
                            "bear_pct": bear_pct,
                            "bear_23": bear_23,
                        }
                        rows.append(rec)
                        if best is None or obj > best["obj"]:
                            best = rec

    rows.sort(key=lambda x: -x["obj"])
    return {"best": best, "top10": rows[:10], "n_eval": len(rows)}


def load_params(path: Optional[Path] = None) -> dict:
    path = path or (ROOT / "gate_params.json")
    if path.exists():
        import json
        d = json.loads(path.read_text(encoding="utf-8"))
        return {**DEFAULT_PARAMS, **d.get("params", d)}
    return dict(DEFAULT_PARAMS)


def save_params(params: dict, path: Optional[Path] = None, meta: Optional[dict] = None):
    import json
    path = path or (ROOT / "gate_params.json")
    path.write_text(
        json.dumps({"params": params, "meta": meta or {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
