#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标普板块资金轮动观测机 — 本地日频复刻 (Pine v1.3m 会员版 忠实移植)

将挂在 TradingView 上的 Pine 指标 `全市场板块资金轮动观测机 v1.3m` 的打分引擎，
在本地用 yfinance 日线公开数据完整复刻，每日收盘后独立出一份读数 + 归因报告。
不与 TV 交互（TV 免费版延迟/无技术告警/沙箱无法改写库脚本），所有配方/阈值与
Pine 原版逐字一致：趋势/资金/量能/广度/不过热 五项证据，理论最高 107 截断到 100；
八条归因剧本（攻守对调/小盘接棒/等权追赶/利率驱动/通胀后周期/真避险/全场降风险/
月季末再平衡）；联动加分与原因引擎同权重同优先级。

输出：
  output/sector_rotation_<as_of>.md   (人类阅读)
  output/sector_rotation_<as_of>.json (机器解析)
  as_of = 标普最新可用交易日（盘后跑即上一美国交易日）

用法：
  python sector_rotation_daily.py
  python sector_rotation_daily.py --date 2026-08-10 --report-dir /abs/path
  python sector_rotation_daily.py --force-refresh
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

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sector-rotation")

DEFAULT_PROXY = "http://127.0.0.1:7890"
_PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY")
# 代理开关（向后兼容）：默认仍走本地代理 127.0.0.1:7890（用户本机行为不变）。
# 外部环境（如 grokbot 云机无本地代理）可设 SECTOR_PROXY 重定向或关闭：
#   SECTOR_PROXY=off|none|0|false|no|""  → 完全不设代理（依赖直连/环境自带代理）
#   SECTOR_PROXY=http://host:port         → 使用指定代理
# 若已设置系统变量 HTTPS_PROXY/HTTP_PROXY，则优先级最高、本函数不覆盖。
_PROXY_OFF_VALUES = ("", "off", "none", "0", "false", "no")

# ---- 版本与有效期（与 Pine 流水线盖戳区一致）----
VER_TXT = "会员版 v1.3m"
EXP_TXT = "2026-11-07"
EXPIRY = dt.date(2026, 11, 7)

# ---- 引擎固定参数（与内部版完全一致）----
TREND_LEN = 20
SLOW_LEN = 50
VOL_LEN = 20
VOL_CONFIRM = 1.30
CROWD_REL60 = 12.0
CROWD_DIST50 = 6.0
BENCH_CROWD60 = 12.0
BENCH_CROWD_DIST = 6.0
HISTORY_BARS = 400  # 取足 350+ 以便所有 lookback（rel60/ma50/accel）稳定

# ---- 旁路设施（不参与 Pine 打分，主分数逐位不变）----
# 本地行情缓存：加速 + 网络不可用时降级出报告
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "sector_price_cache"
CACHE_MAX_BARS = 800          # 每标的缓存保留的最大条数
FETCH_ATTEMPTS = 2            # 单标的拉取尝试次数（yfinance 偶发抖动）
# 诊断：连续广度影子分（仅对照，不进入主分数/状态判定）
BREADTH_CONT_MAX = 25.0
# 诊断：广度-流动背离告警阈值
DIVERGE_FLOW_MIN = 26.0       # 资金流分量达到高档
DIVERGE_BREADTH_MAX = 50.0    # 但真广度低于此值 → 指数由少数龙头拉动

# 报告必需标的：缺任一即无法出完整读数（含归因剧本的确认腿）
REQUIRED_KEYS = ("spy", "xlk", "xlv", "tlt", "hyg", "gld", "kre")

# ---- 标的映射：TV ticker -> yfinance code -> 中文名 ----
TICKERS: Dict[str, str] = {
    "spy": "SPY", "xlk": "XLK", "xlf": "XLF", "xlv": "XLV", "xly": "XLY",
    "xlp": "XLP", "xle": "XLE", "xli": "XLI", "xlb": "XLB", "xlu": "XLU",
    "xlre": "XLRE", "xlc": "XLC", "iwm": "IWM", "rsp": "RSP", "qqq": "QQQ",
    "tlt": "TLT", "hyg": "HYG", "gld": "GLD", "kre": "KRE",
}
LABELS: Dict[str, str] = {
    "spy": "标普(SPY)", "xlk": "科技", "xlf": "金融", "xlv": "医疗保健", "xly": "可选消费",
    "xlp": "必需消费", "xle": "能源", "xli": "工业", "xlb": "材料", "xlu": "公用事业",
    "xlre": "房地产", "xlc": "通信服务", "iwm": "小盘", "rsp": "等权", "qqq": "大科技",
    "tlt": "长债(TLT)", "hyg": "高收益债(HYG)", "gld": "黄金(GLD)", "kre": "区域银行(KRE)",
}

# 14 主题行顺序（id 与 Pine themeReason 一致：1科技..14大科技）
THEME_ORDER: List[Tuple[int, str, str]] = [
    (1, "xlk", "科技"), (2, "xlf", "金融"), (3, "xlv", "医疗保健"), (4, "xly", "可选消费"),
    (5, "xlp", "必需消费"), (6, "xle", "能源"), (7, "xli", "工业"), (8, "xlb", "材料"),
    (9, "xlu", "公用事业"), (10, "xlre", "房地产"), (11, "xlc", "通信服务"),
    (12, "iwm", "小盘"), (13, "rsp", "等权"), (14, "qqq", "大科技"),
]
# 11 个板块（广度/计数只用这 11 个，不含小盘/等权/大科技）
SECTOR_KEYS = ["xlk", "xlf", "xlv", "xly", "xlp", "xle", "xli", "xlb", "xlu", "xlre", "xlc"]


# ===================== 阶段二：代理注入 =====================
def _ensure_proxy() -> None:
    # 已显式设置系统代理 → 直接采用，不覆盖
    if any(os.environ.get(k) for k in _PROXY_KEYS):
        return
    # SECTOR_PROXY 开关：显式关闭/置空则不设代理（供无本地代理环境，如 grokbot 云机）
    sp = os.environ.get("SECTOR_PROXY")
    if sp is not None and str(sp).strip().lower() in _PROXY_OFF_VALUES:
        return
    try:
        proxy = sp if (sp and str(sp).strip()) else DEFAULT_PROXY
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
    except Exception:
        pass


# ===================== 阶段二：数据获取 =====================
def _download_close_vol(code: str, period: str = "2y",
                        attempts: int = FETCH_ATTEMPTS) -> Optional[Tuple[pd.Series, pd.Series]]:
    """拉取单标的日线。yfinance 偶发失败（"possibly delisted" 误报 / 静默空数据），
    因此默认重试一次；全部失败返回 None，由调用方决定降级或报错。"""
    import yfinance as yf
    _ensure_proxy()
    raw = None
    for i in range(max(1, attempts)):
        try:
            raw = yf.download(code, period=period, auto_adjust=True, progress=False, threads=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("download %s failed (attempt %d/%d): %s", code, i + 1, attempts, exc)
            raw = None
        if raw is not None and not getattr(raw, "empty", True):
            break
        if i + 1 < attempts:
            logger.warning("download %s returned empty (attempt %d/%d), retrying", code, i + 1, attempts)
            raw = None
    if raw is None or getattr(raw, "empty", True):
        return None
    cols = raw.columns

    def _field(field: str):
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


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.csv"


def _cache_read(key: str) -> Optional[pd.DataFrame]:
    """读取本地缓存；返回 Date 索引、含 Close/Volume 的 DataFrame，不可用返回 None。"""
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, parse_dates=["Date"])
        if df.empty or "Close" not in df.columns:
            return None
        df = df.set_index("Date").sort_index()
        if "Volume" not in df.columns:
            df["Volume"] = np.nan
        return df[["Close", "Volume"]].astype(float)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache read %s failed: %s", key, exc)
        return None


def _cache_write(key: str, close: pd.Series, vol: pd.Series) -> None:
    """回写缓存（原子替换）。缓存失败不影响主流程，仅告警。"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"Close": pd.to_numeric(close, errors="coerce"),
                           "Volume": pd.to_numeric(vol, errors="coerce")})
        df = df[df["Close"].notna()].sort_index().tail(CACHE_MAX_BARS)
        if df.empty:
            return
        df.index.name = "Date"
        target = _cache_path(key)
        tmp = target.with_suffix(".csv.tmp")
        df.to_csv(tmp, encoding="utf-8")
        tmp.replace(target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache write %s failed: %s", key, exc)


def load_data(force_refresh: bool = False) -> Tuple[Dict[str, Dict[str, pd.Series]], List[str], List[str]]:
    """返回 (data, missing_keys, from_cache_keys)。

    - 每个标的先联网拉取（单次失败自动重试）；成功后回写本地缓存。
    - 拉取失败则回退本地缓存并计入 from_cache_keys；缓存也不可用才计入 missing_keys。
    - 缓存仅用于加速与断网降级；联网成功时数据完全来自网络，读数不受缓存影响。
    """
    out: Dict[str, Dict[str, pd.Series]] = {}
    missing: List[str] = []
    from_cache: List[str] = []
    for key, code in TICKERS.items():
        res = None if force_refresh else _download_close_vol(code)
        if res is None:
            cached = _cache_read(key)
            if cached is not None and len(cached) >= 60:
                out[key] = {"close": cached["Close"], "vol": cached["Volume"]}
                from_cache.append(key)
                logger.warning("network failed for %s (%s) — falling back to cache (%d bars, last %s)",
                               key, code, len(cached), cached.index[-1].date())
            else:
                missing.append(key)
                logger.error("no data for %s (%s): network failed and no usable cache", key, code)
            continue
        close, vol = res
        _cache_write(key, close, vol)
        out[key] = {"close": close, "vol": vol}
        logger.info("fetched %s (%s): %d bars", key, code, len(close))
    return out, missing, from_cache


# ===================== 阶段三：向量化指标 =====================
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def pct_change_n(s: pd.Series, n: int) -> pd.Series:
    x = s.shift(n)
    out = (s - x) / x * 100.0
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


def ret_one(s: pd.Series) -> pd.Series:
    x = s.shift(1)
    out = (s - x) / x
    return out.replace([np.inf, -np.inf], np.nan)


def safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.divide(b).where(b != 0.0)


def cap100(x) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan
    return float(min(100.0, max(0.0, x)))


def breadth_etf(c: pd.Series) -> pd.Series:
    m = sma(c, TREND_LEN)
    out = pd.Series(np.where((c > m).fillna(False), 70.0, 30.0), index=c.index)
    out = out.where(~(c.isna() | m.isna()))
    return out


def rel_accel(rel5, rel20):
    return rel5 - rel20 / 4.0


def pos_part(x):
    return x if x is None or np.isnan(x) else max(x, 0.0)


def neg_part(x):
    return x if x is None or np.isnan(x) else max(-x, 0.0)


def score_accel(x, scale: float, max_score: float) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0.0
    return float(min(max_score, max(0.0, x / scale * max_score)))


def avg_ret5(closes: List[Optional[pd.Series]]):
    rets = [ret_one(c) for c in closes if c is not None]
    if not rets:
        idx = None
        for c in closes:
            if c is not None:
                idx = c.index
                break
        empty = pd.Series(dtype=float, index=idx) if idx is not None else pd.Series(dtype=float)
        return empty, empty
    df = pd.concat(rets, axis=1)
    return df.mean(axis=1, skipna=True), df.notna().sum(axis=1)


def chain_index(ret: pd.Series) -> pd.Series:
    """Pine: idx := na(idx[1]) ? 100 : na(ret) ? idx[1] : idx[1]*(1+ret)"""
    cum = pd.Series(index=ret.index, dtype=float)
    prev = 100.0
    arr = ret.to_numpy()
    idx = ret.index
    for i in range(len(arr)):
        v = arr[i]
        if v is None or (isinstance(v, float) and np.isnan(v)):
            cum.iloc[i] = prev
        else:
            prev = prev * (1.0 + float(v))
            cum.iloc[i] = prev
    return cum


# ===================== 阶段三：打分引擎（全序列）=====================
def calc_metrics_full(idx: pd.Series, dv: pd.Series, c_spy: pd.Series):
    """返回所有派生量的完整 Series（仅末值会用于最终读数）。"""
    # Align to idx so np.where/Series(index=...) cannot see 401 vs 400 when
    # yfinance/cache bars differ by one session across close vs volume.
    dv = dv.reindex(idx.index)
    c_spy = c_spy.reindex(idx.index)
    ratio = safe_div(idx, c_spy)
    rel1 = pct_change_n(ratio, 1)
    rel5 = pct_change_n(ratio, 5)
    rel20 = pct_change_n(ratio, 20)
    rel60 = pct_change_n(ratio, 60)
    abs5 = pct_change_n(idx, 5)
    ma20 = sma(ratio, TREND_LEN)
    ma50 = sma(ratio, SLOW_LEN)
    dv_fix = dv.ffill()
    vol_r = safe_div(dv_fix, sma(dv_fix, VOL_LEN))
    dist50 = safe_div(ratio - ma50, ma50) * 100.0

    valid = ratio.notna() & c_spy.notna()
    down_day = idx < idx.shift(1)

    trend_score = (ratio > ma20).astype(float) * 10.0 + (ratio > ma50).astype(float) * 10.0 + (ma20 > ma20.shift(5)).astype(float) * 10.0
    flow_score = (rel5 > 0).astype(float) * 10.0 + (rel20 > 0).astype(float) * 8.0 + (rel5 > rel5.shift(5)).astype(float) * 8.0 + (rel5 > rel20 / 4.0).astype(float) * 8.0

    rel1_pos = (rel1 > 0).fillna(False)
    vol_score = pd.Series(0.0, index=ratio.index)
    vol_score = vol_score.mask(rel1_pos & (vol_r >= 1.0).fillna(False), 10.0)
    vol_score = vol_score.mask(rel1_pos & (vol_r >= VOL_CONFIRM).fillna(False), 20.0)

    # 广度：ETF 价 vs 自身20均（70/30 粗占位，与 Pine 一致）
    br = breadth_etf(idx).reindex(ratio.index)
    br_score = pd.Series(0.0, index=ratio.index)
    br_score = br_score.mask((br >= 50.0).fillna(False), 8.0)
    br_score = br_score.mask((br >= 70.0).fillna(False), 15.0)

    over_ext = (rel60 > CROWD_REL60).fillna(False) | (dist50 > CROWD_DIST50).fillna(False)
    crowd_score = pd.Series(8.0, index=ratio.index).mask(over_ext.fillna(False), 0.0)

    raw = trend_score + flow_score + vol_score + br_score + crowd_score
    score = raw.clip(0, 100).where(valid)

    distribution = valid & (ratio < ma20) & (rel5 < 0) & (vol_r > VOL_CONFIRM) & (rel1 < 0)
    beta_lift = valid & (abs5 > 0) & (rel5 < 0)

    state = np.select(
        [~valid, distribution, over_ext & (score >= 60), score >= 75, score >= 60,
         (score < 45) & (rel5 < 0) & (rel20 < 0)],
        [9, -2, 3, 2, 1, -1], default=0,
    ).astype(int)

    trend_code = np.select(
        [(ratio > ma20) & (ratio > ma50), ratio > ma20, (ratio < ma20) & (ratio < ma50), ratio < ma20],
        [2, 1, -2, -1], default=0,
    ).astype(int)

    return {
        "ratio": ratio, "rel1": rel1, "rel5": rel5, "rel20": rel20, "rel60": rel60,
        "abs5": abs5, "ma20": ma20, "ma50": ma50, "vol_r": vol_r, "dist50": dist50,
        "breadth": br, "score": score, "state": pd.Series(state, index=ratio.index),
        "trend_code": pd.Series(trend_code, index=ratio.index),
        "over_ext": over_ext, "distribution": distribution, "beta_lift": beta_lift,
    }


def calc_bench_full(px: pd.Series, dv: pd.Series, br_pct: float, c_spy: Optional[pd.Series] = None):
    # Align volume to price index (close vs vol HISTORY_BARS tails can diverge).
    dv = dv.reindex(px.index)
    a1 = pct_change_n(px, 1)
    a5 = pct_change_n(px, 5)
    a20 = pct_change_n(px, 20)
    a60 = pct_change_n(px, 60)
    m20 = sma(px, TREND_LEN)
    m50 = sma(px, SLOW_LEN)
    dv_fix = dv.ffill()
    v_r = safe_div(dv_fix, sma(dv_fix, VOL_LEN))
    d50 = safe_div(px - m50, m50) * 100.0

    valid = px.notna()
    down_day = px < px.shift(1)

    trend_score = (px > m20).astype(float) * 10.0 + (px > m50).astype(float) * 10.0 + (m20 > m20.shift(5)).astype(float) * 10.0
    flow_score = (a5 > 0).astype(float) * 10.0 + (a20 > 0).astype(float) * 8.0 + (a5 > a5.shift(5)).astype(float) * 8.0 + (a5 > a20 / 4.0).astype(float) * 8.0
    a1_pos = (a1 > 0).fillna(False)
    vol_score = pd.Series(0.0, index=px.index)
    vol_score = vol_score.mask(a1_pos & (v_r >= 1.0).fillna(False), 10.0)
    vol_score = vol_score.mask(a1_pos & (v_r >= VOL_CONFIRM).fillna(False), 20.0)
    br_score = 15.0 if (not np.isnan(br_pct) and br_pct >= 70.0) else (8.0 if (not np.isnan(br_pct) and br_pct >= 50.0) else 0.0)
    br_score = pd.Series(br_score, index=px.index)

    over_ext = (a60 > BENCH_CROWD60).fillna(False) | (d50 > BENCH_CROWD_DIST).fillna(False)
    crowd_score = pd.Series(8.0, index=px.index).mask(over_ext.fillna(False), 0.0)

    raw = trend_score + flow_score + vol_score + br_score + crowd_score
    score = raw.clip(0, 100).where(valid)

    distribution = valid & (px < m20) & (a5 < 0) & (v_r > VOL_CONFIRM) & (a1 < 0)
    state = np.select(
        [~valid, distribution, over_ext & (score >= 60), score >= 75, score >= 60,
         (score < 45) & (a5 < 0) & (a20 < 0)],
        [9, -2, 3, 2, 1, -1], default=0,
    ).astype(int)
    trend_code = np.select(
        [(px > m20) & (px > m50), px > m20, (px < m20) & (px < m50), px < m20],
        [2, 1, -2, -1], default=0,
    ).astype(int)

    # ---- 旁路诊断（不参与上文的 score / state，仅供报告展示与对照）----
    br_cont = pd.Series(
        float(np.clip((br_pct / 100.0) * BREADTH_CONT_MAX, 0.0, BREADTH_CONT_MAX))
        if (br_pct is not None and not np.isnan(br_pct)) else 0.0, index=px.index)
    crowd_s = crowd_score  # already aligned Series
    score_cont = (trend_score + flow_score + vol_score + br_cont + crowd_s).clip(0, 100).where(valid)

    return {
        "a1": a1, "a5": a5, "a20": a20, "a60": a60, "vol_r": v_r, "dist50": d50,
        "score": score, "state": pd.Series(state, index=px.index),
        "trend_code": pd.Series(trend_code, index=px.index), "over_ext": over_ext,
        "distribution": distribution,
        # 诊断：五项分量分解 + 连续广度影子分
        "components": {"trend": trend_score, "flow": flow_score, "vol": vol_score,
                       "breadth": br_score, "crowd": crowd_s},
        "score_cont": score_cont, "breadth_cont_score": br_cont,
    }


# ===================== 文本映射 =====================
def fmt_pct(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "na"
    return f"{x:.2f}%"


def fmt_num(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "na"
    return f"{x:.2f}"


def fmt_score(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "na"
    return f"{x:.0f}"


def trend_text(code: int) -> str:
    return {2: "强", 1: "偏强", -1: "偏弱", -2: "弱"}.get(code, "中性")


def state_text(state: int) -> str:
    return {3: "拥挤主升", 2: "确认进入", 1: "早期轮动", 0: "中性观察",
            -1: "资金撤出", -2: "派发/撤出", 9: "无数据"}.get(state, "中性")


def bench_state_text(state: int) -> str:
    return {3: "拥挤主升", 2: "确认走强", 1: "早期转强", 0: "中性观察",
            -1: "资金离场", -2: "派发/离场", 9: "无数据"}.get(state, "中性")


def reason_text(code: int) -> str:
    return {1: "主动流入", 2: "早期轮动", 3: "攻守对调受益", 4: "利率驱动", 5: "通胀后周期",
            6: "拥挤主升", 8: "派发撤出", 9: "跟涨假强", 10: "抗跌观察", 11: "全场降风险",
            12: "月/季再平衡", 13: "小盘接棒", 14: "等权追赶", 15: "双负转弱", 16: "龙头抬轿",
            17: "普涨走强", 18: "动能转强", 99: "无数据"}.get(code, "中性")


STATE_EMOJI = {3: "🟠", 2: "🟢", 1: "🔵", 0: "⚪", -1: "🔴", -2: "🔴", 9: "⚫"}


# ===================== 阶段四：终态与归因 =====================
def compute_state_from_score(score, over_ext, distribution, rel5, rel20) -> int:
    if score is None or np.isnan(score):
        return 9
    if distribution:
        return -2
    if over_ext and score >= 60:
        return 3
    if score >= 75:
        return 2
    if score >= 60:
        return 1
    if score < 45 and (rel5 is not None and not np.isnan(rel5) and rel5 < 0) and (rel20 is not None and not np.isnan(rel20) and rel20 < 0):
        return -1
    return 0


def theme_reason(idv, state, score, rel5, rel20, abs5, beta_lift,
                 de_risk_s, flip_s, flip_to_def,
                 small_s, broad_s, rate_s, rate_dn, rate_up,
                 infl_s, rebal_s) -> int:
    if state == 9:
        return 99
    if state == -2:
        return 8
    if beta_lift and (score is not None and not np.isnan(score) and score < 60):
        return 9
    if de_risk_s >= 70 and (abs5 is not None and not np.isnan(abs5) and abs5 < 0) and (rel5 is not None and not np.isnan(rel5) and rel5 < 0):
        return 11
    if flip_s >= 60 and flip_to_def and idv in (3, 5, 9) and (rel5 is not None and not np.isnan(rel5) and rel5 > 0):
        return 3
    if flip_s >= 60 and (not flip_to_def) and idv in (1, 2, 4) and (rel5 is not None and not np.isnan(rel5) and rel5 > 0):
        return 3
    if idv == 12 and small_s >= 60:
        return 13
    if idv == 13 and broad_s >= 60:
        return 14
    if rate_s >= 60 and rate_dn and idv in (9, 10):
        return 4
    if rate_s >= 60 and rate_up and idv == 2:
        return 4
    if infl_s >= 60 and idv in (6, 8) and (score is not None and not np.isnan(score) and score >= 60):
        return 5
    if rebal_s >= 65 and state <= 1:
        return 12
    if state == 3:
        return 6
    if score is not None and not np.isnan(score) and score >= 75:
        return 1
    if score is not None and not np.isnan(score) and score >= 60:
        return 2
    if (abs5 is not None and not np.isnan(abs5) and abs5 < 0) and (rel5 is not None and not np.isnan(rel5) and rel5 > 0):
        return 10
    if (rel5 is not None and not np.isnan(rel5) and rel5 < 0) and (rel20 is not None and not np.isnan(rel20) and rel20 < 0):
        return 15
    return 0


def bench_reason(state, abs5, breadth, score, abs20) -> int:
    if state == 9:
        return 99
    if state == -2:
        return 8
    if state == 3:
        return 6
    if (abs5 is not None and not np.isnan(abs5) and abs5 > 0) and (breadth is not None and not np.isnan(breadth) and breadth < 50):
        return 16
    if (score is not None and not np.isnan(score) and score >= 75) and (breadth is not None and not np.isnan(breadth) and breadth >= 70):
        return 17
    if score is not None and not np.isnan(score) and score >= 75:
        return 1
    if score is not None and not np.isnan(score) and score >= 60:
        return 18
    if (abs5 is not None and not np.isnan(abs5) and abs5 < 0) and (abs20 is not None and not np.isnan(abs20) and abs20 < 0):
        return 15
    return 0


def leg_txt(v, up, dn) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "缺数"
    return up if v > 0 else dn


# ===================== 主流程 =====================
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="标普板块轮动 日频复刻")
    parser.add_argument("--date", default=None, help="指定 as_of（YYYY-MM-DD），默认用最新交易日")
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args(argv)

    _ensure_proxy()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    ROOT = Path(__file__).resolve().parents[2]
    report_dir = Path(args.report_dir) if args.report_dir else ROOT / "output"
    report_dir.mkdir(parents=True, exist_ok=True)

    data, missing_keys, from_cache = load_data(force_refresh=args.force_refresh)
    if missing_keys or "spy" not in data or len(data) < 5:
        if missing_keys:
            logger.error("以下标的拉取失败（已重试 %d 次，且无可用本地缓存）：%s",
                         FETCH_ATTEMPTS, ", ".join(f"{k}={TICKERS[k]}" for k in missing_keys))
        if "spy" not in data:
            logger.error("基准 SPY 无数据。")
        logger.error("数据不完整，无法出读数。请检查代理(%s)/网络后重跑。"
                     "联网失败时会自动回退本地缓存(%s)，缓存也缺失才会报此错。",
                     DEFAULT_PROXY, CACHE_DIR)
        return 2
    if from_cache:
        logger.warning("以下标的来自本地缓存（非实时）：%s", ", ".join(from_cache))

    # 对齐到公共日期轴；先取 close∩vol 公共 index 再 tail，避免 close/vol 401 vs 400 错位
    df_close = pd.DataFrame({k: v["close"] for k, v in data.items()})
    df_vol = pd.DataFrame({k: v["vol"] for k, v in data.items()})
    df_close = df_close.ffill().bfill()
    df_vol = df_vol.ffill().fillna(0.0)
    common = df_close.index.intersection(df_vol.index)
    if len(common) == 0:
        logger.error("close 与 volume 无公共交易日，无法对齐 HISTORY_BARS（拒绝写出错误读数）。")
        return 2
    df_close = df_close.loc[common].tail(HISTORY_BARS)
    df_vol = df_vol.reindex(df_close.index).ffill().fillna(0.0)

    # as_of = 最新交易日
    as_of = df_close.index[-1].date()
    if args.date:
        try:
            as_of = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        except Exception:
            logger.warning("无效 --date，回退到最新交易日 %s", as_of)

    # 取 as_of 位置的末行索引
    if as_of not in set(d.date() for d in df_close.index):
        logger.warning("指定 as_of=%s 无数据，使用最新 %s", as_of, df_close.index[-1].date())
        as_of = df_close.index[-1].date()
    else:
        # 历史回看：截断到 as_of，末行即该交易日（rolling 仍保留其前的历史）
        if args.date:
            ts = pd.Timestamp(as_of)
            df_close = df_close[df_close.index <= ts]
            df_vol = df_vol[df_vol.index <= ts]

    c_spy = df_close["spy"]

    stale_days = (dt.date.today() - as_of).days
    if stale_days > 4:
        logger.warning("数据偏旧：最新交易日 %s 距今天 %d 天（可能遇休市或数据未更新）", as_of, stale_days)
    # 合成组指数
    off_ret, _ = avg_ret5([df_close.get("xlk"), df_close.get("xly"), df_close.get("xlf")])
    def_ret, _ = avg_ret5([df_close.get("xlp"), df_close.get("xlu"), df_close.get("xlv")])
    emb_ret, _ = avg_ret5([df_close.get("xle"), df_close.get("xlb")])
    off_idx = chain_index(off_ret)
    def_idx = chain_index(def_ret)
    emb_idx = chain_index(emb_ret)

    # 各主题打分（完整序列）
    metrics: Dict[str, dict] = {}
    for key in TICKERS:
        if key == "spy":
            continue
        idx = df_close[key]
        dv = df_vol[key] * idx  # 成交额（金额） = 价 * 量
        m = calc_metrics_full(idx, dv, c_spy)
        m["base_score"] = m["score"].iloc[-1]
        metrics[key] = m

    # 基准行：广度 = 11 板块站上自身20均占比（真广度）
    br_vals = []
    for k in SECTOR_KEYS:
        b = metrics[k]["breadth"].iloc[-1]
        br_vals.append(70.0 if (not np.isnan(b) and b >= 70.0) else 0.0)
    bench_breadth = 100.0 * sum(1 for v in br_vals if v >= 70.0) / len(SECTOR_KEYS)
    spy_dv = df_vol["spy"] * c_spy
    bench = calc_bench_full(c_spy, spy_dv, bench_breadth)

    # ---- 旁路诊断：基准分数分量分解 + 广度-流动背离（不参与主分数）----
    def _comp_last(series):
        v = series.iloc[-1] if series is not None else np.nan
        return None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(float(v), 2)

    bench_components = {k: _comp_last(v) for k, v in bench["components"].items()}
    bench_score_cont = _comp_last(bench["score_cont"])
    _flow_v = bench_components.get("flow") or 0.0
    # 资金流打到高档、但真广度已跌破 50% → 指数由少数龙头拉动，提防补跌
    bench_divergence = bool(_flow_v >= DIVERGE_FLOW_MIN
                            and not (isinstance(bench_breadth, float) and np.isnan(bench_breadth))
                            and bench_breadth < DIVERGE_BREADTH_MAX)

    # ---- 合成组比价与确认腿（标量末值）----
    def last_of(s):
        return s.iloc[-1] if s is not None else np.nan

    off_ratio = safe_div(off_idx, c_spy)
    off_c5, off_c20 = last_of(pct_change_n(off_ratio, 5)), last_of(pct_change_n(off_ratio, 20))
    off_c_accel = rel_accel(off_c5, off_c20)
    def_ratio = safe_div(def_idx, c_spy)
    def_c5, def_c20 = last_of(pct_change_n(def_ratio, 5)), last_of(pct_change_n(def_ratio, 20))
    def_c_accel = rel_accel(def_c5, def_c20)
    def_off_ratio = safe_div(def_idx, off_idx)
    def_off5 = last_of(pct_change_n(def_off_ratio, 5))
    def_off_accel = rel_accel(def_off5, last_of(pct_change_n(def_off_ratio, 20)))
    off_def_ratio = safe_div(off_idx, def_idx)
    off_def5 = last_of(pct_change_n(off_def_ratio, 5))
    off_def_accel = rel_accel(off_def5, last_of(pct_change_n(off_def_ratio, 20)))
    emb_tech_ratio = safe_div(emb_idx, df_close["xlk"])
    emb_tech5 = last_of(pct_change_n(emb_tech_ratio, 5))
    emb_tech_accel = rel_accel(emb_tech5, last_of(pct_change_n(emb_tech_ratio, 20)))

    leg_tlt5 = last_of(pct_change_n(df_close["tlt"], 5))
    leg_gld5 = last_of(pct_change_n(df_close["gld"], 5))
    leg_hyg_rel5 = last_of(pct_change_n(safe_div(df_close["hyg"], c_spy), 5))
    leg_kre_rel5 = last_of(pct_change_n(safe_div(df_close["kre"], c_spy), 5))

    # 各主题标量
    def sval(key, field):
        return last_of(metrics[key][field])

    theme_scalars: Dict[str, dict] = {}
    for key in TICKERS:
        if key == "spy":
            continue
        rel5 = sval(key, "rel5")
        rel20 = sval(key, "rel20")
        rel1 = sval(key, "rel1")
        abs5 = sval(key, "abs5")
        vol_r = sval(key, "vol_r")
        breadth = sval(key, "breadth")
        base_score = sval(key, "score")
        over_ext = bool(sval(key, "over_ext"))
        distribution = bool(sval(key, "distribution"))
        beta_lift = bool(sval(key, "beta_lift"))
        trend_code = int(sval(key, "trend_code"))
        accel = rel_accel(rel5, rel20)
        theme_scalars[key] = dict(rel1=rel1, rel5=rel5, rel20=rel20, rel60=sval(key, "rel60"),
                                  abs5=abs5, vol_r=vol_r, breadth=breadth, base_score=base_score,
                                  over_ext=over_ext, distribution=distribution, beta_lift=beta_lift,
                                  trend_code=trend_code, accel=accel)

    # ---- 板块计数 ----
    pos_rel_count = sum(1 for k in SECTOR_KEYS if (theme_scalars[k]["rel5"] is not None and not np.isnan(theme_scalars[k]["rel5"]) and theme_scalars[k]["rel5"] > 0))
    weak_count = sum(1 for k in SECTOR_KEYS if (theme_scalars[k]["rel5"] is not None and not np.isnan(theme_scalars[k]["rel5"]) and theme_scalars[k]["rel5"] < 0))
    abs_down_count = sum(1 for k in SECTOR_KEYS if (theme_scalars[k]["abs5"] is not None and not np.isnan(theme_scalars[k]["abs5"]) and theme_scalars[k]["abs5"] < 0))

    # ---- 八条归因剧本（标量）----
    defShiftRaw = (score_accel(neg_part(off_c_accel), 2.0, 25.0) + score_accel(pos_part(def_c_accel), 2.0, 25.0)
                   + score_accel(pos_part(def_off_accel), 1.5, 20.0)
                   + (10.0 if (def_c5 is not None and not np.isnan(def_c5) and off_c5 is not None and not np.isnan(off_c5) and def_c5 > off_c5) else 0.0)
                   + (5.0 if (def_c5 is not None and not np.isnan(def_c5) and def_c5 > 0) else 0.0))
    offShiftRaw = (score_accel(neg_part(def_c_accel), 2.0, 25.0) + score_accel(pos_part(off_c_accel), 2.0, 25.0)
                   + score_accel(pos_part(off_def_accel), 1.5, 20.0)
                   + (10.0 if (def_c5 is not None and not np.isnan(def_c5) and off_c5 is not None and not np.isnan(off_c5) and off_c5 > def_c5) else 0.0)
                   + (5.0 if (off_c5 is not None and not np.isnan(off_c5) and off_c5 > 0) else 0.0))
    flip_to_def = defShiftRaw >= offShiftRaw
    flip_score = cap100(max(defShiftRaw, offShiftRaw))

    iwm = theme_scalars["iwm"]; rsp = theme_scalars["rsp"]
    small_score = cap100(score_accel(pos_part(iwm["accel"]), 1.5, 35.0)
                         + (15.0 if (iwm["rel5"] is not None and not np.isnan(iwm["rel5"]) and iwm["rel5"] > 0) else 0.0)
                         + (20.0 if (iwm["base_score"] is not None and not np.isnan(iwm["base_score"]) and iwm["base_score"] >= 60) else 0.0)
                         + (15.0 if (leg_hyg_rel5 is not None and not np.isnan(leg_hyg_rel5) and leg_hyg_rel5 > 0) else 0.0))

    broad_score = cap100(score_accel(pos_part(rsp["accel"]), 1.5, 35.0)
                         + (15.0 if (rsp["rel5"] is not None and not np.isnan(rsp["rel5"]) and rsp["rel5"] > 0) else 0.0)
                         + (20.0 if (rsp["base_score"] is not None and not np.isnan(rsp["base_score"]) and rsp["base_score"] >= 60) else 0.0)
                         + (15.0 if pos_rel_count >= 7 else (8.0 if pos_rel_count >= 5 else 0.0)))

    xlu_a = theme_scalars["xlu"]["accel"]; xlre_a = theme_scalars["xlre"]["accel"]; xlf_a = theme_scalars["xlf"]["accel"]
    rate_bene_accel = (xlu_a + xlre_a) / 2.0 if (xlu_a is not None and not np.isnan(xlu_a) and xlre_a is not None and not np.isnan(xlre_a)) else np.nan
    rate_gap = rate_bene_accel - xlf_a if (rate_bene_accel is not None and not np.isnan(rate_bene_accel) and xlf_a is not None and not np.isnan(xlf_a)) else np.nan
    rate_sign_down = (rate_gap is not None and not np.isnan(rate_gap) and rate_gap > 0 and leg_tlt5 is not None and not np.isnan(leg_tlt5) and leg_tlt5 > 0)
    rate_sign_up = (rate_gap is not None and not np.isnan(rate_gap) and rate_gap < 0 and leg_tlt5 is not None and not np.isnan(leg_tlt5) and leg_tlt5 < 0)
    rate_raw = 0.0
    if rate_sign_down or rate_sign_up:
        rate_raw += 25.0
        rate_raw += score_accel(abs(rate_gap) if rate_gap is not None and not np.isnan(rate_gap) else np.nan, 2.0, 30.0)
        rate_raw += score_accel(leg_tlt5 if leg_tlt5 is not None and not np.isnan(leg_tlt5) else np.nan, 2.0, 20.0)
    if rate_sign_down and theme_scalars["xlu"]["rel5"] is not None and not np.isnan(theme_scalars["xlu"]["rel5"]) and theme_scalars["xlu"]["rel5"] > 0 and theme_scalars["xlre"]["rel5"] is not None and not np.isnan(theme_scalars["xlre"]["rel5"]) and theme_scalars["xlre"]["rel5"] > 0:
        rate_raw += 15.0
    elif rate_sign_up and theme_scalars["xlf"]["rel5"] is not None and not np.isnan(theme_scalars["xlf"]["rel5"]) and theme_scalars["xlf"]["rel5"] > 0:
        rate_raw += 15.0
    if rate_sign_down and theme_scalars["xlf"]["rel5"] is not None and not np.isnan(theme_scalars["xlf"]["rel5"]) and theme_scalars["xlf"]["rel5"] < 0:
        rate_raw += 10.0
    elif rate_sign_up and ((theme_scalars["xlu"]["rel5"] is not None and not np.isnan(theme_scalars["xlu"]["rel5"]) and theme_scalars["xlu"]["rel5"] < 0) or (theme_scalars["xlre"]["rel5"] is not None and not np.isnan(theme_scalars["xlre"]["rel5"]) and theme_scalars["xlre"]["rel5"] < 0)):
        rate_raw += 10.0
    rate_score = cap100(rate_raw)
    rate_dir_txt = "利率下行剧本" if rate_sign_down else ("利率上行剧本" if rate_sign_up else "利率与板块不同调")

    xle = theme_scalars["xle"]; xlb = theme_scalars["xlb"]
    infl_score = cap100(score_accel(pos_part(emb_tech_accel), 1.5, 30.0)
                        + (15.0 if (xle["rel5"] is not None and not np.isnan(xle["rel5"]) and xle["rel5"] > 0 and xlb["rel5"] is not None and not np.isnan(xlb["rel5"]) and xlb["rel5"] > 0) else 0.0)
                        + score_accel(pos_part(xle["accel"]), 2.0, 15.0)
                        + (10.0 if (leg_gld5 is not None and not np.isnan(leg_gld5) and leg_gld5 > 0) else 0.0)
                        + (10.0 if (leg_tlt5 is not None and not np.isnan(leg_tlt5) and leg_tlt5 < 0) else 0.0)
                        + (20.0 if ((xle["base_score"] is not None and not np.isnan(xle["base_score"]) and xle["base_score"] >= 60) or (xlb["base_score"] is not None and not np.isnan(xlb["base_score"]) and xlb["base_score"] >= 60)) else 0.0))

    def_leading = def_c5 is not None and not np.isnan(def_c5) and def_c5 > 0
    haven_raw = 0.0
    if def_leading:
        haven_raw += 25.0 if (leg_tlt5 is not None and not np.isnan(leg_tlt5) and leg_tlt5 > 0) else 0.0
        haven_raw += 20.0 if (leg_gld5 is not None and not np.isnan(leg_gld5) and leg_gld5 > 0) else 0.0
        haven_raw += 25.0 if (leg_hyg_rel5 is not None and not np.isnan(leg_hyg_rel5) and leg_hyg_rel5 < 0) else 0.0
        bench_abs5 = last_of(bench["a5"])
        haven_raw += 15.0 if (bench_abs5 is not None and not np.isnan(bench_abs5) and bench_abs5 < 0) else 0.0
        haven_raw += 15.0 if (leg_kre_rel5 is not None and not np.isnan(leg_kre_rel5) and leg_kre_rel5 < 0) else 0.0
    haven_score = cap100(haven_raw)
    if def_leading:
        haven_hint = leg_txt(leg_tlt5, "债涨", "债跌") + " · " + leg_txt(leg_gld5, "金涨", "金跌") + " · " + (("信用缺数") if (leg_hyg_rel5 is None or np.isnan(leg_hyg_rel5)) else ("信用弱" if leg_hyg_rel5 < 0 else "信用稳"))
    else:
        haven_hint = "防守未领先, 本题不成立"

    de_risk_score = cap100(
        (35.0 if weak_count >= 8 else (22.0 if weak_count >= 6 else (10.0 if weak_count >= 5 else 0.0)))
        + (25.0 if abs_down_count >= 8 else (15.0 if abs_down_count >= 6 else 0.0))
        + (10.0 if (def_leading and def_c5 is not None and not np.isnan(def_c5) and off_c5 is not None and not np.isnan(off_c5) and def_c5 > off_c5) else 0.0)
        + (10.0 if (leg_hyg_rel5 is not None and not np.isnan(leg_hyg_rel5) and leg_hyg_rel5 < 0) else 0.0)
        + (5.0 if (leg_kre_rel5 is not None and not np.isnan(leg_kre_rel5) and leg_kre_rel5 < -2.0) else 0.0)
    )

    # 月/季末再平衡
    mo = as_of.month
    dom = as_of.day
    is_month_turn = dom >= 24 or dom <= 3
    is_quarter_turn = ((mo in (3, 6, 9, 12)) and dom >= 20) or ((mo in (1, 4, 7, 10)) and dom <= 3)
    rebal_keys = SECTOR_KEYS + ["iwm", "rsp", "qqq"]
    winner_decel = 0
    laggard_recovery = 0
    for k in rebal_keys:
        r20 = theme_scalars[k]["rel20"]
        acc = theme_scalars[k]["accel"]
        if r20 is None or np.isnan(r20) or acc is None or np.isnan(acc):
            continue
        if r20 > 4 and acc < 0:
            winner_decel += 1
        if r20 < 1.5 and acc > 0:
            laggard_recovery += 1
    rebalance_raw = (20.0 if is_month_turn else 0.0) + (25.0 if is_quarter_turn else 0.0)
    rebalance_raw += (25.0 if winner_decel >= 3 else (18.0 if winner_decel == 2 else (10.0 if winner_decel == 1 else 0.0)))
    rebalance_raw += (25.0 if laggard_recovery >= 3 else (18.0 if laggard_recovery == 2 else (10.0 if laggard_recovery == 1 else 0.0)))
    rebalance_raw += (10.0 if flip_score >= 45 else 0.0)
    rebalance_score = cap100(rebalance_raw)

    # ---- 联动加分 → 终态/原因 ----
    defBoost = (10.0 if flip_score >= 60 else (5.0 if flip_score >= 45 else 0.0)) if flip_to_def else 0.0
    offBoost = (10.0 if flip_score >= 60 else (5.0 if flip_score >= 45 else 0.0)) if not flip_to_def else 0.0
    rateDnBoost = (8.0 if rate_score >= 60 else (4.0 if rate_score >= 45 else 0.0)) if rate_sign_down else 0.0
    rateUpBoost = (8.0 if rate_score >= 60 else (4.0 if rate_score >= 45 else 0.0)) if rate_sign_up else 0.0
    inflBoost = 8.0 if infl_score >= 60 else (4.0 if infl_score >= 45 else 0.0)

    def boost_score(base, boost):
        return cap100(base + boost) if base is not None and not np.isnan(base) else np.nan

    boost_map = {
        "xlk": offBoost, "xlf": offBoost + rateUpBoost, "xlv": defBoost, "xly": offBoost,
        "xlp": defBoost, "xle": inflBoost, "xli": 0.0, "xlb": inflBoost,
        "xlu": defBoost + rateDnBoost, "xlre": rateDnBoost, "xlc": 0.0,
        "iwm": (12.0 if small_score >= 60 else (6.0 if small_score >= 45 else 0.0)),
        "rsp": (12.0 if broad_score >= 60 else (6.0 if broad_score >= 45 else 0.0)),
        "qqq": 0.0,
    }

    final: Dict[str, dict] = {}
    for idv, key, name in THEME_ORDER:
        sc = theme_scalars[key]
        score = boost_score(sc["base_score"], boost_map[key])
        state = compute_state_from_score(score, sc["over_ext"], sc["distribution"], sc["rel5"], sc["rel20"])
        reason = theme_reason(idv, state, score, sc["rel5"], sc["rel20"], sc["abs5"], sc["beta_lift"],
                              de_risk_score, flip_score, flip_to_def, small_score, broad_score,
                              rate_score, rate_sign_down, rate_sign_up, infl_score, rebalance_score)
        final[key] = dict(id=idv, name=name, score=score, state=state, reason=reason,
                          rel5=sc["rel5"], rel20=sc["rel20"], rel60=sc["rel60"], abs5=sc["abs5"],
                          vol_r=sc["vol_r"], breadth=sc["breadth"], trend_code=sc["trend_code"],
                          base_score=sc["base_score"])

    # 基准终态
    bench_score = last_of(bench["score"])
    bench_state = int(last_of(bench["state"]))
    bench_abs5 = last_of(bench["a5"])
    bench_abs20 = last_of(bench["a20"])
    bench_abs60 = last_of(bench["a60"])
    bench_vol_r = last_of(bench["vol_r"])
    bench_trend = int(last_of(bench["trend_code"]))
    bench_reason_code = bench_reason(bench_state, bench_abs5, bench_breadth, bench_score, bench_abs20)

    # ===================== 输出 =====================
    expired = dt.date.today() > EXPIRY
    days_left = max(0, (EXPIRY - dt.date.today()).days)

    # JSON
    themes_out = []
    for idv, key, name in THEME_ORDER:
        t = final[key]
        themes_out.append({
            "id": idv, "name": name, "proxy": TICKERS[key],
            "rel5_pct": None if (t["rel5"] is not None and np.isnan(t["rel5"])) else round(float(t["rel5"]), 4),
            "rel20_pct": None if (t["rel20"] is not None and np.isnan(t["rel20"])) else round(float(t["rel20"]), 4),
            "rel60_pct": None if (t["rel60"] is not None and np.isnan(t["rel60"])) else round(float(t["rel60"]), 4),
            "abs5_pct": None if (t["abs5"] is not None and np.isnan(t["abs5"])) else round(float(t["abs5"]), 4),
            "trend": trend_text(t["trend_code"]),
            "vol_ratio": None if (t["vol_r"] is not None and np.isnan(t["vol_r"])) else round(float(t["vol_r"]), 4),
            "breadth_pct": None if (t["breadth"] is not None and np.isnan(t["breadth"])) else round(float(t["breadth"]), 2),
            "score": None if (t["score"] is not None and np.isnan(t["score"])) else round(float(t["score"]), 2),
            "base_score": None if (t["base_score"] is not None and np.isnan(t["base_score"])) else round(float(t["base_score"]), 2),
            "state": state_text(t["state"]), "state_code": int(t["state"]),
            "reason": reason_text(t["reason"]), "reason_code": int(t["reason"]),
        })

    attribution = [
        {"name": "攻守对调", "score": round(float(flip_score), 2),
         "hint": ("转守" if flip_to_def else "转攻") + " · 防守减进攻提速差: " + fmt_pct(def_c_accel - off_c_accel)},
        {"name": "小盘接棒", "score": round(float(small_score), 2), "hint": "小盘对基准提速: " + fmt_pct(iwm["accel"])},
        {"name": "等权追赶", "score": round(float(broad_score), 2),
         "hint": "等权提速: " + fmt_pct(rsp["accel"]) + " · 转正板块 " + str(pos_rel_count) + "/11"},
        {"name": "利率驱动", "score": round(float(rate_score), 2), "hint": rate_dir_txt + " · 债腿5日: " + fmt_pct(leg_tlt5)},
        {"name": "通胀后周期", "score": round(float(infl_score), 2), "hint": "能源材料对科技提速: " + fmt_pct(emb_tech_accel)},
        {"name": "真避险对假防守", "score": round(float(haven_score), 2), "hint": haven_hint},
        {"name": "全场降风险", "score": round(float(de_risk_score), 2), "hint": "转弱板块: " + str(weak_count) + "/11"},
        {"name": "月/季末再平衡", "score": round(float(rebalance_score), 2),
         "hint": "强者减速 " + str(winner_decel) + " 个 / 弱者回血 " + str(laggard_recovery) + " 个"},
    ]

    bench_out = {
        "name": "基准(SPY)", "proxy": "SPY",
        "abs5_pct": None if (bench_abs5 is not None and np.isnan(bench_abs5)) else round(float(bench_abs5), 4),
        "abs20_pct": None if (bench_abs20 is not None and np.isnan(bench_abs20)) else round(float(bench_abs20), 4),
        "abs60_pct": None if (bench_abs60 is not None and np.isnan(bench_abs60)) else round(float(bench_abs60), 4),
        "trend": trend_text(bench_trend), "vol_ratio": None if (bench_vol_r is not None and np.isnan(bench_vol_r)) else round(float(bench_vol_r), 4),
        "breadth_pct": round(float(bench_breadth), 2), "score": round(float(bench_score), 2),
        "state": bench_state_text(bench_state), "state_code": bench_state,
        "reason": reason_text(bench_reason_code), "reason_code": int(bench_reason_code),
        # 旁路诊断（不影响上面任何字段）
        "components": bench_components,
        "score_cont": bench_score_cont,
        "breadth_divergence": bench_divergence,
    }

    payload = {
        "as_of": as_of.isoformat(),
        "run_date": dt.date.today().isoformat(),
        "version": VER_TXT,
        "expired": expired,
        "days_left": days_left,
        "bench": bench_out,
        "themes": themes_out,
        "attribution": attribution,
        "counts": {"pos_rel": pos_rel_count, "weak": weak_count, "abs_down": abs_down_count},
        "data_source": {"from_cache": from_cache, "cache_used": bool(from_cache)},
    }

    json_path = report_dir / f"sector_rotation_{as_of.isoformat()}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s", json_path)

    # Markdown
    md = build_markdown(payload, days_left, expired)
    md_path = report_dir / f"sector_rotation_{as_of.isoformat()}.md"
    md_path.write_text(md, encoding="utf-8")
    logger.info("wrote %s", md_path)
    print(f"OK as_of={as_of} bench_score={bench_score:.1f}({bench_state_text(bench_state)}) themes={len(themes_out)} attribution={len(attribution)} -> {md_path.name}")
    return 0


def build_markdown(p: dict, days_left: int, expired: bool) -> str:
    L = []
    L.append(f"# 标普板块资金轮动观测 · {p['as_of']}")
    L.append("")
    L.append(f"- 版本：{p['version']}（本地日频复刻，配方/阈值与 TV Pine 原版一致）")
    L.append(f"- 生成：{p['run_date']} · 数据锚点：{p['as_of']}（标普最新交易日）")
    if expired:
        L.append(f"- ⚠️ **已过期**（有效期至 {EXP_TXT}）：下列读数基于过期引擎，请在会员渠道获取当前版本后替换。")
    elif days_left <= 14:
        L.append(f"- ⚠️ 距脚本到期 {days_left} 天（{EXP_TXT}）。")
    _ds = p.get("data_source") or {}
    if _ds.get("from_cache"):
        L.append(f"- ⚠️ **数据回退**：以下标的联网拉取失败，已用本地缓存（非实时）—— {', '.join(_ds['from_cache'])}")
    L.append("")
    L.append("> 本工具为教学与数据参考工具，全部输出仅为对公开市场数据的统计描述，不构成投资建议。")
    L.append("")

    b = p["bench"]
    L.append("## 基准行（SPY 绝对口径）")
    L.append("")
    L.append(f"| 分数 | 状态 | 绝对5D | 绝对20D | 绝对60D | 趋势 | 成交额 | 广度 | 可能原因 |")
    L.append(f"|---|---|---|---|---|---|---|---|---|")
    L.append(f"| {fmt_score(b['score'])} | {STATE_EMOJI[b['state_code']]}{b['state']} | {fmt_pct(b['abs5_pct'])} | {fmt_pct(b['abs20_pct'])} | {fmt_pct(b['abs60_pct'])} | {b['trend']} | {fmt_num(b['vol_ratio'])}x | {fmt_pct(b['breadth_pct'])} | {b['reason']} |")
    L.append("")
    L.append("> 基准行广度 = 11 板块站上自身20日均线占比（全表唯一真广度）；主题行的「站上MA20」是该 ETF 自身价格与自身20均的关系，不是板块广度。")
    L.append("")

    # ---- 旁路诊断：分数怎么来的、广度有没有和资金流打架 ----
    comp = b.get("components") or {}
    if comp:
        _tot = sum(v for v in comp.values() if v is not None)
        L.append("## 基准分数分解（诊断 · 不改主分数）")
        L.append("")
        L.append("| 趋势 /30 | 资金流 /34 | 量能 /20 | 广度 /15 | 拥挤 /8 | 合计 |")
        L.append("|---|---|---|---|---|---|")
        L.append(f"| {fmt_score(comp.get('trend'))} | {fmt_score(comp.get('flow'))} | {fmt_score(comp.get('vol'))} | "
                 f"{fmt_score(comp.get('breadth'))} | {fmt_score(comp.get('crowd'))} | {fmt_score(_tot)} |")
        L.append("")
        _sc, _s = b.get("score_cont"), b.get("score")
        _delta = (None if (_sc is None or _s is None) else round(float(_sc) - float(_s), 1))
        L.append(f"- 广度连续口径影子分 **{fmt_score(_sc)}**（现行三档阶跃为 {fmt_score(_s)}，差 {_delta if _delta is None else f'{_delta:+.0f}'}）"
                 f"—— 仅供对照，不进入状态判定。")
        if b.get("breadth_divergence"):
            L.append(f"- ⚠️ **广度-流动背离**：资金流分量 {fmt_score(comp.get('flow'))}/34 已在高档，但真广度仅 "
                     f"{fmt_pct(b['breadth_pct'])}（< {DIVERGE_BREADTH_MAX:.0f}%）—— 指数由少数龙头拉动，提防补跌。")
        else:
            L.append("- 无广度-流动背离（资金流未打满高档，或真广度仍在 50% 以上）。")
        L.append("")

    L.append("## 主题行（相对 SPY 比价打分）")
    L.append("")
    L.append("| 主题 | 代理 | 5D相对 | 20D相对 | 60D相对 | 绝对5D | 趋势 | 成交额 | 站上MA20 | 分数/状态 | 可能原因 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for t in p["themes"]:
        _bv = t.get("breadth_pct")
        _above = "—" if _bv is None else ("上方" if float(_bv) >= 70.0 else "下方")
        L.append(
            f"| {t['name']} | {t['proxy']} | {fmt_pct(t['rel5_pct'])} | {fmt_pct(t['rel20_pct'])} | {fmt_pct(t['rel60_pct'])} | {fmt_pct(t['abs5_pct'])} | {t['trend']} | {fmt_num(t['vol_ratio'])}x | {_above} | {fmt_score(t['score'])} {STATE_EMOJI[t['state_code']]}{t['state']} | {t['reason']} |"
        )
    L.append("")

    L.append("## 归因剧本（钱可能在按哪种剧本搬家）")
    L.append("")
    L.append("| 归因 | 分数 | 核心信号 |")
    L.append("|---|---|---|")
    for a in p["attribution"]:
        L.append(f"| {a['name']} | {fmt_score(a['score'])} | {a['hint']} |")
    L.append("")
    L.append(f"> 板块计数：转正(5D相对>0) {p['counts']['pos_rel']}/11 · 转弱(5D相对<0) {p['counts']['weak']}/11 · 绝对下跌 {p['counts']['abs_down']}/11")
    L.append("")
    L.append("---")
    L.append("")
    L.append("**读法速记**：基准走弱时主题高分只说明跌得少；基准「龙头抬轿」= 指数涨但过半板块没跟上，提防补跌。")
    L.append("分数 75 确认进入 / 60 早期轮动 / 45 转弱；>60 的归因剧本值得注意，但 60 是经验线不是下注指令。")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
