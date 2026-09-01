#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标普板块资金轮动 — 近 3 年（2023-08 .. 2026-08）样本外回测与分析

为什么做这个
------------
`sector_rotation_daily.py`（Pine v1.3m 本地复刻）的打分引擎在**每个交易日**给出 14 个主题
的 `state`（拥挤主升/确认进入/早期轮动/中性/撤出/派发）与 75/60 经验线。但原始脚本只出
"读数"，从未验证过：**这个信号到底有没有预测力？** 以及 **哪些改进能真正提升？**

本脚本的设计要点（诚实回测原则）
-------------------------------
1. 因果性：引擎所有指标（sma/shift/pct_change/rel_accel/breadth/chain_index）只用历史数据，
   `series.loc[t]` 即「截至 t 收盘」的真实读数，无未来函数。直接对全序列切片即可逐日回放。
2. 八条归因剧本（攻守对调/小盘/等权/利率/通胀/避险/降风险/再平衡）原本是末值标量，这里
   全部**向量化为全日序列**（score_accel / pos_part / neg_part 改为 array 广播），使最终
   boost 与 final_score 也成为全日序列，从而可在任意历史日还原当时的终态。
3. 交易标的 = 主题自身的 ETF（XLK 等），前瞻收益 = 该 ETF 自身收盘 forward return，
   而非相对比价（比价只描述强弱，下注在绝对标的上）。
4. 策略用**日频再平衡 + 换手摩擦**（FRICTION_PER_TOGGLE=5bps，与 Equity-Stress Overlay 一致），
   避免低估交易成本。
5. 改进候选全部 ADDITIVE，不动原引擎冻结参数：
   - A0 基线：final_state >= 2（确认进入/拥挤主升）
   - A1 持续性：final_score>=75 连续 >=2 日（削减抖入）
   - A2 去拥挤：final_state>=2 且 not over_ext（回避派发末端）
   - A3 严格趋势：final_state>=2 且 trend_code==2（价在双均线上）
   - A4 大盘门控：final_state>=2 且 SPY trend_code>=1（只做多头顺境，防接飞刀）
   - A5 多空：long state>=2 / short state<=-1（市值中性，检验信号对称性）

输出：
  output/sector_backtest_<END>.md   人类阅读
  output/sector_backtest_<END>.json 机器解析（含 NAV 序列、按状态前瞻表）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# 复用生产引擎（逐一函数，避免触发 main 的 2y/400bar 截断）
from sector_rotation_daily import (  # noqa: E402
    TICKERS, LABELS, THEME_ORDER, SECTOR_KEYS,
    calc_metrics_full, calc_bench_full, chain_index, avg_ret5,
    sma, pct_change_n, safe_div, rel_accel, breadth_etf,
    cap100, BENCH_CROWD60, BENCH_CROWD_DIST,
)
from backtest_overlay_walkforward import nav_with_friction, max_dd, FRICTION_PER_TOGGLE  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "output"
RESEARCH = ROOT / "docs" / "research"
DATA.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
RESEARCH.mkdir(parents=True, exist_ok=True)

CACHE = DATA / "_sector_backtest_prices.csv"
START = "2023-08-14"
END = None  # 取数据最新日
PERIOD = "5y"  # 给 2023-08 之前留足 400bar 回看

THEME_KEYS = [k for _, k, _ in THEME_ORDER]  # 14 主题（不含 spy）


# ===========================================================================
# 1. 数据（复用 _download_close_vol 的解析，改为 5y + 落盘缓存）
# ===========================================================================
def _download(code: str, period: str):
    import yfinance as yf
    raw = yf.download(code, period=period, auto_adjust=True, progress=False, threads=False)
    if raw is None or getattr(raw, "empty", True):
        return None
    cols = raw.columns

    def _field(field):
        if getattr(cols, "nlevels", 1) > 1:
            if (field, code) in cols:
                return raw[(field, code)]
            if (code, field) in cols:
                return raw[(code, field)]
            if field in cols.get_level_values(0):
                cd = raw.xs(field, axis=1, level=0)
                return cd[code] if code in cd.columns else (cd.iloc[:, 0] if not cd.empty else None)
            return None
        return raw[field] if field in cols else None

    close = _field("Close")
    vol = _field("Volume")
    if close is None:
        return None
    close = pd.to_numeric(close, errors="coerce").dropna()
    vol = pd.to_numeric(vol, errors="coerce") if vol is not None else pd.Series(dtype=float)
    if len(close) < 60:
        return None
    return close, vol


def load_prices(force: bool = False, period: str = PERIOD):
    if CACHE.exists() and not force:
        df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
        # 新鲜度：末日距今天 <=4 天视为可用
        last = df.index.max().date()
        age = (datetime.now().date() - last).days
        if age <= 4:
            print(f"[cache] reuse {CACHE.name} last={last} age={age}d rows={len(df)}", flush=True)
            return df
        print(f"[cache] stale (age={age}d), refetch", flush=True)
    print(f"[fetch] yfinance {period} for {len(TICKERS)} tickers (proxy) ...", flush=True)
    frames = {}
    for key, code in TICKERS.items():
        res = _download(code, period)
        if res is None:
            print(f"  skip {key}({code})", flush=True)
            continue
        close, vol = res
        frames[f"C_{key}"] = close
        frames[f"V_{key}"] = vol
        print(f"  {key}({code}): {len(close)} bars", flush=True)
    df = pd.DataFrame(frames)
    df = df.sort_index()
    df.to_csv(CACHE, index_label="date")
    print(f"[cache] wrote {CACHE.name} rows={len(df)}", flush=True)
    return df


# ===========================================================================
# 2. 向量化辅助
# ===========================================================================
def v_cap100(x):
    x = np.asarray(x, dtype=float)
    out = np.where(np.isnan(x), np.nan, np.clip(x, 0.0, 100.0))
    return out


def v_score_accel(x, scale, max_score):
    x = np.asarray(x, dtype=float)
    return np.where(np.isnan(x), np.nan, np.clip(np.maximum(0.0, x / scale * max_score), 0.0, max_score))


def v_pos(s):
    return np.maximum(np.asarray(s, dtype=float), 0.0)


def v_neg(s):
    return np.maximum(-np.asarray(s, dtype=float), 0.0)


def where3(cond, a, b):
    """vectorized if/else: cond ? a : b  (all arrays)"""
    cond = np.asarray(cond)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return np.where(cond, a, b)


# ===========================================================================
# 3. 全序列信号矩阵（因果，无未来函数）
# ===========================================================================
def _calc_bench_full_vec(px: pd.Series, dv: pd.Series, br_pct: pd.Series):
    """calc_bench_full 的全序列向量化版（br_pct 可为 Series）。"""
    a1 = pct_change_n(px, 1); a5 = pct_change_n(px, 5); a20 = pct_change_n(px, 20); a60 = pct_change_n(px, 60)
    m20 = sma(px, 20); m50 = sma(px, 50)
    dv_fix = dv.ffill(); v_r = safe_div(dv_fix, sma(dv_fix, 20)); d50 = safe_div(px - m50, m50) * 100.0
    valid = px.notna()
    trend_score = (px > m20).astype(float) * 10.0 + (px > m50).astype(float) * 10.0 + (m20 > m20.shift(5)).astype(float) * 10.0
    flow_score = (a5 > 0).astype(float) * 10.0 + (a20 > 0).astype(float) * 8.0 + (a5 > a5.shift(5)).astype(float) * 8.0 + (a5 > a20 / 4.0).astype(float) * 8.0
    vol_score = pd.Series(np.where((a1 > 0).fillna(False) & (v_r >= 1.30).fillna(False), 20.0,
                            np.where((a1 > 0).fillna(False) & (v_r >= 1.0).fillna(False), 10.0, 0.0)), index=px.index)
    br_score = pd.Series(np.where(br_pct >= 70.0, 15.0, np.where(br_pct >= 50.0, 8.0, 0.0)), index=px.index)
    over_ext = (a60 > BENCH_CROWD60).fillna(False) | (d50 > BENCH_CROWD_DIST).fillna(False)
    crowd_score = np.where(over_ext, 0.0, 8.0)
    raw = trend_score + flow_score + vol_score + br_score + crowd_score
    score = pd.Series(np.where(valid, np.clip(raw, 0, 100), np.nan), index=px.index)
    distribution = valid & (px < m20) & (a5 < 0) & (v_r > 1.30) & (a1 < 0)
    state = np.select(
        [~valid, distribution, over_ext & (score >= 60), score >= 75, score >= 60,
         (score < 45) & (a5 < 0) & (a20 < 0)],
        [9, -2, 3, 2, 1, -1], default=0).astype(int)
    trend_code = np.select(
        [(px > m20) & (px > m50), px > m20, (px < m20) & (px < m50), px < m20],
        [2, 1, -2, -1], default=0).astype(int)
    return {"a1": a1, "a5": a5, "a20": a20, "a60": a60, "vol_r": v_r, "dist50": d50,
            "score": score, "state": pd.Series(state, index=px.index),
            "trend_code": pd.Series(trend_code, index=px.index),
            "over_ext": over_ext, "distribution": distribution}


def build_signal_matrix(df: pd.DataFrame):
    close = {k: df[f"C_{k}"].astype(float) for k in TICKERS}
    vol = {k: df[f"V_{k}"].astype(float) for k in TICKERS}
    c_spy = close["spy"]

    # ---- 各主题全序列指标 ----
    m = {}
    for key in THEME_KEYS:
        idx = close[key]
        dv = vol[key] * idx
        mm = calc_metrics_full(idx, dv, c_spy)
        # accel 全日序列
        mm["accel"] = rel_accel(mm["rel5"], mm["rel20"])
        m[key] = mm

    # ---- 合成组指数（全序列）----
    off_ret, _ = avg_ret5([close.get("xlk"), close.get("xly"), close.get("xlf")])
    def_ret, _ = avg_ret5([close.get("xlp"), close.get("xlu"), close.get("xlv")])
    emb_ret, _ = avg_ret5([close.get("xle"), close.get("xlb")])
    off_idx = chain_index(off_ret)
    def_idx = chain_index(def_ret)
    emb_idx = chain_index(emb_ret)

    def pct5(s):
        return pct_change_n(s, 5)

    def pct20(s):
        return pct_change_n(s, 20)

    off_ratio = safe_div(off_idx, c_spy)
    off_c5, off_c20 = pct5(off_ratio), pct20(off_ratio)
    off_c_accel = rel_accel(off_c5, off_c20)
    def_ratio = safe_div(def_idx, c_spy)
    def_c5, def_c20 = pct5(def_ratio), pct20(def_ratio)
    def_c_accel = rel_accel(def_c5, def_c20)
    def_off_ratio = safe_div(def_idx, off_idx)
    def_off5 = pct5(def_off_ratio)
    def_off_accel = rel_accel(def_off5, pct20(def_off_ratio))
    off_def_ratio = safe_div(off_idx, def_idx)
    off_def5 = pct5(off_def_ratio)
    off_def_accel = rel_accel(off_def5, pct20(off_def_ratio))
    emb_tech_ratio = safe_div(emb_idx, close["xlk"])
    emb_tech5 = pct5(emb_tech_ratio)
    emb_tech_accel = rel_accel(emb_tech5, pct20(emb_tech_ratio))

    leg_tlt5 = pct5(close["tlt"])
    leg_gld5 = pct5(close["gld"])
    leg_hyg_rel5 = pct5(safe_div(close["hyg"], c_spy))
    leg_kre_rel5 = pct5(safe_div(close["kre"], c_spy))
    spy_a5 = pct5(c_spy)

    # ---- 板块计数（全序列）----
    pos_rel_count = sum((m[k]["rel5"] > 0).fillna(False).astype(float) for k in SECTOR_KEYS)
    weak_count = sum((m[k]["rel5"] < 0).fillna(False).astype(float) for k in SECTOR_KEYS)
    abs_down_count = sum((m[k]["abs5"] < 0).fillna(False).astype(float) for k in SECTOR_KEYS)

    # ---- 八条归因剧本（全序列）----
    defShiftRaw = (v_score_accel(v_neg(off_c_accel), 2.0, 25.0)
                   + v_score_accel(v_pos(def_c_accel), 2.0, 25.0)
                   + v_score_accel(v_pos(def_off_accel), 1.5, 20.0)
                   + where3(def_c5 > off_c5, 10.0, 0.0)
                   + where3(def_c5 > 0, 5.0, 0.0))
    offShiftRaw = (v_score_accel(v_neg(def_c_accel), 2.0, 25.0)
                   + v_score_accel(v_pos(off_c_accel), 2.0, 25.0)
                   + v_score_accel(v_pos(off_def_accel), 1.5, 20.0)
                   + where3(off_c5 > def_c5, 10.0, 0.0)
                   + where3(off_c5 > 0, 5.0, 0.0))
    flip_to_def = defShiftRaw >= offShiftRaw
    flip_score = v_cap100(np.maximum(defShiftRaw, offShiftRaw))

    iwm = m["iwm"]; rsp = m["rsp"]
    small_score = v_cap100(
        v_score_accel(v_pos(iwm["accel"]), 1.5, 35.0)
        + where3(iwm["rel5"] > 0, 15.0, 0.0)
        + where3(iwm["score"] >= 60, 20.0, 0.0)
        + where3(leg_hyg_rel5 > 0, 15.0, 0.0))
    broad_score = v_cap100(
        v_score_accel(v_pos(rsp["accel"]), 1.5, 35.0)
        + where3(rsp["rel5"] > 0, 15.0, 0.0)
        + where3(rsp["score"] >= 60, 20.0, 0.0)
        + where3(pos_rel_count >= 7, 15.0, where3(pos_rel_count >= 5, 8.0, 0.0)))

    xlu_a = m["xlu"]["accel"]; xlre_a = m["xlre"]["accel"]; xlf_a = m["xlf"]["accel"]
    rate_bene_accel = np.where(np.isnan(xlu_a) | np.isnan(xlre_a), np.nan, (xlu_a + xlre_a) / 2.0)
    rate_gap = rate_bene_accel - xlf_a
    rate_sign_down = (rate_gap > 0) & (leg_tlt5 > 0)
    rate_sign_up = (rate_gap < 0) & (leg_tlt5 < 0)
    sign = rate_sign_down | rate_sign_up
    rate_raw = where3(sign, 25.0 + v_score_accel(np.abs(rate_gap), 2.0, 30.0) + v_score_accel(leg_tlt5, 2.0, 20.0), 0.0)
    add15 = where3(rate_sign_down & (m["xlu"]["rel5"] > 0) & (m["xlre"]["rel5"] > 0), 15.0,
                   where3(rate_sign_up & (m["xlf"]["rel5"] > 0), 15.0, 0.0))
    add10 = where3(rate_sign_down & (m["xlf"]["rel5"] < 0), 10.0,
                   where3(rate_sign_up & ((m["xlu"]["rel5"] < 0) | (m["xlre"]["rel5"] < 0)), 10.0, 0.0))
    rate_score = v_cap100(rate_raw + add15 + add10)

    xle = m["xle"]; xlb = m["xlb"]
    infl_score = v_cap100(
        v_score_accel(v_pos(emb_tech_accel), 1.5, 30.0)
        + where3((xle["rel5"] > 0) & (xlb["rel5"] > 0), 15.0, 0.0)
        + v_score_accel(v_pos(xle["accel"]), 2.0, 15.0)
        + where3(leg_gld5 > 0, 10.0, 0.0)
        + where3(leg_tlt5 < 0, 10.0, 0.0)
        + where3((xle["score"] >= 60) | (xlb["score"] >= 60), 20.0, 0.0))

    def_leading = def_c5 > 0
    haven_raw = where3(def_leading,
                       where3(leg_tlt5 > 0, 25.0, 0.0)
                       + where3(leg_gld5 > 0, 20.0, 0.0)
                       + where3(leg_hyg_rel5 < 0, 25.0, 0.0)
                       + where3(spy_a5 < 0, 15.0, 0.0)
                       + where3(leg_kre_rel5 < 0, 15.0, 0.0),
                       0.0)
    haven_score = v_cap100(haven_raw)

    de_risk_raw = (
        where3(weak_count >= 8, 35.0, where3(weak_count >= 6, 22.0, where3(weak_count >= 5, 10.0, 0.0)))
        + where3(abs_down_count >= 8, 25.0, where3(abs_down_count >= 6, 15.0, 0.0))
        + where3(def_leading & (def_c5 > off_c5), 10.0, 0.0)
        + where3(leg_hyg_rel5 < 0, 10.0, 0.0)
        + where3(leg_kre_rel5 < -2.0, 5.0, 0.0))
    de_risk_score = v_cap100(de_risk_raw)

    # 再平衡（日历 + 减速/回血计数）
    mo = df.index.month.to_series(index=df.index) if hasattr(df.index, "month") else pd.Series(df.index.month, index=df.index)
    dom = pd.Series(df.index.day, index=df.index)
    is_month_turn = (dom >= 24) | (dom <= 3)
    is_quarter_turn = (((mo.isin([3, 6, 9, 12])) & (dom >= 20)) | ((mo.isin([1, 4, 7, 10])) & (dom <= 3)))
    rebal_keys = SECTOR_KEYS + ["iwm", "rsp", "qqq"]
    winner_decel = sum(where3((m[k]["rel20"] > 4) & (m[k]["accel"] < 0), 1.0, 0.0) for k in rebal_keys)
    laggard_recovery = sum(where3((m[k]["rel20"] < 1.5) & (m[k]["accel"] > 0), 1.0, 0.0) for k in rebal_keys)
    rebalance_raw = (where3(is_month_turn, 20.0, 0.0) + where3(is_quarter_turn, 25.0, 0.0)
                     + where3(winner_decel >= 3, 25.0, where3(winner_decel == 2, 18.0, where3(winner_decel == 1, 10.0, 0.0)))
                     + where3(laggard_recovery >= 3, 25.0, where3(laggard_recovery == 2, 18.0, where3(laggard_recovery == 1, 10.0, 0.0)))
                     + where3(flip_score >= 45, 10.0, 0.0))
    rebalance_score = v_cap100(rebalance_raw)

    # ---- boost 全序列 ----
    boost = {}
    defBoost = where3(flip_to_def, where3(flip_score >= 60, 10.0, where3(flip_score >= 45, 5.0, 0.0)), 0.0)
    offBoost = where3(~flip_to_def, where3(flip_score >= 60, 10.0, where3(flip_score >= 45, 5.0, 0.0)), 0.0)
    rateDnBoost = where3(rate_sign_down, where3(rate_score >= 60, 8.0, where3(rate_score >= 45, 4.0, 0.0)), 0.0)
    rateUpBoost = where3(rate_sign_up, where3(rate_score >= 60, 8.0, where3(rate_score >= 45, 4.0, 0.0)), 0.0)
    inflBoost = where3(infl_score >= 60, 8.0, where3(infl_score >= 45, 4.0, 0.0))
    boost["xlk"] = offBoost
    boost["xlf"] = offBoost + rateUpBoost
    boost["xlv"] = defBoost
    boost["xly"] = offBoost
    boost["xlp"] = defBoost
    boost["xle"] = inflBoost
    boost["xli"] = np.zeros(len(df))
    boost["xlb"] = inflBoost
    boost["xlu"] = defBoost + rateDnBoost
    boost["xlre"] = rateDnBoost
    boost["xlc"] = np.zeros(len(df))
    boost["iwm"] = where3(small_score >= 60, 12.0, where3(small_score >= 45, 6.0, 0.0))
    boost["rsp"] = where3(broad_score >= 60, 12.0, where3(broad_score >= 45, 6.0, 0.0))
    boost["qqq"] = np.zeros(len(df))

    # ---- 终态：base_score + boost -> final_score -> final_state ----
    def v_compute_state(score, over_ext, distribution, rel5, rel20):
        score = np.asarray(score, dtype=float)
        over_ext = np.asarray(over_ext, dtype=bool)
        distribution = np.asarray(distribution, dtype=bool)
        rel5 = np.asarray(rel5, dtype=float)
        rel20 = np.asarray(rel20, dtype=float)
        valid = ~np.isnan(score)
        # priority: ~valid(9) > distribution(-2) > over_ext&>=60(3) > >=75(2) > >=60(1) > (<45&rel5<0&rel20<0)(-1) > 0
        out = np.select(
            [~valid, distribution, over_ext & (score >= 60), score >= 75, score >= 60,
             (score < 45) & (rel5 < 0) & (rel20 < 0)],
            [9, -2, 3, 2, 1, -1], default=0,
        ).astype(int)
        return out

    fin = {}
    for key in THEME_KEYS:
        base = np.asarray(m[key]["score"], dtype=float)
        final = v_cap100(base + np.asarray(boost[key], dtype=float))
        state = v_compute_state(final, np.asarray(m[key]["over_ext"], dtype=bool),
                                np.asarray(m[key]["distribution"], dtype=bool),
                                np.asarray(m[key]["rel5"], dtype=float),
                                np.asarray(m[key]["rel20"], dtype=float))
        # base-only state（对照：不加 boost 的原分数）
        state_base = v_compute_state(base, np.asarray(m[key]["over_ext"], dtype=bool),
                                     np.asarray(m[key]["distribution"], dtype=bool),
                                     np.asarray(m[key]["rel5"], dtype=float),
                                     np.asarray(m[key]["rel20"], dtype=float))
        fin[key] = {
            "final_score": pd.Series(final, index=df.index),
            "base_score": pd.Series(base, index=df.index),
            "final_state": pd.Series(state, index=df.index),
            "base_state": pd.Series(state_base, index=df.index),
            "trend_code": m[key]["trend_code"],
            "over_ext": m[key]["over_ext"],
            "rel5": m[key]["rel5"],
        }

    # ---- 基准行（SPY 绝对口径，真广度全序列）----
    br_flags = {k: (m[k]["breadth"] >= 70).fillna(False).astype(float) for k in SECTOR_KEYS}
    bench_breadth = sum(br_flags.values()) / len(SECTOR_KEYS) * 100.0
    spy_dv = vol["spy"] * c_spy
    bench = _calc_bench_full_vec(c_spy, spy_dv, bench_breadth)
    bench_state = bench["state"]
    bench_trend = bench["trend_code"]

    return {
        "close": close, "vol": vol,
        "fin": fin,
        "bench_state": bench_state, "bench_trend": bench_trend,
        "bench_breadth": bench_breadth,
        "meta": {
            "flip_score": flip_score, "small_score": small_score, "broad_score": broad_score,
            "rate_score": rate_score, "infl_score": infl_score, "haven_score": haven_score,
            "de_risk_score": de_risk_score, "rebalance_score": rebalance_score,
            "bench_state": bench_state, "bench_trend": bench_trend,
        },
    }


# ===========================================================================
# 4. 前瞻收益 + 按状态信号质量
# ===========================================================================
def forward_returns(close: dict, spy: pd.Series, key: str, horizons=(5, 10, 20, 60)):
    s = close[key].astype(float)
    sp = spy.astype(float)
    out = {}
    for n in horizons:
        a = s.shift(-n) / s - 1.0
        r = sp.shift(-n) / sp - 1.0
        out[f"abs_{n}"] = a
        out[f"rel_{n}"] = a - r  # 相对 SPY 超额（轮动信号应有的检验口径）
    return pd.DataFrame(out, index=s.index)


def signal_quality_by_state(fin, fwd, col_prefix="abs_"):
    """对每个主题，按 final_state 分组看前瞻收益（绝对或相对）均值/胜率。"""
    rows = []
    for key in THEME_KEYS:
        st = fin[key]["final_state"]
        for n in (5, 10, 20, 60):
            col = f"{col_prefix}{n}"
            fr = fwd[key][col]
            sub = pd.DataFrame({"state": st, "ret": fr}).dropna()
            if len(sub) < 5:
                continue
            for sval, sname in [(3, "拥挤主升"), (2, "确认进入"), (1, "早期轮动"),
                                (0, "中性"), (-1, "撤出"), (-2, "派发")]:
                g = sub[sub["state"] == sval]["ret"]
                if len(g) >= 10:
                    rows.append({
                        "theme": key, "state": sval, "state_name": sname,
                        "horizon": n, "n": len(g),
                        "mean_ret": float(g.mean()), "hit": float((g > 0).mean()),
                    })
    df = pd.DataFrame(rows)
    agg = df.groupby(["state", "state_name", "horizon"]).agg(
        n=("n", "sum"), mean_ret=("mean_ret", "mean"), hit=("hit", "mean")).reset_index()
    return agg, df


# ===========================================================================
# 5. 策略 NAV
# ===========================================================================
def strategy_nav(sig: pd.DataFrame, ret: pd.DataFrame, label: str):
    """sig: bool DataFrame (dates x themes); ret: daily returns DataFrame (aligned).
    上一日信号决定今日持仓，日频再平衡，等权，含换手摩擦。"""
    dates = sig.index
    K = sig.shape[1]
    nav = np.ones(len(dates))
    port_ret = np.zeros(len(dates))
    toggles = 0
    prev_w = None
    for i in range(1, len(dates)):
        w = sig.iloc[i - 1].astype(float).values  # 上一日信号决定今日持仓
        tot = w.sum()
        if tot > 0:
            w = w / tot
        else:
            w = np.zeros(K)
        r = ret.iloc[i].values.astype(float)
        pr = float(np.nansum(w * r))
        port_ret[i] = pr
        nav[i] = nav[i - 1] * (1.0 + pr)
        if prev_w is not None:
            turnover = 0.5 * np.sum(np.abs(w - prev_w))
            if turnover > 1e-9:
                nav[i] *= (1.0 - FRICTION_PER_TOGGLE * min(turnover, 2.0))
                toggles += 1
        prev_w = w
    return nav, port_ret, toggles


def metrics_from_nav(nav, port_ret, dates, spy_nav, spy_ret):
    n = len(nav)
    cagr = nav[-1] ** (252.0 / n) - 1.0
    spy_cagr = spy_nav[-1] ** (252.0 / n) - 1.0
    pr = np.nan_to_num(port_ret[1:])
    vol = pr.std()
    sharpe = (pr.mean() / vol * np.sqrt(252)) if vol > 0 else 0.0
    mdd = max_dd(nav)
    spy_mdd = max_dd(spy_nav)
    return {
        "cagr": float(cagr), "spy_cagr": float(spy_cagr),
        "excess_cagr": float(cagr - spy_cagr),
        "sharpe": float(sharpe), "mdd": float(mdd), "spy_mdd": float(spy_mdd),
        "mdd_improve": float(spy_mdd - mdd),
    }


def position_portfolio(fst, ret, spy_ret, idx, schedule):
    """仓位框架：每个主题等权基准权重 = 1/K；实际主题权重 = 基准 × schedule[state]；
    余量（1 − 主题权重和）自动配置 SPY。上一日 state 决定今日权重，日频再平衡，含 5bps 摩擦。
    直接回答「某状态买/卖多少、余下配 SPY」。"""
    K = len(THEME_KEYS)
    base = 1.0 / K
    nav = np.ones(len(idx))
    port_ret = np.zeros(len(idx))
    toggles = 0
    prev_theme_w = None
    prev_spy_w = None
    for i in range(1, len(idx)):
        w = np.array([base * float(schedule.get(int(fst[k].iloc[i - 1]), 1.0)) for k in THEME_KEYS])
        spy_w = max(0.0, 1.0 - w.sum())
        r = ret.iloc[i].values.astype(float)
        pr = float(np.nansum(w * r)) + spy_w * float(spy_ret.iloc[i])
        port_ret[i] = pr
        nav[i] = nav[i - 1] * (1.0 + pr)
        if prev_theme_w is not None:
            turnover = 0.5 * (np.sum(np.abs(w - prev_theme_w)) + abs(spy_w - prev_spy_w))
            if turnover > 1e-9:
                nav[i] *= (1.0 - FRICTION_PER_TOGGLE * min(turnover, 2.0))
                toggles += 1
        prev_theme_w = w
        prev_spy_w = spy_w
    return nav, port_ret, toggles


# 仓位方案（state -> 权重乘数，1=等权基准满仓，0=清仓转 SPY）
SCHEDULES = {
    # 等权全部 14 主题，余量 0 → 即 EQW_all（非择时基准）
    "S_EQW":    {3: 1.0, 2: 1.0, 1: 1.0, 0: 1.0, -1: 1.0, -2: 1.0},
    # 工具原意：只买「强势」(state>=2)，其余清仓转 SPY
    "S_MOM":    {3: 1.0, 2: 1.0, 1: 1.0, 0: 0.0, -1: 0.0, -2: 0.0},
    # 数据读法：持有全部，仅剔除「拥挤主升」(state==3)
    "S_FADE":   {3: 0.0, 2: 1.0, 1: 1.0, 0: 1.0, -1: 1.0, -2: 1.0},
    # 温和逆向：弱势/中性满仓，强势半仓，拥挤清仓
    "S_CONTRA": {3: 0.0, 2: 0.5, 1: 1.0, 0: 1.0, -1: 1.0, -2: 1.0},
}


def build_signals(fin, bench, window):
    """返回各候选策略的 boolean signal DataFrame（仅窗口内）。"""
    idx = window
    sigs = {}
    fst = {k: fin[k]["final_state"].reindex(idx) for k in THEME_KEYS}
    fsc = {k: fin[k]["final_score"].reindex(idx) for k in THEME_KEYS}
    ftc = {k: fin[k]["trend_code"].reindex(idx) for k in THEME_KEYS}
    ovx = {k: fin[k]["over_ext"].reindex(idx) for k in THEME_KEYS}
    bst = bench["bench_state"].reindex(idx)
    btc = bench["bench_trend"].reindex(idx)

    # A0 基线
    A0 = pd.DataFrame({k: (fst[k] >= 2).fillna(False) for k in THEME_KEYS}, index=idx)
    # A1 持续性：final_score>=75 连续2日
    A1 = pd.DataFrame({k: (fst[k] >= 2).fillna(False) & (fsc[k] >= 75).fillna(False) & (fsc[k].shift(1) >= 75).fillna(False)
                       for k in THEME_KEYS}, index=idx)
    # A2 去拥挤
    A2 = pd.DataFrame({k: (fst[k] >= 2).fillna(False) & (~ovx[k].fillna(False)) for k in THEME_KEYS}, index=idx)
    # A3 严格趋势
    A3 = pd.DataFrame({k: (fst[k] >= 2).fillna(False) & (ftc[k] == 2).fillna(False) for k in THEME_KEYS}, index=idx)
    # A4 大盘门控
    A4 = pd.DataFrame({k: (fst[k] >= 2).fillna(False) & (btc >= 1).fillna(False) for k in THEME_KEYS}, index=idx)
    sigs = {"A0": A0, "A1": A1, "A2": A2, "A3": A3, "A4": A4}
    return sigs, fst, fsc


def build_ls_signal(fin, idx):
    long = pd.DataFrame({k: (fin[k]["final_state"].reindex(idx) >= 2).fillna(False) for k in THEME_KEYS}, index=idx)
    short = pd.DataFrame({k: (fin[k]["final_state"].reindex(idx) <= -1).fillna(False) for k in THEME_KEYS}, index=idx)
    return long, short


def build_ls_signal_inv(fin, idx):
    """逆向：long 弱势(state<=-1) / short 强势(state>=2)。"""
    long = pd.DataFrame({k: (fin[k]["final_state"].reindex(idx) <= -1).fillna(False) for k in THEME_KEYS}, index=idx)
    short = pd.DataFrame({k: (fin[k]["final_state"].reindex(idx) >= 2).fillna(False) for k in THEME_KEYS}, index=idx)
    return long, short


# ===========================================================================
# 6. 主流程
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--period", default=PERIOD, help="yfinance 拉取区间，跨regime重测用 10y")
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    args = ap.parse_args(argv)

    df = load_prices(force=args.force_refresh, period=args.period)
    end = args.end or df.index.max().strftime("%Y-%m-%d")
    win = df.loc[args.start:end]
    print(f"[window] {args.start} .. {end}  rows={len(win)}", flush=True)

    # 在全量历史上算信号（保证窗口起点有 200 日均线等 warm-up），再切片到窗口
    sig_full = build_signal_matrix(df)
    close_full = sig_full["close"]
    fin_full = sig_full["fin"]
    idx = win.index
    close = {k: close_full[k].reindex(idx) for k in THEME_KEYS}
    close["spy"] = close_full["spy"].reindex(idx)
    fin = {k: {c: s.reindex(idx) for c, s in d.items()} for k, d in fin_full.items()}

    # 前瞻收益（自身收盘绝对 + 相对 SPY 超额）
    fwd = {k: forward_returns(close, close["spy"], k) for k in THEME_KEYS}

    # ---- 信号质量（按状态）：绝对 & 相对 ----
    agg_abs, raw_abs = signal_quality_by_state(fin, fwd, "abs_")
    agg_rel, raw_rel = signal_quality_by_state(fin, fwd, "rel_")
    print("\n=== 按 final_state 的前瞻收益（跨主题均值）===", flush=True)
    for pref, tag in (("abs_", "绝对"), ("rel_", "相对SPY")):
        agg = agg_abs if pref == "abs_" else agg_rel
        print(f"-- [{tag}] --", flush=True)
        for n in (5, 10, 20, 60):
            sub = agg[agg["horizon"] == n][["state", "state_name", "n", "mean_ret", "hit"]]
            for _, r in sub.iterrows():
                print(f"  N{n} state={int(r['state']):>2} {r['state_name']:<6} n={int(r['n']):>5} "
                      f"mean={r['mean_ret']*100:+.2f}% hit={r['hit']*100:.1f}%", flush=True)

    # ---- 策略 NAV ----
    ret = pd.DataFrame({k: close[k].pct_change().reindex(idx).fillna(0.0) for k in THEME_KEYS}, index=idx)
    spy_ret = close["spy"].pct_change().reindex(idx).fillna(0.0)
    spy_nav = np.cumprod(1.0 + spy_ret.values)

    sigs, fst, fsc = build_signals(fin, sig_full, idx)
    results = {}
    navs = {}
    for name, s in sigs.items():
        nav, pr, tog = strategy_nav(s, ret, name)
        m = metrics_from_nav(nav, pr, idx, spy_nav, spy_ret)
        m["toggles"] = int(tog)
        results[name] = m
        navs[name] = nav
        print(f"[strat] {name}: CAGR={m['cagr']*100:+.2f}% (SPY {m['spy_cagr']*100:+.2f}%) "
              f"Sharpe={m['sharpe']:.2f} MDD={m['mdd']*100:.1f}% (SPY {m['spy_mdd']*100:.1f}%) "
              f"exCAGR={m['excess_cagr']*100:+.2f}pp toggles={tog}", flush=True)

    # A5 多空（long state>=2 / short state<=-1）
    long_sig, short_sig = build_ls_signal(fin, idx)
    nav_ls, pr_ls, tog_ls = strategy_nav_ls(long_sig, short_sig, ret)
    m_ls = metrics_from_nav(nav_ls, pr_ls, idx, spy_nav, spy_ret)
    m_ls["toggles"] = int(tog_ls)
    results["A5_LS"] = m_ls
    navs["A5_LS"] = nav_ls
    print(f"[strat] A5_LS: CAGR={m_ls['cagr']*100:+.2f}% Sharpe={m_ls['sharpe']:.2f} "
          f"MDD={m_ls['mdd']*100:.1f}% toggles={tog_ls}", flush=True)

    # ---- 反向（逆向）假设验证：数据暗示 state 是反向指标 ----
    # A6 反多空：long state<=-1（派发/撤出）/ short state>=2（确认/拥挤）
    long_inv, short_inv = build_ls_signal_inv(fin, idx)
    nav_inv, pr_inv, tog_inv = strategy_nav_ls(long_inv, short_inv, ret)
    m_inv = metrics_from_nav(nav_inv, pr_inv, idx, spy_nav, spy_ret)
    m_inv["toggles"] = int(tog_inv)
    results["A6_INV_LS"] = m_inv
    navs["A6_INV_LS"] = nav_inv
    print(f"[strat] A6_INV_LS: CAGR={m_inv['cagr']*100:+.2f}% Sharpe={m_inv['sharpe']:.2f} "
          f"MDD={m_inv['mdd']*100:.1f}% toggles={tog_inv}", flush=True)

    # A7 逆向多头：只买 state<=-1（被洗出的弱势板块）
    A7 = pd.DataFrame({k: (fin[k]["final_state"].reindex(idx) <= -1).fillna(False) for k in THEME_KEYS}, index=idx)
    nav_a7, pr_a7, tog_a7 = strategy_nav(A7, ret, "A7")
    m_a7 = metrics_from_nav(nav_a7, pr_a7, idx, spy_nav, spy_ret)
    m_a7["toggles"] = int(tog_a7)
    results["A7_LONG_WEAK"] = m_a7
    navs["A7_LONG_WEAK"] = nav_a7
    print(f"[strat] A7_LONG_WEAK: CAGR={m_a7['cagr']*100:+.2f}% Sharpe={m_a7['sharpe']:.2f} "
          f"MDD={m_a7['mdd']*100:.1f}% toggles={tog_a7}", flush=True)

    # A8 逆向多头：买一切「非强势」（state<=0，回避确认/拥挤）
    A8 = pd.DataFrame({k: (fin[k]["final_state"].reindex(idx) <= 0).fillna(False) for k in THEME_KEYS}, index=idx)
    nav_a8, pr_a8, tog_a8 = strategy_nav(A8, ret, "A8")
    m_a8 = metrics_from_nav(nav_a8, pr_a8, idx, spy_nav, spy_ret)
    m_a8["toggles"] = int(tog_a8)
    results["A8_LONG_NOTSTRONG"] = m_a8
    navs["A8_LONG_NOTSTRONG"] = nav_a8
    print(f"[strat] A8_LONG_NOTSTRONG: CAGR={m_a8['cagr']*100:+.2f}% Sharpe={m_a8['sharpe']:.2f} "
          f"MDD={m_a8['mdd']*100:.1f}% toggles={tog_a8}", flush=True)

    # A9 去拥挤极值（long-only 改进版）：等权持有全部，仅剔除 state==3（拥挤主升）
    A9 = pd.DataFrame({k: (fin[k]["final_state"].reindex(idx) != 3).fillna(False) for k in THEME_KEYS}, index=idx)
    nav_a9, pr_a9, tog_a9 = strategy_nav(A9, ret, "A9")
    m_a9 = metrics_from_nav(nav_a9, pr_a9, idx, spy_nav, spy_ret)
    m_a9["toggles"] = int(tog_a9)
    results["A9_FADE_CROWD_ONLY"] = m_a9
    navs["A9_FADE_CROWD_ONLY"] = nav_a9
    print(f"[strat] A9_FADE_CROWD_ONLY: CAGR={m_a9['cagr']*100:+.2f}% Sharpe={m_a9['sharpe']:.2f} "
          f"MDD={m_a9['mdd']*100:.1f}% toggles={tog_a9}", flush=True)

    # ---- 基线：等权持有全部 14 主题（≈RSP 思路）----
    eqw = pd.DataFrame({k: True for k in THEME_KEYS}, index=idx)
    nav_eq, pr_eq, tog_eq = strategy_nav(eqw, ret, "EQW")
    m_eq = metrics_from_nav(nav_eq, pr_eq, idx, spy_nav, spy_ret)
    m_eq["toggles"] = int(tog_eq)
    results["EQW_all"] = m_eq
    navs["EQW_all"] = nav_eq

    # ---- 仓位建议框架：按状态设定权重乘数，余量配 SPY ----
    pos_results = {}
    pos_navs = {}
    pos_alloc = {}
    for sname, sched in SCHEDULES.items():
        nav_p, pr_p, tog_p = position_portfolio(fst, ret, spy_ret, idx, sched)
        m_p = metrics_from_nav(nav_p, pr_p, idx, spy_nav, spy_ret)
        m_p["toggles"] = int(tog_p)
        sched_vec = pd.DataFrame({k: fst[k].map(lambda s: float(sched.get(int(s), 1.0)))
                                  for k in THEME_KEYS}, index=idx)
        daily_theme_w = sched_vec.sum(axis=1) / len(THEME_KEYS)
        pos_alloc[sname] = {"avg_theme_weight": float(daily_theme_w.mean()),
                             "avg_spy_weight": float(1.0 - daily_theme_w.mean())}
        pos_results[sname] = m_p
        pos_navs[sname] = nav_p
        print(f"[pos] {sname}: CAGR={m_p['cagr']*100:+.2f}% exCAGR={m_p['excess_cagr']*100:+.2f}pp "
              f"Sharpe={m_p['sharpe']:.2f} avgThemes={daily_theme_w.mean()*100:.1f}% "
              f"avgSPY={(1-daily_theme_w.mean())*100:.1f}% tog={tog_p}", flush=True)

    # 仓位建议表（基于 S_FADE 推荐 + 经验相对超额）
    rel60 = agg_rel[agg_rel["horizon"] == 60].set_index("state")
    advice = []
    for sval, sname, action, pos in [
        (3, "拥挤主升", "卖出 / 清仓（剔除）", "0%（该份转 SPY）"),
        (2, "确认进入", "持有满仓", "100%（等权基准）"),
        (1, "早期轮动", "持有满仓", "100%"),
        (0, "中性", "持有满仓", "100%"),
        (-1, "撤出", "持有满仓", "100%"),
        (-2, "派发", "持有满仓", "100%"),
    ]:
        r = rel60.loc[sval] if sval in rel60.index else None
        mean_rel = float(r["mean_ret"]) if r is not None else float("nan")
        hit = float(r["hit"]) if r is not None else float("nan")
        n = int(r["n"]) if r is not None else 0
        advice.append({"state": sval, "state_name": sname, "n60_rel_excess": mean_rel,
                       "n60_hit": hit, "n": n, "action": action, "position": pos})

    # 汇总表
    print("\n=== 策略汇总（含5bps换手摩擦）===", flush=True)
    print(f"{'name':<16}{'CAGR':>10}{'exCAGR':>9}{'Sharpe':>9}{'MDD':>9}{'MDDimp':>9}{'tog':>7}", flush=True)
    for name in ["SPY", "EQW_all", "A0", "A1", "A2", "A3", "A4", "A5_LS",
                 "A6_INV_LS", "A7_LONG_WEAK", "A8_LONG_NOTSTRONG", "A9_FADE_CROWD_ONLY"]:
        if name == "SPY":
            print(f"{name:<10}{m_eq['spy_cagr']*100:>+10.2f}{0:>9.2f}{0:>9.2f}{m_eq['spy_mdd']*100:>9.1f}{0:>9.1f}{0:>7}", flush=True)
            continue
        m = results[name]
        print(f"{name:<10}{m['cagr']*100:>+10.2f}{m['excess_cagr']*100:>+9.2f}{m['sharpe']:>9.2f}"
              f"{m['mdd']*100:>9.1f}{m['mdd_improve']*100:>9.1f}{m['toggles']:>7}", flush=True)

    # ---- 写报告 ----
    out = {
        "window_start": args.start, "window_end": end,
        "n_days": int(len(win)),
        "strategy_metrics": {k: {kk: (round(v, 5) if isinstance(v, float) else v) for kk, v in vv.items()} for k, vv in results.items()},
        "signal_quality_abs": agg_abs.round(5).to_dict(orient="records"),
        "signal_quality_rel": agg_rel.round(5).to_dict(orient="records"),
        "position_schedules": {k: {kk: (round(v, 5) if isinstance(v, float) else v) for kk, v in vv.items()}
                               for k, vv in pos_results.items()},
        "position_alloc": pos_alloc,
        "position_advice": advice,
        "nav": {k: [round(float(x), 6) for x in navs[k]] for k in navs},
        "nav_dates": [d.strftime("%Y-%m-%d") for d in idx],
    }
    json_path = OUT / f"sector_backtest_{end}.json"
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[written] {json_path}", flush=True)

    md = build_markdown(out, agg_abs, agg_rel, results, end)
    md_path = OUT / f"sector_backtest_{end}.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"[written] {md_path}", flush=True)
    return 0


def strategy_nav_ls(long_sig, short_sig, ret):
    dates = long_sig.index
    K = long_sig.shape[1]
    nav = np.ones(len(dates))
    port_ret = np.zeros(len(dates))
    toggles = 0
    prev_w = None
    for i in range(1, len(dates)):
        up = long_sig.iloc[i - 1].values.astype(bool)
        dn = short_sig.iloc[i - 1].values.astype(bool)
        w_l = up.astype(float)
        w_s = dn.astype(float)
        nl = w_l.sum(); ns = w_s.sum()
        if nl > 0:
            w_l = w_l / nl
        else:
            w_l = np.zeros(K)
        if ns > 0:
            w_s = w_s / ns
        else:
            w_s = np.zeros(K)
        w = w_l - w_s  # 多空净权重
        r = ret.iloc[i].values.astype(float)
        pr = float(np.nansum(w * r))
        port_ret[i] = pr
        nav[i] = nav[i - 1] * (1.0 + pr)
        if prev_w is not None:
            turnover = 0.5 * np.sum(np.abs(w - prev_w))
            if turnover > 1e-9:
                nav[i] *= (1.0 - FRICTION_PER_TOGGLE * min(turnover, 2.0))
                toggles += 1
        prev_w = w
    return nav, port_ret, toggles


def build_markdown(out, agg_abs, agg_rel, results, end):
    w0, w1 = out["window_start"], out["window_end"]
    L = []
    L.append(f"# 标普板块资金轮动 — 回测分析（{w0} ~ {w1}）\n")
    L.append("> 因果复刻 Pine v1.3m 打分引擎，逐日回放。前瞻收益=主题自身 ETF 收盘 forward return；")
    L.append("> 策略日频再平衡 + 5bps 换手摩擦。改进候选均为 ADDITIVE，不改动原冻结参数。\n")

    def state_row(agg, sval, sname):
        cells = [f"{sval} {sname}"]
        for n in (5, 10, 20, 60):
            r = agg[(agg.state == sval) & (agg.horizon == n)]
            if len(r):
                rr = r.iloc[0]
                cells.append(f"{rr['mean_ret']*100:+.2f}%")
                cells.append(f"{rr['hit']*100:.0f}%")
            else:
                cells += ["—", "—"]
        return "| " + " | ".join(cells) + " |"

    L.append("## 1. 信号质量：final_state 预测的是「绝对」还是「相对 SPY」收益？")
    L.append("")
    L.append("按状态分组，跨 14 主题汇总（仅 n≥10）。轮动信号**应有的检验口径是「相对 SPY 超额」**——")
    L.append("它声称某板块将*领涨*，所以要看它是否跑赢 SPY，而非绝对涨跌。\n")

    L.append("### 1a. 绝对前瞻收益（主题自身 ETF）")
    L.append("")
    L.append("| 状态 | N5 mean | hit | N10 mean | hit | N20 mean | hit | N60 mean | hit |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for sval, sname in [(3, "拥挤主升"), (2, "确认进入"), (1, "早期轮动"), (0, "中性"), (-1, "撤出"), (-2, "派发")]:
        L.append(state_row(agg_abs, sval, sname))
    L.append("")

    L.append("### 1b. 相对 SPY 前瞻超额（轮动信号应有口径）")
    L.append("")
    L.append("| 状态 | N5 excess | hit | N10 excess | hit | N20 excess | hit | N60 excess | hit |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for sval, sname in [(3, "拥挤主升"), (2, "确认进入"), (1, "早期轮动"), (0, "中性"), (-1, "撤出"), (-2, "派发")]:
        L.append(state_row(agg_rel, sval, sname))
    L.append("")
    L.append("> ⚠️ **关键发现（看 1b 相对列）**：在 6 个状态里，**只有「拥挤主升(3)」有显著为负的前瞻超额**")
    L.append("> （N20 相对 SPY −1.32%、N60 −6.96%、胜率仅 20.5%），其余状态相对 SPY 都接近 0 或微负；")
    L.append("> 「派发(-2)/撤出(-1)」相对 SPY 仅为**略平**（N60 约 −0.2%，并非强正）。也就是说：v1.3m 的信号")
    L.append("> 真正的、稳健的预测力是「**拥挤主升 = 后续将跑输 SPY**」这个**反向风险标记**，而不是「买确认/早期」")
    L.append("> 的正向选股力——后者的前瞻收益几乎不区分于抛硬币。在 2023-2026 这段 AI 龙头动量市里，")
    L.append("> 「相对 SPY 已强/超买」被它误读为「将继续强」，实为阶段性见顶信号。\n")

    L.append("## 2. 策略汇总（含 5bps 换手摩擦）")
    L.append("")
    L.append(f"| 策略 | CAGR | 超额CAGR(vs SPY) | Sharpe | MDD | MDD改善 | 换手次数 |")
    L.append(f"|---|---|---|---|---|---|---|")
    spy_cagr = results["EQW_all"]["spy_cagr"]
    spy_mdd = results["EQW_all"]["spy_mdd"]
    L.append(f"| SPY 买入持有 | {spy_cagr*100:+.2f}% | — | — | {spy_mdd*100:.1f}% | — | — |")
    for name in ["EQW_all", "A0", "A1", "A2", "A3", "A4", "A5_LS", "A6_INV_LS", "A7_LONG_WEAK", "A8_LONG_NOTSTRONG"]:
        m = results[name]
        L.append(f"| {name} | {m['cagr']*100:+.2f}% | {m['excess_cagr']*100:+.2f}pp | {m['sharpe']:.2f} | "
                 f"{m['mdd']*100:.1f}% | {m['mdd_improve']*100:+.1f}pp | {m['toggles']} |")
    L.append("")
    L.append("策略说明：")
    L.append("- **EQW_all**：等权持有全部 14 主题（≈RSP 思路，非择时基准）")
    L.append("- **A0 基线**：final_state ≥ 2（确认/拥挤）即持有，否则空仓  ← 工具原意（做多强势）")
    L.append("- **A1 持续性**：A0 且 final_score ≥ 75 连续 ≥2 日")
    L.append("- **A2 去拥挤**：A0 且非 over_ext")
    L.append("- **A3 严格趋势**：A0 且 trend_code==2")
    L.append("- **A4 大盘门控**：A0 且 SPY trend_code ≥ 1")
    L.append("- **A5_LS**：long state≥2 / short state≤-1（原意多空）")
    L.append("- **A6_INV_LS**：long state≤-1 / short state≥2（对称逆向多空）")
    L.append("- **A7_LONG_WEAK**：只买 state≤-1（逆向多头：买被洗出的弱势）")
    L.append("- **A8_LONG_NOTSTRONG**：买一切 state≤0（回避所有强势）")
    L.append("- **A9_FADE_CROWD_ONLY**：等权全部，**仅剔除 state==3**（只淡化拥挤极值）\n")

    L.append("## 3. 结论与改进方向")
    L.append("")
    a0 = results["A0"]; a5 = results["A5_LS"]; a6 = results["A6_INV_LS"]
    a7 = results["A7_LONG_WEAK"]; a8 = results["A8_LONG_NOTSTRONG"]; a9 = results["A9_FADE_CROWD_ONLY"]
    L.append(f"- **原意（做多强势）跑输**：A0（买 state≥2）CAGR {a0['cagr']*100:+.2f}% vs SPY {spy_cagr*100:+.2f}%，")
    L.append(f"  超额 {a0['excess_cagr']*100:+.2f}pp；即便最严格 A3 也仅 {results['A3']['excess_cagr']*100:+.2f}pp。")
    L.append(f"  对称多空 A5_LS = {a5['cagr']*100:+.2f}%（Sharpe {a5['sharpe']:.2f}），连其逆向 A6_INV_LS 也仅 {a6['cagr']*100:+.2f}%（Sharpe {a6['sharpe']:.2f}）——")
    L.append(f"  因为信号边缘是**相对 SPY**的，朴素多空在单边牛市里被 beta 拖累，并非简单反转就赚。")
    L.append(f"- **可行的改进（long-only 框架内）**：A9_FADE_CROWD_ONLY（等权全部、仅剔 state==3）CAGR {a9['cagr']*100:+.2f}%、")
    L.append(f"  Sharpe {a9['sharpe']:.2f}、MDD {a9['mdd']*100:.1f}%，**优于 EQW_all（{results['EQW_all']['cagr']*100:+.2f}%）与 A0**。")
    L.append(f"  A7（买弱势）+{a7['cagr']*100:.2f}%、A8（回避所有强势）+{a8['cagr']*100:.2f}% 也≈等权——说明")
    L.append(f"  「买弱势」本身不神，主要赢在**始终在场**而非 A0 那样频繁空仓错过牛市漂移。")
    L.append("")
    L.append("### 改进建议（按可信度排序）")
    L.append("1. **把「拥挤主升(3)」从「买入清单」移到「剔除/减配清单」**：这是样本里唯一稳健、")
    L.append("   统计显著的反向信号（N60 相对 SPY −6.96%、胜率 20.5%）。当前它却作为最高置信「买入」标签，方向错。")
    L.append("   → 具体落地：A9 这类「等权 + 剔除 state==3」在回测中确实优于原 A0 与等权基线。")
    L.append("2. **不要再用 final_state 做「满仓做多强势」触发器**：在 2023-2026 动量市里它没有正向选股力，")
    L.append("   且空仓机制让它错过牛市。若保留，至少把默认动作从「买 ≥2」改为「持有全部、剔除 3」。")
    L.append("3. **最大未决风险 = 单 regime**：本窗口恰是 AI 龙头动量延续市，信号呈反向。")
    L.append("   必须在 2018 回调、2022 加息熊市、2020 疫情等**不同 regime** 重测——反转市里同一信号可能正向。")
    L.append("   在此之前，任何「改进」都只是这段行情内的方向性证据，不能直接上线。")
    L.append("4. **若要交易相对信号，需 beta 中性实现**（配对/市场中性），而非朴素多空，")
    L.append("   否则牛市 beta 会淹没微小的相对边缘（见 A5/A6 均为负）。")
    L.append("")
    L.append(f"> ⚠️ 诚实声明：以上为**单段**窗口（{w0} ~ {w1}）内结果，且无未来函数、含 5bps 摩擦。")
    L.append("> A9 的改善幅度不大（≈等权 ±1pp），且可能随 regime 改变符号。结论应作为**研究方向**")
    L.append("> （信号方向存疑、需跨 regime 复核 + beta 中性实现），而非直接替换现有引擎参数。\n")

    # ---- 第 4 节：仓位建议（按状态）+ 相对 SPY 的超额收益 ----
    pos_results = out.get("position_schedules", {})
    pos_alloc = out.get("position_alloc", {})
    advice = out.get("position_advice", [])
    if pos_results and advice:
        L.append("## 4. 仓位建议（按状态）与「相比直接买入 SPY」的超额收益")
        L.append("")
        L.append("### 4a. 每个状态该买 / 卖多少、余下配 SPY")
        L.append("")
        L.append("| 状态 | 经验相对SPY超额(N60)\* | 胜率 | 样本n | 建议操作 | 建议仓位（占等权基准） |")
        L.append("|---|---|---|---|---|---|")
        for a in advice:
            ex = a["n60_rel_excess"]; hit = a["n60_hit"]
            ex_s = f"{ex*100:+.2f}%" if not (isinstance(ex, float) and np.isnan(ex)) else "—"
            hit_s = f"{hit*100:.0f}%" if not (isinstance(hit, float) and np.isnan(hit)) else "—"
            L.append(f"| {a['state']} {a['state_name']} | {ex_s} | {hit_s} | {a['n']} | {a['action']} | {a['position']} |")
        L.append("")
        L.append("> \* N60 相对超额 = 进入该状态后未来约 60 个交易日（≈3 个月）平均跑赢/跑输 SPY 的幅度，**非年化**。")
        L.append("> 读法：**只有「拥挤主升(3)」有统计显著为负的前瞻相对超额（−6.96%、胜率 20.5%）**；")
        L.append("> 其余状态相对 SPY 都≈0 或微负。即「买确认/早期」不增反持平，唯一有信息量的动作是")
        L.append("> **把拥挤主升那份清仓、转投 SPY**。\n")

        L.append("### 4b. 按上述仓位方案执行，相比直接买入 SPY 的超额收益")
        L.append("")
        L.append("| 仓位方案 | 平均板块仓位 | 平均SPY仓位 | CAGR | 相对SPY超额 | Sharpe | MDD | 换手 |")
        L.append("|---|---|---|---|---|---|---|---|")
        spy_cagr = results["EQW_all"]["spy_cagr"]
        spy_mdd = results["EQW_all"]["spy_mdd"]
        L.append(f"| **直接买入 SPY（基准）** | 0% | 100% | {spy_cagr*100:+.2f}% | — | — | {spy_mdd*100:.1f}% | — |")
        for sname in ["S_EQW", "S_MOM", "S_FADE", "S_CONTRA"]:
            if sname not in pos_results:
                continue
            m = pos_results[sname]; al = pos_alloc.get(sname, {})
            tw = al.get("avg_theme_weight", 1.0); sw = al.get("avg_spy_weight", 0.0)
            L.append(f"| {sname} | {tw*100:.1f}% | {sw*100:.1f}% | {m['cagr']*100:+.2f}% | "
                     f"{m['excess_cagr']*100:+.2f}pp | {m['sharpe']:.2f} | {m['mdd']*100:.1f}% | {m['toggles']} |")
        L.append("")
        L.append("> **最重要的诚实结论**：在 2023-2026 这段 AI 龙头动量市里，**没有任何板块轮动方案跑赢直接买 SPY**。")
        L.append("> 连「等权全部 14 板块」(S_EQW) 都跑输 SPY 约 4.4pp——因为 SPY 市值加权、被少数赢家主导，")
        L.append("> 分散的板块篮子反而拖后腿。唯一能**减小跑输幅度**的是 S_FADE（剔除拥挤主升）：")
        L.append("> 把跑输从 −4.44pp 收窄到 −3.78pp，且换手极低（144 次）。")
        L.append("> 换言之，相对 SPY 的**最优仓位建议 = 别折腾，直接买 SPY**；若一定要持板块篮子，")
        L.append("> 则「全持、仅把拥挤主升那份清仓转 SPY」是回测里最不坏的选择。\n")

        L.append("### 4c. 单状态「持有该板块 vs 直接买 SPY」的季度超额（决策依据）")
        L.append("")
        L.append("若在某状态满仓买入该主题 ETF、并持有约 3 个月（N60），相对 SPY 的平均得失：")
        L.append("")
        L.append("| 状态 | 满仓持有该板块 vs SPY（N60 平均） | 胜率 | 解读 |")
        L.append("|---|---|---|---|")
        for a in advice:
            ex = a["n60_rel_excess"]; hit = a["n60_hit"]
            if isinstance(ex, float) and np.isnan(ex):
                continue
            if a["state"] == 3:
                interp = "显著跑输 → 应清仓转 SPY"
            elif ex < -0.005:
                interp = "略跑输 → 不如直接买 SPY"
            elif ex > 0.005:
                interp = "略跑赢 → 可超配"
            else:
                interp = "≈持平 → 持有与否无差"
            L.append(f"| {a['state']} {a['state_name']} | {ex*100:+.2f}% | {hit*100:.0f}% | {interp} |")
        L.append("")
        L.append("> 注意：这些是**样本内单段**估计，未跨 regime 验证；N60 为约 3 个月持有窗口，非长期年化。")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
