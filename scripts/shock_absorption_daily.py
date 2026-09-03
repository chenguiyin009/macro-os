#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场冲击消化能力评判 · 日频 Python 端口 (Shock Absorption · daily headless port)

--------------------------------------------------------------------------------
WHY THIS EXISTS
--------------------------------------------------------------------------------
Pine v6 指标《市场冲击消化能力评判》的本地日频复刻（详见 pine2python-sop）。
本体逻辑：每天先看市场有没有受到冲击（某条腿变动稀有度 z 过线才认冲击），
认定冲击后再评吸收质量——损伤比 q = 股指损伤z / |冲击z|：
    q < 0.5   接受冲击（股指伤得远轻于冲击，q 为负说明冲击当天不跌反涨）
    0.5<=q<1  冲击加剧
    q >= 1    破裂（市场在放大而不是吸收）
四条冲击腿：利率(久期换算bp,双边) / 美元(双边) / 信用(单边,HYG被砸) / 波动(单边,VIX上冲)。
另含股指损伤量表、20日滚动记录、迟滞状态机(脆弱累积/高度脆弱)、三个指纹
(保证金抛售/股债汇三杀/BTC金丝雀)、0-100 冲击评分。

它本质是**分母压力记账器**，定位为 macro-os daily 管线里 denominator 模块的
**旁证交叉校验**——只观察 + 报警，不进主状态机，不动合成预算 min。

数据：yfinance 代理（见 SYMBOL_MAP）。MOVE 为 TV-only，按 Pine 原意「缺数弃权，
不参与任何计算」处理。DXY 用 DX-Y.NYB 代理。

OUTPUT: output/shock_absorption_<date>.{md,json}
  <date> = 对齐后最新可得交易日（union of business days, ffill 对齐）

所有门槛初值未经回放标定（对应 Pine 头部 SA-1），由 argparse 暴露，便于后续标定。
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Defensive: 单线程，避免内存压力下 BLAS 线程扇出假 OOM（与 denominator 端口一致）。
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_MAX_THREADS", "1")

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT.parent / "output"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("shock-absorption-daily")

DEFAULT_PROXY = "http://127.0.0.1:7890"
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY")

# 外部环境（如 grokbot 云机无本地代理）可设 SECTOR_PROXY 重定向或关闭：
#   SECTOR_PROXY=off|none|0|false|no|""  → 完全不设代理（依赖直连/环境自带代理）
#   SECTOR_PROXY=http://host:port         → 使用指定代理
# 若已设置系统变量 HTTPS_PROXY/HTTP_PROXY，则优先级最高、本函数不覆盖。
_PROXY_OFF_VALUES = ("", "off", "none", "0", "false", "no")

# ---- 阶段一：Ticker 映射字典 (TV ticker -> yfinance 代理) ----
# kind 仅用于文档化：双边/单边在腿判定里单独处理。
SYMBOL_MAP: Dict[str, str] = {
    "zn":   "ZN=F",     # CBOT:ZN1! 10年期国债期货
    "ub":   "UB=F",     # CBOT:UB1! 超长期国债期货
    "dxy":  "DX-Y.NYB", # TVC:DXY 美元指数
    "es":   "ES=F",     # CME_MINI:ES1!
    "nq":   "NQ=F",     # CME_MINI:NQ1!
    "btc":  "BTC-USD",  # BITSTAMP:BTCUSD
    "gc":   "GC=F",     # COMEX:GC1! 黄金期货
    "hyg":  "HYG",      # AMEX:HYG 高收益债
    "lqd":  "LQD",      # AMEX:LQD 投资级债（质量差分母）
    "kre":  "KRE",      # AMEX:KRE 区域银行
    "vix":  "^VIX",     # CBOE:VIX
    # MOVE (TVC:MOVE) 为 TV-only，无开源代理。Pine 里 MOVE 仅第17行展示、
    # 不进任何判定也不计入 20日破裂(fail20)，故弃权对状态机与记录零影响。
}
LABELS = {
    "zn": "ZN(10Y国债)", "ub": "UB(超长债)", "dxy": "美元指数", "es": "ES",
    "nq": "NQ", "btc": "BTC", "gc": "黄金", "hyg": "HYG", "lqd": "LQD",
    "kre": "KRE", "vix": "VIX",
}


# --------------------------------------------------------------------------- #
# 阶段二：代理注入 + 防御式取数
# --------------------------------------------------------------------------- #
def _ensure_proxy() -> None:
    """Best-effort: set a local proxy if none is configured (yfinance needs it here).

    Honors SECTOR_PROXY so cloud runners without the local 127.0.0.1:7890 proxy
    (e.g. grokbot) run with direct egress or their own proxy instead of being
    silently pointed at a non-existent local proxy.
    """
    if any(os.environ.get(k) for k in _PROXY_ENV_KEYS):
        return
    sp = os.environ.get("SECTOR_PROXY")
    if sp is not None and str(sp).strip().lower() in _PROXY_OFF_VALUES:
        return
    try:
        proxy = sp if (sp and str(sp).strip()) else DEFAULT_PROXY
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
    except Exception:
        pass


def _download_close(ticker: str, period: str = "3y") -> Optional[pd.Series]:
    """拉取单标的日线收盘，防御式 MultiIndex 解析 + 数值清洗 + 最少样本门槛。"""
    import yfinance as yf
    _ensure_proxy()
    try:
        raw = yf.download(ticker, period=period, auto_adjust=True,
                          progress=False, threads=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance download failed %s: %s", ticker, exc)
        return None
    if raw is None or getattr(raw, "empty", True):
        logger.warning("yfinance empty: %s", ticker)
        return None
    cols = raw.columns
    close = None
    if getattr(cols, "nlevels", 1) > 1:
        if ("Close", ticker) in cols:
            close = raw[("Close", ticker)]
        elif (ticker, "Close") in cols:
            close = raw[(ticker, "Close")]
        elif "Close" in cols.get_level_values(0):
            cd = raw.xs("Close", axis=1, level=0)
            close = cd[ticker] if ticker in cd.columns else cd.iloc[:, 0]
        else:
            return None
    else:
        close = raw["Close"] if "Close" in cols else (raw[ticker] if ticker in cols else None)
    if close is None:
        return None
    s = pd.to_numeric(close, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s if len(s) >= 60 else None


def fetch_closes(period: str = "3y") -> Dict[str, pd.Series]:
    closes: Dict[str, pd.Series] = {}
    for key, code in SYMBOL_MAP.items():
        s = _download_close(code, period=period)
        if s is not None:
            closes[key] = s
            logger.info("fetched %s (%s): %d bars", key, code, len(s))
        else:
            logger.warning("skip %s (%s): no data", key, code)
    return closes


# --------------------------------------------------------------------------- #
# 阶段三：向量化指标 (Pine -> Pandas)
# --------------------------------------------------------------------------- #
def chg_z(close: pd.Series, lb: int, z_len: int) -> Tuple[pd.Series, pd.Series]:
    """f_chgZ: 零心 z = roc / stdev(roc). 不减均值(防趋势期均值漂移误标)."""
    ret = close.pct_change(lb)
    sd = ret.rolling(z_len, min_periods=z_len).std(ddof=0)  # Pine stdev = 总体 = ddof=0
    z = ret / sd
    return ret, z


def bp_z(close: pd.Series, lb: int, z_len: int, dur: float
         ) -> Tuple[pd.Series, pd.Series]:
    """f_bpZ: 价格变动经久期换算成收益率 bp 当量，再取零心 z。"""
    ret = close.pct_change(lb)
    chgP_pct = ret * 100.0                       # 对齐 ta.roc (百分比)
    bp = -chgP_pct / dur * 100.0                 # 收益率 bp 当量
    sd = bp.rolling(z_len, min_periods=z_len).std(ddof=0)
    z = bp / sd
    return bp, z


def pct_rank(close: pd.Series, length: int) -> pd.Series:
    """ta.percentrank: 当前值在最近 length 根内的百分位 (0-100, 含当前)。"""
    def _rank(x: np.ndarray) -> float:
        cur = x[-1]
        return 100.0 * float((x < cur).sum()) / len(x)
    return close.rolling(length, min_periods=length).apply(_rank, raw=True)


def _grade(hit: pd.Series, shock_z: pd.Series, dmg_z: pd.Series,
           q_strain: float, q_fail: float) -> pd.Series:
    """f_grade: 0=无冲击 1=接受 2=加剧 3=破裂。dmg_z 为 NaN 时判 0。"""
    code = pd.Series(0, index=hit.index, dtype=int)
    q = pd.Series(np.nan, index=hit.index, dtype=float)
    m = hit & dmg_z.notna()
    q[m] = dmg_z[m] / shock_z[m].abs()
    code[m & (q >= q_fail)] = 3
    code[m & (q >= q_strain) & (q < q_fail)] = 2
    code[m & (q < q_strain)] = 1
    return code, q


# --------------------------------------------------------------------------- #
# 核心计算
# --------------------------------------------------------------------------- #
def compute_state(closes: Dict[str, pd.Series], args) -> dict:
    lb = args.lb
    z_len = args.z_len
    vix_pct_len = args.vix_pct_len
    z_exam = args.z_exam
    z_big = args.z_big
    q_strain = args.q_strain
    q_fail = args.q_fail
    enter1 = args.enter1
    enter2 = args.enter2
    dur_zn = args.dur_zn
    dur_ub = args.dur_ub

    # 对齐：取所有符号共有交易日的**交集**（gaps_on 等价做法）。
    # 不 ffill/bfill：某符号缺 K 的那天直接整组弃权，避免 FX/BTC 的隔日行
    # 把收盘外推到 ES/NQ/VIX 的空缺日，制造 pct_change=0 的假平 bar。
    # 交集天然剔除"只有 FX/BTC 有、ES/NQ 还没收盘"的尾部 stub 日（如盘前 8-12），
    # 否则 pandas 对带尾部 NaN 的列做 position-based rolling 会把末端整列判无效。
    common_idx = None
    for s in closes.values():
        common_idx = s.index if common_idx is None else common_idx.intersection(s.index)
    if common_idx is None or len(common_idx) == 0:
        raise RuntimeError("no common trading days across symbols")
    df = pd.DataFrame({k: v.reindex(common_idx) for k, v in closes.items()})
    if df.empty:
        raise RuntimeError("no closes available to compute")
    idx = df.index

    # --- 各腿 z 序列 ---
    zn_bp, zn_z = bp_z(df["zn"], lb, z_len, dur_zn) if "zn" in df else (None, None)
    ub_bp, ub_z = bp_z(df["ub"], lb, z_len, dur_ub) if "ub" in df else (None, None)
    dxy_ret, dxy_z = chg_z(df["dxy"], lb, z_len) if "dxy" in df else (None, None)
    es_ret, es_z = chg_z(df["es"], lb, z_len) if "es" in df else (None, None)
    nq_ret, nq_z = chg_z(df["nq"], lb, z_len) if "nq" in df else (None, None)
    btc_ret, btc_z = chg_z(df["btc"], lb, z_len) if "btc" in df else (None, None)
    gc_ret, gc_z = chg_z(df["gc"], lb, z_len) if "gc" in df else (None, None)
    hyg_ret, hyg_z = chg_z(df["hyg"], lb, z_len) if "hyg" in df else (None, None)
    kre_ret, kre_z = chg_z(df["kre"], lb, z_len) if "kre" in df else (None, None)
    vix_ret, vix_z = chg_z(df["vix"], lb, z_len) if "vix" in df else (None, None)
    vix_pct = pct_rank(df["vix"], vix_pct_len) if "vix" in df else None

    # 质量差 = HYG/LQD 比价的零心 z（仅显示，不进判定）
    qual_z = None
    if "hyg" in df and "lqd" in df:
        qual = (df["hyg"] / df["lqd"]).dropna()
        if len(qual) >= z_len:
            _, qual_z = chg_z(qual, lb, z_len)

    # MOVE 缺数弃权（TV-only，无开源代理）—— 全程以 NaN 参与，任何计算都不引用。
    move_z = pd.Series(np.nan, index=idx)

    # --- 股指损伤量表: eqDmgZ = -(esZ+nqZ)/2, 正=在跌 ---
    if es_z is None and nq_z is None:
        eq_dmg_z = pd.Series(np.nan, index=idx)
    elif es_z is None:
        eq_dmg_z = -nq_z
    elif nq_z is None:
        eq_dmg_z = -es_z
    else:
        eq_dmg_z = -(es_z + nq_z) / 2.0

    # --- 利率腿: 取 |z| 更大的那条 ---
    rate_z = rate_bp = None
    rate_long_end = False
    rate_abstain = (zn_z is None and ub_z is None)
    if not rate_abstain:
        if zn_z is None:
            rate_z, rate_bp, rate_long_end = ub_z, ub_bp, True
        elif ub_z is None:
            rate_z, rate_bp, rate_long_end = zn_z, zn_bp, False
        else:
            mask_ub = ub_z.abs() > zn_z.abs()
            rate_z = ub_z.where(mask_ub, zn_z)
            rate_bp = ub_bp.where(mask_ub, zn_bp)
            rate_long_end = bool(mask_ub.iloc[-1]) if mask_ub.notna().any() else False
    rate_z = rate_z if rate_z is not None else pd.Series(np.nan, index=idx)
    rate_bp = rate_bp if rate_bp is not None else pd.Series(np.nan, index=idx)

    # --- 四条冲击腿判定 ---
    rate_hit = rate_z.abs() >= z_exam
    dxy_hit = dxy_z.abs() >= z_exam if dxy_z is not None else pd.Series(False, index=idx)
    cred_hit = (-hyg_z) >= z_exam if hyg_z is not None else pd.Series(False, index=idx)
    vol_hit = vix_z >= z_exam if vix_z is not None else pd.Series(False, index=idx)

    rate_code, rate_q = _grade(rate_hit, rate_z, eq_dmg_z, q_strain, q_fail)
    dxy_code, dxy_q = _grade(dxy_hit, dxy_z if dxy_z is not None else pd.Series(np.nan, index=idx),
                             eq_dmg_z, q_strain, q_fail)
    cred_code, cred_q = _grade(cred_hit, hyg_z if hyg_z is not None else pd.Series(np.nan, index=idx),
                               eq_dmg_z, q_strain, q_fail)
    vol_code, vol_q = _grade(vol_hit, vix_z if vix_z is not None else pd.Series(np.nan, index=idx),
                             eq_dmg_z, q_strain, q_fail)

    # --- 当日结论（取四条腿里最差） ---
    day_code = pd.concat([rate_code, dxy_code, cred_code, vol_code], axis=1).max(axis=1)
    hit_today = day_code >= 1
    fail_today = day_code == 3
    strain_today = day_code == 2
    big_hit_today = (
        (rate_hit & (rate_z.abs() >= z_big))
        | (dxy_hit & (dxy_z.abs() >= z_big))
        | (cred_hit & (-hyg_z >= z_big))
        | (vol_hit & (vix_z >= z_big))
    )
    big_fail_today = fail_today & big_hit_today

    # --- 20日滚动记录 ---
    hit20 = hit_today.rolling(20, min_periods=1).sum()
    fail20 = fail_today.rolling(20, min_periods=1).sum()
    strain20 = strain_today.rolling(20, min_periods=1).sum()
    rate_fail20 = (rate_code == 3).rolling(20, min_periods=1).sum()
    dxy_fail20 = (dxy_code == 3).rolling(20, min_periods=1).sum()
    cred_fail20 = (cred_code == 3).rolling(20, min_periods=1).sum()
    vol_fail20 = (vol_code == 3).rolling(20, min_periods=1).sum()

    # --- 状态机(迟滞): 逐根迭代, 依赖前一根 fragLevel ---
    frag = np.zeros(len(idx), dtype=int)
    for i in range(len(idx)):
        f20 = fail20.iloc[i]
        prev = int(frag[i - 1]) if i > 0 else 0
        if pd.isna(f20):
            lvl = 0
        elif f20 >= enter2:
            lvl = 2
        elif prev == 2 and f20 >= enter2 - 1:
            lvl = 2
        elif f20 >= enter1:
            lvl = 1
        elif prev >= 1 and f20 >= enter1 - 1:
            lvl = 1
        else:
            lvl = 0
        frag[i] = lvl
    frag_level = pd.Series(frag, index=idx)

    # --- 指纹 ---
    margin_fp = (
        (gc_z <= -1.0) & (dxy_z >= 0.5) & (eq_dmg_z >= 1.0)
    ) if (gc_z is not None and dxy_z is not None) else pd.Series(False, index=idx)
    sell_america = (
        rate_hit & (rate_z > 0) & dxy_hit & (dxy_z < 0) & (eq_dmg_z >= 1.0)
    )
    btc_canary = (
        (btc_z <= -1.0) & (eq_dmg_z < 0.5)
    ) if btc_z is not None else pd.Series(False, index=idx)

    # --- 冲击评分 0-100（仅主观数值评比，不定义状态） ---
    legs_reg = (
        rate_hit.astype(int) + dxy_hit.astype(int)
        + cred_hit.astype(int) + vol_hit.astype(int)
    )
    # maxAbsZ: 各命中腿的 |signed z|（信用腿取 -hygZ）
    max_abs_z = pd.Series(np.nan, index=idx)
    if rate_hit.any():
        max_abs_z = rate_z.abs().where(rate_hit)
    if dxy_hit.any():
        cand = dxy_z.abs().where(dxy_hit)
        max_abs_z = pd.concat([max_abs_z, cand], axis=1).max(axis=1)
    if cred_hit.any():
        cand = (-hyg_z).where(cred_hit)
        max_abs_z = pd.concat([max_abs_z, cand], axis=1).max(axis=1)
    if vol_hit.any():
        cand = vix_z.where(vol_hit)
        max_abs_z = pd.concat([max_abs_z, cand], axis=1).max(axis=1)

    q_worst = pd.Series(np.nan, index=idx)
    for q in (rate_q, dxy_q, cred_q, vol_q):
        if q is not None:
            q_worst = pd.concat([q_worst, q], axis=1).max(axis=1)

    pts_strength = (max_abs_z.clip(upper=2.5) / 2.5 * 40.0).fillna(0.0)
    pts_quality = (q_worst.clip(lower=0, upper=1.5) / 1.5 * 40.0).fillna(0.0)
    pts_breadth = (legs_reg - 1).clip(lower=0) * 7.0
    pts_breadth = pts_breadth.clip(upper=14.0)
    pts_side = margin_fp.astype(float) * 5.0 + sell_america.astype(float) * 5.0
    raw_score = pts_strength + pts_quality + pts_breadth + pts_side
    shock_score = (hit_today * raw_score.clip(upper=100.0)).round()

    # --- as-of 锚定：核心腿齐备的最后一根（与 Pine "图表最后日K" 一致）---
    # 避坑：union 最大日期会被 FX/BTC 等 24h 品种提前推到一根"其余品种
    # 收盘=昨收"的空 bar，导致 pct_change 全 0。Pine 的图是 QQQ 日线，
    # 最后一根必然是美股交易日（利率/美元/信用/波动/股指全部有数据）。
    # 故锚定到这些核心腿全部非 NaN 的最后一行，否则状态机/评分读到空值。
    def _notna(s: Optional[pd.Series]) -> pd.Series:
        return s.notna() if s is not None else pd.Series(False, index=idx)

    core_mask = (
        _notna(rate_z) & _notna(dxy_z) & _notna(hyg_z) & _notna(vix_z)
        & (_notna(es_z) | _notna(nq_z))
    )
    valid_dates = core_mask[core_mask].index
    if len(valid_dates) == 0:
        logger.warning("no core-complete bar found; falling back to last row")
        i = -1
    else:
        i = int(idx.get_loc(valid_dates[-1]))

    date_str = idx[i].strftime("%Y-%m-%d")

    def _v(s: Optional[pd.Series], scale: float = 1.0) -> Optional[float]:
        if s is None:
            return None
        v = s.iloc[i]
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return round(float(v) * scale, 3)

    def _code_text(c: int) -> str:
        return {3: "破裂", 2: "冲击加剧", 1: "接受冲击", 0: "无冲击"}[c]

    rate_code_i = int(rate_code.iloc[i])
    dxy_code_i = int(dxy_code.iloc[i])
    cred_code_i = int(cred_code.iloc[i])
    vol_code_i = int(vol_code.iloc[i])
    day_code_i = int(day_code.iloc[i])
    frag_i = int(frag_level.iloc[i])

    # 主软肋：今天破裂的腿优先，否则 20日破裂最多的腿
    weak_link = "无"
    if rate_code_i == 3:
        weak_link = "利率"
    elif cred_code_i == 3:
        weak_link = "信用"
    elif dxy_code_i == 3:
        weak_link = "美元"
    elif vol_code_i == 3:
        weak_link = "波动"
    else:
        m = max(float(rate_fail20.iloc[i]), float(dxy_fail20.iloc[i]),
                float(cred_fail20.iloc[i]), float(vol_fail20.iloc[i]))
        if m > 0:
            if float(rate_fail20.iloc[i]) == m:
                weak_link = "利率(20日)"
            elif float(cred_fail20.iloc[i]) == m:
                weak_link = "信用(20日)"
            elif float(dxy_fail20.iloc[i]) == m:
                weak_link = "美元(20日)"
            else:
                weak_link = "波动(20日)"

    state_text = {2: "高度脆弱", 1: "脆弱累积", 0: "吸收正常"}[frag_i]
    do_text = {
        2: "降杠杆, 停止逢跌加仓, 等一次干净的接受冲击再谈修复",
        1: "新仓减半, 只留高确信, 盯紧信用腿和银行腿",
        0: "按各机指令正常执行, 冲击日照常守低吸纪律",
    }[frag_i]
    dont_text = {
        2: "别假设逢跌买入还管用",
        1: "别追高贝塔反弹, 别把单日反弹当修复",
        0: "别把单独一天的国债下跌过度解读",
    }[frag_i]

    payload = {
        "date": date_str,
        "as_of": dt.datetime.now().isoformat(timespec="seconds"),
        "script": "市场冲击消化能力评判 v2.2 (Python headless port)",
        "source": "yfinance daily (MOVE abstained: TV-only)",
        "params": {
            "lb": lb, "z_len": z_len, "vix_pct_len": vix_pct_len,
            "z_exam": z_exam, "z_big": z_big,
            "q_strain": q_strain, "q_fail": q_fail,
            "enter1": enter1, "enter2": enter2,
            "dur_zn": dur_zn, "dur_ub": dur_ub,
        },
        "fragility_level": frag_i,
        "fragility_state": state_text,
        "weak_link": weak_link,
        "day_code": day_code_i,
        "day_text": (
            f"{'强冲击 · ' if big_hit_today.iloc[i] else ''}{_code_text(day_code_i)}"
            + (f" · {int(shock_score.iloc[i])}分" if hit_today.iloc[i] else "")
            if hit_today.iloc[i] else "无冲击"
        ),
        "shock_score": int(shock_score.iloc[i]) if hit_today.iloc[i] else 0,
        "big_hit_today": bool(big_hit_today.iloc[i]),
        "big_fail_today": bool(big_fail_today.iloc[i]),
        "record_20d": {
            "hit": int(hit20.iloc[i]) if not pd.isna(hit20.iloc[i]) else None,
            "strain": int(strain20.iloc[i]) if not pd.isna(strain20.iloc[i]) else None,
            "fail": int(fail20.iloc[i]) if not pd.isna(fail20.iloc[i]) else None,
            "rate_fail": int(rate_fail20.iloc[i]) if not pd.isna(rate_fail20.iloc[i]) else None,
            "dxy_fail": int(dxy_fail20.iloc[i]) if not pd.isna(dxy_fail20.iloc[i]) else None,
            "cred_fail": int(cred_fail20.iloc[i]) if not pd.isna(cred_fail20.iloc[i]) else None,
            "vol_fail": int(vol_fail20.iloc[i]) if not pd.isna(vol_fail20.iloc[i]) else None,
        },
        "do": do_text,
        "dont": dont_text,
        "legs": {
            "rate": {
                "abstain": rate_abstain,
                "hit": bool(rate_hit.iloc[i]) if not rate_abstain else False,
                "code": rate_code_i,
                "code_text": _code_text(rate_code_i),
                "z": _v(rate_z),
                "bp": _v(rate_bp),
                "long_end": rate_long_end,
                "q": _v(rate_q),
                "dir": ("利率急升" if (not rate_abstain and rate_z.iloc[i] > 0)
                        else "利率急跌" if not rate_abstain else ""),
            },
            "dxy": {
                "abstain": dxy_z is None,
                "hit": bool(dxy_hit.iloc[i]) if dxy_z is not None else False,
                "code": dxy_code_i,
                "code_text": _code_text(dxy_code_i),
                "z": _v(dxy_z),
                "ret_pct": _v(dxy_ret, 100.0),
                "q": _v(dxy_q),
                "dir": ("美元急升" if (dxy_z is not None and dxy_z.iloc[i] > 0)
                        else "美元急跌" if dxy_z is not None else ""),
            },
            "credit": {
                "abstain": hyg_z is None,
                "hit": bool(cred_hit.iloc[i]) if hyg_z is not None else False,
                "code": cred_code_i,
                "code_text": _code_text(cred_code_i),
                "z": _v(hyg_z),
                "q": _v(cred_q),
                "qual_z": _v(qual_z),
            },
            "vol": {
                "abstain": vix_z is None,
                "hit": bool(vol_hit.iloc[i]) if vix_z is not None else False,
                "code": vol_code_i,
                "code_text": _code_text(vol_code_i),
                "z": _v(vix_z),
                "pct": _v(vix_pct),
                "q": _v(vol_q),
            },
        },
        "damage_gauge": {
            "eq_dmg_z": _v(eq_dmg_z),
            "es_z": _v(es_z),
            "nq_z": _v(nq_z),
        },
        "fingerprints": {
            "margin_call": bool(margin_fp.iloc[i]) if hasattr(margin_fp, "iloc") else False,
            "sell_america": bool(sell_america.iloc[i]),
            "btc_canary": bool(btc_canary.iloc[i]),
        },
        "aux": {
            "gc_z": _v(gc_z),
            "btc_z": _v(btc_z),
            "kre_z": _v(kre_z),
            "move_z": None,  # TV-only 弃权
        },
        "note": (
            "分母压力旁证：仅观察+报警，不进主状态机，不动合成预算。 "
            "门槛为初值未标定(SA-1)，当参考不当指令。 MOVE 缺数弃权。"
        ),
    }
    return payload


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def build_report(p: dict) -> str:
    L = []
    L.append(f"# 市场冲击消化能力评判 · 每日读数（Python Headless 复刻 · {p['date']}）\n")
    L.append(f"- **生成时间**: {p['as_of']}")
    L.append(f"- **脚本**: {p['script']}")
    L.append(f"- **数据**: {p['source']}")
    L.append("")
    L.append("## 总状态\n")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 大状态 | **{p['fragility_state']}** (level={p['fragility_level']}) |")
    L.append(f"| 今日结论 | {p['day_text']} |")
    L.append(f"| 主软肋 | {p['weak_link']} |")
    r = p["record_20d"]
    L.append(f"| 20日记录 | 冲击 {r['hit']} · 加剧 {r['strain']} · 破裂 {r['fail']} |")
    L.append(f"| 做 | {p['do']} |")
    L.append(f"| 别做 | {p['dont']} |")
    L.append("")

    L.append("## 四条冲击腿\n")
    L.append("| 腿 | 命中 | 判定 | z | 损伤比 q | 备注 |")
    L.append("|---|---|---|---|---|---|")
    rp = p["legs"]["rate"]
    L.append(f"| 利率 | {'是' if rp['hit'] else ('弃权' if rp['abstain'] else '否')} | "
             f"{rp['code_text']} | {rp['z']} | {rp['q']} | "
             f"{rp['dir']}{(' (' + str(rp['bp']) + 'bp ' + ('长端' if rp['long_end'] else '腹部') + ')') if rp['bp'] is not None else ''} |")
    dp = p["legs"]["dxy"]
    L.append(f"| 美元 | {'是' if dp['hit'] else ('弃权' if dp['abstain'] else '否')} | "
             f"{dp['code_text']} | {dp['z']} | {dp['q']} | {dp['dir']} |")
    cp = p["legs"]["credit"]
    L.append(f"| 信用 | {'是' if cp['hit'] else ('弃权' if cp['abstain'] else '否')} | "
             f"{cp['code_text']} | {cp['z']} | {cp['q']} | 质量差 z {cp['qual_z']} |")
    vp = p["legs"]["vol"]
    L.append(f"| 波动 | {'是' if vp['hit'] else ('弃权' if vp['abstain'] else '否')} | "
             f"{vp['code_text']} | {vp['z']} | {vp['q']} | 水位 {vp['pct']}分位 |")

    L.append("")
    L.append("## 损伤与旁证\n")
    dg = p["damage_gauge"]
    L.append(f"- **股指损伤**: 损伤 z {dg['eq_dmg_z']} (ES {dg['es_z']} / NQ {dg['nq_z']})")
    fp = p["fingerprints"]
    L.append(f"- **保证金抛售指纹**: {'出现' if fp['margin_call'] else '未出现'}")
    L.append(f"- **股债汇三杀**: {'出现' if fp['sell_america'] else '未出现'}")
    L.append(f"- **BTC金丝雀**: {'报警' if fp['btc_canary'] else '平静'}")
    ax = p["aux"]
    L.append(f"- **辅助**: 金 z {ax['gc_z']} · BTC z {ax['btc_z']} · KRE z {ax['kre_z']} · MOVE 弃权")
    L.append("")
    L.append("> ⚠️ 边界声明：本读数来自观察工具，非交易信号/投资建议。仅回答“市场今天有没有在吸收冲击”。")
    L.append("> 完整逻辑见 `scripts/shock_absorption_daily.py`；门槛初值未经标定(SA-1)。")
    return "\n".join(L) + "\n"


def write_outputs(report_dir: Path, p: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"shock_absorption_{p['date']}.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report_dir / f"shock_absorption_{p['date']}.md").write_text(
        build_report(p), encoding="utf-8")
    logger.info("written shock_absorption_%s.{md,json}", p["date"])


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Shock Absorption daily port (denominator cross-check)")
    ap.add_argument("--date", default=dt.date.today().isoformat(),
                    help="(kept for CLI symmetry; output uses latest aligned data date)")
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    ap.add_argument("--period", default="3y", help="yfinance history window")
    # --- Pine 输入映射（初值，未标定 SA-1） ---
    ap.add_argument("--lb", type=int, default=1, help="冲击观察窗(根)")
    ap.add_argument("--z-len", type=int, default=120, help="z分母窗口(日)")
    ap.add_argument("--vix-pct-len", type=int, default=252, help="波动水位分位窗口(日)")
    ap.add_argument("--z-exam", type=float, default=1.0, help="冲击认定线 z")
    ap.add_argument("--z-big", type=float, default=1.5, help="强冲击线 z")
    ap.add_argument("--q-strain", type=float, default=0.5, help="加剧线(损伤比)")
    ap.add_argument("--q-fail", type=float, default=1.0, help="破裂线(损伤比)")
    ap.add_argument("--enter1", type=int, default=3, help="脆弱累积进场(20日破裂次数)")
    ap.add_argument("--enter2", type=int, default=5, help="高度脆弱进场(20日破裂次数)")
    ap.add_argument("--dur-zn", type=float, default=6.4, help="ZN 久期")
    ap.add_argument("--dur-ub", type=float, default=22.0, help="UB 久期")
    args = ap.parse_args(argv)

    closes = fetch_closes(period=args.period)
    if not closes:
        logger.error("no data fetched; abort")
        return 1
    try:
        payload = compute_state(closes, args)
    except Exception as exc:  # noqa: BLE001
        logger.error("compute failed: %s", exc)
        return 1
    write_outputs(Path(args.report_dir), payload)
    try:
        print(build_report(payload))
    except Exception:
        pass
    print(f"\n[written] shock_absorption_{payload['date']}.json / .md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
