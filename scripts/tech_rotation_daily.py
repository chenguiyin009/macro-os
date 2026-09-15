#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科技板块资金轮动观测机 v2.6.1r — 本地日频复刻 (Pine 忠实移植)

将挂在 TradingView 上的会员 Pine 指标 `科技板块资金轮动观测机 v2.6.1r` 的
13 主题打分引擎在本地用 yfinance 日线公开数据完整复刻，每日独立出一份
「主题仪表盘 + 归因面板 + 所选主题轮动分数」读数，对齐 TV 快照的「一/二/三」三节。

为什么是 headless 移植（而非直连 TV）：
  原 TV 快照依赖本机 TradingView Desktop(Chrome+CDP 9222) + 手动挂载的 v2.6.1r
  + MCP 读 Pine 算值，云机无法复刻。本脚本用 yfinance 拉价，在本地 NumPy/Pandas
  上 1:1 还原 Pine 全部公式，输出与 TV 读数逐字对齐（已用 2026-09-01 已知快照校验）。

数据：
  基准 QQQ + 6 只 ETF(SMH/IGV/SKYY/CIBR/BOTZ/AIQ)
  + 7 个等权篮子(共 28 只成分：PLTR/APP/SNOW/DDOG/MDB/COHR/LITE/AAOI/ANET/CIEN/
    CSCO/MU/WDC/STX/SNDK/VRT/ETN/PWR/CEG/GEV/MSFT/AMZN/GOOGL/META/NVDA/AVGO/AMD/MRVL)

输出：
  output/tech_rotation_<as_of>.md   (人类阅读，对齐 TV 快照三节)
  output/tech_rotation_<as_of>.json (机器解析)
  as_of = 美东已收盘现金会话（盘前/缓存滞后时标 stale，优先复用已有 expected 产物）

用法：
  python tech_rotation_daily.py
  python tech_rotation_daily.py --date 2026-08-10 --report-dir /abs/path
  python tech_rotation_daily.py --force-refresh
  SECTOR_PROXY=off python tech_rotation_daily.py   # 无本地代理的云机
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tech-rotation")

DEFAULT_PROXY = "http://127.0.0.1:7890"
_PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY")
# 代理开关（向后兼容）：默认仍走本地代理 127.0.0.1:7890（用户本机行为不变）。
# 外部环境（如 grokbot 云机无本地代理）可设 SECTOR_PROXY 重定向或关闭：
#   SECTOR_PROXY=off|none|0|false|no|""  → 完全不设代理（依赖直连/环境自带代理）
#   SECTOR_PROXY=http://host:port         → 使用指定代理
# 若已设置系统变量 HTTPS_PROXY/HTTP_PROXY，则优先级最高、本函数不覆盖。
_PROXY_OFF_VALUES = ("", "off", "none", "0", "false", "no")

# ---- 版本与有效期（与会员渠道盖戳区一致）----
VER_TXT = "科技板块资金轮动观测机 v2.6.1r"
EXP_TXT = "2026-11-07"
EXPIRY = dt.date(2026, 11, 7)

# ---- 引擎固定参数（与 Pine 内部版完全一致）----
TREND_LEN = 20
SLOW_LEN = 50
VOL_LEN = 20
VOL_CONFIRM = 1.30
CROWD_REL60 = 18.0
CROWD_DIST50 = 8.0
HISTORY_BARS = 400  # 取足 350+ 以便所有 lookback（rel60/ma50/accel）稳定

# ---- 旁路设施 ----
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "tech_price_cache"
CACHE_MAX_BARS = 800
FETCH_ATTEMPTS = 2
FETCH_ATTEMPTS_FORCE = 5
FETCH_BACKOFF_BASE_SEC = 1.5
FETCH_BACKOFF_CAP_SEC = 20.0
FETCH_TICKER_GAP_SEC = 0.35

# ---- 标的映射 ----
QQQ_CODE = "QQQ"
ETF_MAP: Dict[str, str] = {  # key -> yfinance code
    "semi": "SMH", "soft": "IGV", "cloud": "SKYY", "cyber": "CIBR",
    "robot": "BOTZ", "ai": "AIQ",
}
BASKETS: Dict[str, List[str]] = {
    # key -> 成员 yfinance codes（Pine 篮子为等权）
    "app":   ["PLTR", "APP", "SNOW", "DDOG", "MDB"],
    "opt":   ["COHR", "LITE", "AAOI"],
    "net":   ["ANET", "CIEN", "CSCO"],
    "mem":   ["MU", "WDC", "STX", "SNDK"],
    "infra": ["VRT", "ETN", "PWR", "CEG", "GEV"],
    "hyp":   ["MSFT", "AMZN", "GOOGL", "META"],
    "hw":    ["NVDA", "AVGO", "AMD", "MRVL"],
}

# 13 主题行顺序（id 与 Pine themeReason 一致）
THEME_ORDER: List[Tuple[int, str, str, str]] = [
    # id, key, 中文名, 代理显示
    (1, "semi", "半导体", "SMH"),
    (2, "soft", "软件", "IGV"),
    (3, "cloud", "云计算", "SKYY"),
    (4, "cyber", "网络安全", "CIBR"),
    (5, "robot", "机器人", "BOTZ"),
    (6, "ai", "AI综合", "AIQ"),
    (7, "app", "AI应用软件", "篮子"),
    (8, "opt", "光通信零部件", "篮子"),
    (9, "net", "AI网络", "篮子"),
    (10, "mem", "储存", "篮子"),
    (11, "infra", "数据中心电力散热", "篮子"),
    (12, "hyp", "云巨头/AI买方", "篮子"),
    (13, "hw", "AI硬件卖方", "篮子"),
]
THEME_KEYS = [t[1] for t in THEME_ORDER]
BASKET_KEYS = [t[1] for t in THEME_ORDER if t[1] in BASKETS]
ETF_KEYS = [t[1] for t in THEME_ORDER if t[1] in ETF_MAP]
DEFAULT_SELECTED = "semi"  # Pine input 默认「半导体」


selected_name_map = {t[1]: t[2] for t in THEME_ORDER}

# ---- 美东已收盘会话（与 daily_macro as_of 合同对齐）----
_NY_TZ = ZoneInfo("America/New_York")
_US_CASH_CLOSE_HOUR = 16
# 轻量 NYSE 假日代理（与四腿同集）；缺漏时最坏是多退一天
_US_FED_HOLIDAYS = {
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16),
    dt.date(2026, 4, 3), dt.date(2026, 5, 25), dt.date(2026, 6, 19),
    dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26),
    dt.date(2026, 12, 25),
}


def _is_us_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in _US_FED_HOLIDAYS


def _prior_us_business_day(d: dt.date) -> dt.date:
    x = d - dt.timedelta(days=1)
    while not _is_us_business_day(x):
        x -= dt.timedelta(days=1)
    return x


def _busday_lag(as_of: dt.date, expected: dt.date) -> int:
    """Trading-day lag of as_of behind expected (positive => stale)."""
    if as_of >= expected:
        return 0
    n = 0
    x = as_of
    while x < expected:
        x += dt.timedelta(days=1)
        if _is_us_business_day(x):
            n += 1
    return n


def resolve_completed_us_cash_session(explicit: Optional[str] = None) -> Dict[str, Any]:
    """Auto: post-16:00 ET on a US BD -> that day; else prior US BD."""
    now_ny = dt.datetime.now(_NY_TZ)
    today_ny = now_ny.date()
    session_closed = now_ny.hour >= _US_CASH_CLOSE_HOUR
    incomplete_bar = False
    actionable = True
    reason = "completed_us_cash_session"
    if explicit is None or str(explicit).strip() == "" or str(explicit).strip().lower() in ("auto", "latest"):
        if session_closed and _is_us_business_day(today_ny):
            as_of = today_ny
            reason = "post_close_same_bd"
        else:
            as_of = _prior_us_business_day(today_ny)
            reason = "pre_close_or_non_bd_prior"
    else:
        as_of = dt.date.fromisoformat(str(explicit)[:10])
        if as_of > today_ny:
            incomplete_bar = True
            actionable = False
            reason = "future_date_non_actionable"
        elif as_of == today_ny and not session_closed:
            incomplete_bar = True
            actionable = False
            reason = "pre_close_incomplete_bar"
        elif not _is_us_business_day(as_of):
            as_of = _prior_us_business_day(as_of)
            reason = "snapped_prior_bd"
        else:
            reason = "explicit_completed_or_historical"
    return {
        "as_of": as_of,
        "expected_as_of": as_of,
        "incomplete_bar": incomplete_bar,
        "actionable": actionable,
        "non_actionable": not actionable,
        "actionability_reason": reason,
        "now_ny": now_ny.isoformat(timespec="minutes"),
    }


# ===================== 代理注入 =====================
def _ensure_proxy() -> None:
    if any(os.environ.get(k) for k in _PROXY_KEYS):
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


# ===================== 数据获取 =====================

def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(tok in msg for tok in ("429", "rate limit", "too many requests", "quota"))


def _backoff_sleep(attempt_idx: int, *, rateish: bool, code: str, attempts: int) -> None:
    delay = min(FETCH_BACKOFF_CAP_SEC, FETCH_BACKOFF_BASE_SEC * (2 ** attempt_idx))
    delay *= 0.8 + 0.4 * random.random()
    if rateish:
        logger.warning(
            "download %s empty/throttled (attempt %d/%d), backoff %.1fs",
            code, attempt_idx + 1, attempts, delay,
        )
    else:
        logger.warning(
            "download %s retry (attempt %d/%d), backoff %.1fs",
            code, attempt_idx + 1, attempts, delay,
        )
    time.sleep(delay)

def _download_close_vol(code: str, period: str = "2y",
                        attempts: int = FETCH_ATTEMPTS) -> Optional[Tuple[pd.Series, pd.Series]]:
    """拉取单标的日线。对空结果/429 做指数退避重试；全部失败返回 None。"""
    import yfinance as yf
    _ensure_proxy()
    raw = None
    last_exc: Optional[BaseException] = None
    n = max(1, int(attempts))
    for i in range(n):
        last_exc = None
        try:
            raw = yf.download(code, period=period, auto_adjust=True, progress=False, threads=False)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("download %s failed (attempt %d/%d): %s", code, i + 1, n, exc)
            raw = None
        if raw is not None and not getattr(raw, "empty", True):
            break
        if i + 1 >= n:
            break
        rateish = (last_exc is not None and _is_rate_limit_error(last_exc)) or (
            raw is None or getattr(raw, "empty", True)
        )
        _backoff_sleep(i, rateish=rateish, code=code, attempts=n)
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


def load_data(force_refresh: bool = False):
    """返回 (data_code: Dict[code, {close,vol}], missing: List[code], from_cache: List[code])。

    force_refresh=True 用更多次退避重试强制联网（勿再跳过 download）。
    """
    needed = [QQQ_CODE] + list(ETF_MAP.values()) + [c for v in BASKETS.values() for c in v]
    out: Dict[str, Dict[str, pd.Series]] = {}
    missing: List[str] = []
    from_cache: List[str] = []
    attempts = FETCH_ATTEMPTS_FORCE if force_refresh else FETCH_ATTEMPTS
    for i, code in enumerate(needed):
        if i:
            time.sleep(FETCH_TICKER_GAP_SEC)
        res = _download_close_vol(code, attempts=attempts)
        if res is None:
            cached = _cache_read(code)
            if cached is not None and len(cached) >= 60:
                out[code] = {"close": cached["Close"], "vol": cached["Volume"]}
                from_cache.append(code)
                logger.warning("network failed for %s — fallback cache (%d bars, last %s)",
                               code, len(cached), cached.index[-1].date())
            else:
                missing.append(code)
                logger.error("no data for %s: network failed and no usable cache", code)
            continue
        close, vol = res
        _cache_write(code, close, vol)
        out[code] = {"close": close, "vol": vol}
        logger.info("fetched %s: %d bars", code, len(close))
    return out, missing, from_cache


# ===================== 向量化指标 =====================
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def pct_change_n(s: pd.Series, n: int) -> pd.Series:
    x = s.shift(n)
    out = (s - x) / x * 100.0
    return out.replace([np.inf, -np.inf], np.nan)


def ret_one(s: pd.Series) -> pd.Series:
    x = s.shift(1)
    return ((s - x) / x).replace([np.inf, -np.inf], np.nan)


def safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.divide(b).where(b != 0.0)


def cap100(x) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan
    return float(min(100.0, max(0.0, x)))


def chain_index(ret: pd.Series) -> pd.Series:
    """Pine: idx := na(idx[1]) ? 100 : na(ret) ? idx[1] : idx[1]*(1+ret)"""
    cum = pd.Series(index=ret.index, dtype=float)
    prev = 100.0
    arr = ret.to_numpy()
    for i in range(len(arr)):
        v = arr[i]
        if v is None or (isinstance(v, float) and np.isnan(v)):
            cum.iloc[i] = prev
        else:
            prev = prev * (1.0 + float(v))
            cum.iloc[i] = prev
    return cum


def avg_ret5(closes: List[pd.Series]):
    rets = [ret_one(c) for c in closes if c is not None]
    if not rets:
        idx = next((c.index for c in closes if c is not None), None)
        empty = pd.Series(dtype=float, index=idx) if idx is not None else pd.Series(dtype=float)
        return empty, empty
    df = pd.concat(rets, axis=1)
    return df.mean(axis=1, skipna=True), df.notna().sum(axis=1)


def breadth_etf(c: pd.Series) -> pd.Series:
    m = sma(c, TREND_LEN)
    out = pd.Series(np.where((c > m).fillna(False), 70.0, 30.0), index=c.index)
    return out.where(~(c.isna() | m.isna()))


def breadth_basket(members: List[pd.Series]) -> pd.Series:
    """篮子广度：站上自身20均的成员占比（%）。"""
    valid = [c for c in members if c is not None]
    if not valid:
        return pd.Series(dtype=float)
    above = []
    cnt = []
    for c in valid:
        m = sma(c, TREND_LEN)
        above.append((c > m).fillna(False).astype(float))
        cnt.append(c.notna().astype(float))
    above_sum = sum(above)
    cnt_sum = sum(cnt)
    pct = (above_sum / cnt_sum * 100.0).replace([np.inf, -np.inf], np.nan)
    return pct


def dvol_avg(members: List[pd.Series], vols: List[pd.Series]) -> pd.Series:
    """篮子的美元成交额 = 有效成员 c*v 的均值（与 Pine dvolAvg 一致）。"""
    valid = []
    for c, v in zip(members, vols):
        if c is not None and v is not None:
            valid.append(c * v)
    if not valid:
        return pd.Series(dtype=float)
    df = pd.concat(valid, axis=1)
    return df.mean(axis=1, skipna=True)


def rel_accel(rel5, rel20):
    return rel5 - rel20 / 4.0


def pos_part(x):
    return x if x is None or (isinstance(x, float) and np.isnan(x)) else max(x, 0.0)


def neg_part(x):
    return x if x is None or (isinstance(x, float) and np.isnan(x)) else max(-x, 0.0)


def score_accel(x, scale: float, max_score: float) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0.0
    return float(min(max_score, max(0.0, x / scale * max_score)))


def calc_metrics_full(idx: pd.Series, dv: pd.Series, c_qqq: pd.Series):
    """返回完整序列；仅末值用于读数。dv = 美元成交额序列；c_qqq = 基准收盘价序列。"""
    # Align to idx so np.where/Series(index=...) cannot see 401 vs 400 when
    # yfinance/cache bars differ by one session across close vs volume.
    dv = dv.reindex(idx.index)
    c_qqq = c_qqq.reindex(idx.index)
    ratio = safe_div(idx, c_qqq)
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

    valid = ratio.notna() & c_qqq.notna()
    trend_score = (ratio > ma20).astype(float) * 10.0 + (ratio > ma50).astype(float) * 10.0 + (ma20 > ma20.shift(5)).astype(float) * 10.0
    flow_score = (rel5 > 0).astype(float) * 10.0 + (rel20 > 0).astype(float) * 8.0 + (rel5 > rel5.shift(5)).astype(float) * 8.0 + (rel5 > rel20 / 4.0).astype(float) * 8.0
    rel1_pos = (rel1 > 0).fillna(False)
    vol_score = pd.Series(0.0, index=ratio.index)
    vol_score = vol_score.mask(rel1_pos & (vol_r >= 1.0).fillna(False), 10.0)
    vol_score = vol_score.mask(rel1_pos & (vol_r >= VOL_CONFIRM).fillna(False), 20.0)
    # 广度在此传标量系列（已是 70/30 或 %），合成 br_score 用 state 判定时再分类
    br = None  # 占位，由调用方传入 breadth 系列后单独算 br_score
    over_ext = (rel60 > CROWD_REL60).fillna(False) | (dist50 > CROWD_DIST50).fillna(False)
    crowd_score = pd.Series(8.0, index=ratio.index).mask(over_ext.fillna(False), 0.0)
    # 注意：raw 不含 br，br 在调用处结合 breadth 系列补
    raw = trend_score + flow_score + vol_score + crowd_score
    score = raw.clip(0, 100).where(valid)

    distribution = valid & (ratio < ma20) & (rel5 < 0) & (vol_r > VOL_CONFIRM) & (rel1 < 0)
    beta_lift = valid & (abs5 > 0) & (rel5 < 0)
    trend_code = np.select(
        [(ratio > ma20) & (ratio > ma50), ratio > ma20, (ratio < ma20) & (ratio < ma50), ratio < ma20],
        [2, 1, -2, -1], default=0).astype(int)
    return {
        "idx": idx, "ratio": ratio, "rel1": rel1, "rel5": rel5, "rel20": rel20, "rel60": rel60,
        "abs5": abs5, "ma20": ma20, "ma50": ma50, "vol_r": vol_r, "dist50": dist50,
        "over_ext": over_ext, "score": score, "distribution": distribution, "beta_lift": beta_lift,
        "trend_code": pd.Series(trend_code, index=ratio.index),
        "crowd_score": pd.Series(crowd_score, index=ratio.index),
        "trend_score": trend_score, "flow_score": flow_score, "vol_score": vol_score,
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


def reason_text(code: int) -> str:
    return {1: "主动流入", 2: "早期轮动", 3: "强弱对调", 4: "AI链条扩散", 5: "云巨头接棒",
            6: "拥挤主升", 7: "拥挤出清", 8: "派发撤出", 9: "跟涨假强", 10: "抗跌观察",
            11: "内部降风险", 12: "月/季再平衡", 13: "AI应用追赶", 99: "无数据"}.get(code, "中性")


def state_from_score(score, over_ext, distribution, rel5, rel20) -> int:
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
                 de_risk_s, pair_s, ai_app_s,
                 optical_s, net_s, mem_s, power_s, buyer_s, rebal_s, crowd_unwind_s) -> int:
    if state == 9:
        return 99
    if state == -2:
        return 8
    if beta_lift and (score is not None and not np.isnan(score) and score < 60):
        return 9
    if de_risk_s >= 70 and (abs5 is not None and not np.isnan(abs5) and abs5 < 0) and (rel5 is not None and not np.isnan(rel5) and rel5 < 0):
        return 11
    if idv == 2 and pair_s >= 60:
        return 3
    if idv == 7 and ai_app_s >= 60:
        return 13
    if (idv in (8, 9, 10, 11)) and (idv == 8 and optical_s >= 60 or idv == 9 and net_s >= 60 or idv == 10 and mem_s >= 60 or idv == 11 and power_s >= 60) and (score is not None and not np.isnan(score) and score >= 60):
        return 4
    if idv == 12 and buyer_s >= 60:
        return 5
    if rebal_s >= 65 and state <= 1:
        return 12
    if (idv in (1, 13)) and crowd_unwind_s >= 60 and state <= 0:
        return 7
    if state == 3:
        return 6
    if score is not None and not np.isnan(score) and score >= 75:
        return 1
    if score is not None and not np.isnan(score) and score >= 60:
        return 2
    if (abs5 is not None and not np.isnan(abs5) and abs5 < 0) and (rel5 is not None and not np.isnan(rel5) and rel5 > 0):
        return 10
    if (rel5 is not None and not np.isnan(rel5) and rel5 < 0) and (rel20 is not None and not np.isnan(rel20) and rel20 < 0):
        return 8
    return 0


def last_of(s):
    return s.iloc[-1] if s is not None else np.nan


def sval(x):
    return None if (x is None or (isinstance(x, float) and np.isnan(x))) else float(x)


# ===================== 主流程 =====================
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="科技板块资金轮动 v2.6.1r 日频复刻")
    parser.add_argument("--date", default=None, help="指定 as_of（YYYY-MM-DD），默认用最新交易日")
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--theme", default=DEFAULT_SELECTED, help="所选主题(默认 半导体)")
    args = parser.parse_args(argv)

    _ensure_proxy()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    ROOT = Path(__file__).resolve().parents[2]
    report_dir = Path(args.report_dir) if args.report_dir else ROOT / "output"
    report_dir.mkdir(parents=True, exist_ok=True)

    data, missing, from_cache = load_data(force_refresh=args.force_refresh)
    if QQQ_CODE not in data or len(data) < 10:
        logger.error("数据不完整（缺 QQQ 或拉取失败过多），无法出读数。联网失败会回退本地缓存，"
                     "缓存也缺失才报此错。请检查代理/网络后重跑。")
        return 2
    if missing:
        logger.error("以下标的缺失（已回退缓存或拉取失败）：%s", ", ".join(missing))
    if from_cache:
        logger.warning("以下标的来自本地缓存（非实时）：%s", ", ".join(from_cache))

    # 对齐公共日期轴
    all_close = {code: v["close"] for code, v in data.items()}
    all_vol = {code: v["vol"] for code, v in data.items()}
    df_close = pd.DataFrame(all_close).ffill().bfill()
    df_vol = pd.DataFrame(all_vol).ffill().fillna(0.0)
    common = df_close.index.intersection(df_vol.index)
    df_close = df_close.loc[common].tail(HISTORY_BARS)
    df_vol = df_vol.reindex(df_close.index).ffill().fillna(0.0)

    session = resolve_completed_us_cash_session(args.date)
    expected = session["as_of"]
    data_last = df_close.index[-1].date()

    # 数据落后于美东已收盘日：强制再拉一次（缓存/429 常见）
    if data_last < expected and not args.force_refresh:
        logger.warning(
            "data last=%s < expected completed session=%s; retry force_refresh once",
            data_last, expected,
        )
        data, missing, from_cache = load_data(force_refresh=True)
        all_close = {code: v["close"] for code, v in data.items()}
        all_vol = {code: v["vol"] for code, v in data.items()}
        df_close = pd.DataFrame(all_close).ffill().bfill()
        df_vol = pd.DataFrame(all_vol).ffill().fillna(0.0)
        common = df_close.index.intersection(df_vol.index)
        df_close = df_close.loc[common].tail(HISTORY_BARS)
        df_vol = df_vol.reindex(df_close.index).ffill().fillna(0.0)
        data_last = df_close.index[-1].date()

    # 目标 as_of：默认=expected；显式 --date 已在 session 解析
    # 禁止前跳到 > expected 的盘中/次日 bar；缺日只回退到 <= expected
    available = sorted({d.date() for d in df_close.index})
    if expected in set(available):
        as_of = expected
    else:
        prior = [d for d in available if d <= expected]
        if not prior:
            logger.error("无 <= expected=%s 的交易日数据，拒绝写出。", expected)
            return 2
        as_of = prior[-1]
        logger.warning("expected=%s 无数据，回退到 <=expected 的最近日 %s（禁止前跳）", expected, as_of)

    # 若仍落后 expected：优先复用已有完整产物，避免晨报把 as_of 写回更旧文件
    lag_days = _busday_lag(as_of, expected)
    stale = lag_days >= 1 or bool(from_cache)
    reused_artifact = False
    if lag_days >= 1:
        exact_json = report_dir / f"tech_rotation_{expected.isoformat()}.json"
        exact_md = report_dir / f"tech_rotation_{expected.isoformat()}.md"
        if exact_json.exists():
            logger.warning(
                "as_of=%s lags expected=%s by %d TD; reusing existing %s (will not overwrite older regress)",
                as_of, expected, lag_days, exact_json.name,
            )
            try:
                reused = json.loads(exact_json.read_text(encoding="utf-8"))
                q = dict(reused.get("quality") or {})
                q.update({
                    "expected_as_of": expected.isoformat(),
                    "lag_days": lag_days,
                    "stale": True,
                    "reused_artifact": True,
                    "data_last_attempt": as_of.isoformat(),
                    "incomplete_bar": bool(session.get("incomplete_bar")),
                    "actionable": False,
                    "non_actionable": True,
                    "actionability_reason": "stale_reused_prior_artifact",
                    "session_reason": session.get("actionability_reason"),
                })
                reused["quality"] = q
                reused["run_date"] = dt.date.today().isoformat()
                exact_json.write_text(json.dumps(reused, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if exact_md.exists():
                    md_old = exact_md.read_text(encoding="utf-8")
                    banner = (
                        f"> ⚠️ **stale reuse**：本次拉取仅到 {as_of}，已复用 expected={expected} 产物；"
                        f"lag_days={lag_days}。\n\n"
                    )
                    if "stale reuse" not in md_old:
                        exact_md.write_text(banner + md_old, encoding="utf-8")
                print(
                    f"OK as_of={expected} (reused; fetch_lag={as_of} lag_td={lag_days}) "
                    f"-> {exact_json.name}"
                )
                return 0
            except Exception as exc:  # noqa: BLE001
                logger.warning("reuse expected artifact failed: %s; will write stale panel", exc)
        else:
            logger.warning(
                "as_of=%s lags expected=%s by %d TD and no %s; writing stale panel",
                as_of, expected, lag_days, exact_json.name,
            )

    ts = pd.Timestamp(as_of)
    df_close = df_close[df_close.index <= ts]
    df_vol = df_vol[df_vol.index <= ts]
    if df_close.empty:
        logger.error("截断到 as_of=%s 后无数据，拒绝写出。", as_of)
        return 2

    c_qqq = df_close[QQQ_CODE]
    calendar_stale_days = (dt.date.today() - as_of).days
    if calendar_stale_days > 4:
        logger.warning("数据偏旧：最新交易日 %s 距今天 %d 天（可能遇休市或数据未更新）", as_of, calendar_stale_days)

    # ---- 构建每个主题的 idx(价格序列) / dv(美元成交额序列) / breadth(系列) ----
    theme_series: Dict[str, dict] = {}
    for key in THEME_KEYS:
        if key in ETF_MAP:
            code = ETF_MAP[key]
            idx = df_close[code]
            dv = df_vol[code] * idx
            breadth = breadth_etf(idx)
        else:
            members = [df_close[c] for c in BASKETS[key]]
            vols = [df_vol[c] for c in BASKETS[key]]
            ret, _ = avg_ret5(members)
            idx = chain_index(ret)
            dv = dvol_avg(members, vols)
            breadth = breadth_basket(members)
        theme_series[key] = {"idx": idx, "dv": dv, "breadth": breadth}

    # ---- 计算每主题完整指标 ----
    theme_metrics: Dict[str, dict] = {}
    for key in THEME_KEYS:
        ts = theme_series[key]
        m = calc_metrics_full(ts["idx"], ts["dv"], c_qqq)
        m["breadth"] = ts["breadth"]
        br = ts["breadth"].reindex(m["score"].index)
        br_score = pd.Series(0.0, index=m["score"].index)
        br_score = br_score.mask((br >= 50.0).fillna(False), 8.0)
        br_score = br_score.mask((br >= 70.0).fillna(False), 15.0)
        m["br_score"] = br_score
        # 末值标量：Pine raw = trend+flow+vol+crowd+br；calc_metrics_full 的 score 不含 br，必须在此补回
        # 末值标量：Pine raw = trend+flow+vol+crowd+br；calc_metrics_full 的 score 不含 br，必须在此补回（保持 Series 以便 last_of 取末值）
        m["base_score"] = m["score"] + br_score
        theme_metrics[key] = m

    # ---- 主题标量 ----
    def s(key, field):
        return sval(last_of(theme_metrics[key][field]))

    sc: Dict[str, dict] = {}
    for key in THEME_KEYS:
        m = theme_metrics[key]
        rel5 = s(key, "rel5"); rel20 = s(key, "rel20"); rel60 = s(key, "rel60")
        abs5 = s(key, "abs5"); rel1 = s(key, "rel1")
        vol_r = s(key, "vol_r"); breadth = s(key, "breadth")
        base = s(key, "base_score"); over = bool(last_of(m["over_ext"]))
        dist = bool(last_of(m["distribution"])); beta = bool(last_of(m["beta_lift"]))
        trend = int(last_of(m["trend_code"]))
        accel = rel_accel(rel5, rel20)
        sc[key] = dict(rel1=rel1, rel5=rel5, rel20=rel20, rel60=rel60, abs5=abs5,
                       vol_r=vol_r, breadth=breadth, base_score=base, over_ext=over,
                       distribution=dist, beta_lift=beta, trend_code=trend, accel=accel)

    # ---- 跨主题比价加速度（用于归因）----
    def ratio_series(a_key, b_key):
        return safe_div(theme_series[a_key]["idx"], theme_series[b_key]["idx"])

    soft_semi5 = sval(last_of(pct_change_n(ratio_series("soft", "semi"), 5)))
    soft_semi20 = sval(last_of(pct_change_n(ratio_series("soft", "semi"), 20)))
    soft_semi_accel = rel_accel(soft_semi5, soft_semi20)
    app_soft5 = sval(last_of(pct_change_n(ratio_series("app", "soft"), 5)))
    app_soft20 = sval(last_of(pct_change_n(ratio_series("app", "soft"), 20)))
    app_soft_accel = rel_accel(app_soft5, app_soft20)
    opt_semi5 = sval(last_of(pct_change_n(ratio_series("opt", "semi"), 5)))
    opt_semi20 = sval(last_of(pct_change_n(ratio_series("opt", "semi"), 20)))
    opt_semi_accel = rel_accel(opt_semi5, opt_semi20)
    net_semi5 = sval(last_of(pct_change_n(ratio_series("net", "semi"), 5)))
    net_semi20 = sval(last_of(pct_change_n(ratio_series("net", "semi"), 20)))
    net_semi_accel = rel_accel(net_semi5, net_semi20)
    mem_semi5 = sval(last_of(pct_change_n(ratio_series("mem", "semi"), 5)))
    mem_semi20 = sval(last_of(pct_change_n(ratio_series("mem", "semi"), 20)))
    mem_semi_accel = rel_accel(mem_semi5, mem_semi20)
    infra_semi5 = sval(last_of(pct_change_n(ratio_series("infra", "semi"), 5)))
    infra_semi20 = sval(last_of(pct_change_n(ratio_series("infra", "semi"), 20)))
    infra_semi_accel = rel_accel(infra_semi5, infra_semi20)
    hyp_hw5 = sval(last_of(pct_change_n(ratio_series("hyp", "hw"), 5)))
    hyp_hw20 = sval(last_of(pct_change_n(ratio_series("hyp", "hw"), 20)))
    hyp_hw_accel = rel_accel(hyp_hw5, hyp_hw20)

    # ---- 归因剧本 ----
    semi_accel = sc["semi"]["accel"]; hw_accel = sc["hw"]["accel"]
    soft_accel = sc["soft"]["accel"]
    pair_raw = (score_accel(neg_part(semi_accel), 3.0, 25.0)
                + score_accel(neg_part(hw_accel), 3.0, 20.0)
                + score_accel(pos_part(soft_accel), 3.0, 25.0)
                + score_accel(pos_part(soft_semi_accel), 2.0, 20.0)
                + (10.0 if (sc["soft"]["rel5"] is not None and sc["semi"]["rel5"] is not None and sc["soft"]["rel5"] > sc["semi"]["rel5"]) else 0.0)
                + (5.0 if (sc["soft"]["rel5"] is not None and sc["soft"]["rel5"] > 0) else 0.0))
    pair_score = cap100(pair_raw)

    ai_app_raw = (score_accel(pos_part(app_soft_accel), 2.0, 35.0)
                  + (15.0 if (sc["app"]["rel5"] is not None and sc["soft"]["rel5"] is not None and sc["app"]["rel5"] > sc["soft"]["rel5"]) else 0.0)
                  + (20.0 if (sc["app"]["base_score"] is not None and not np.isnan(sc["app"]["base_score"]) and sc["app"]["base_score"] >= 60) else 0.0)
                  + (15.0 if (sc["soft"]["rel5"] is not None and np.isnan(sc["soft"]["rel5"]) == False and sc["soft"]["rel5"] < 0 and sc["app"]["rel5"] is not None and not np.isnan(sc["app"]["rel5"]) and sc["app"]["rel5"] > 0) else 0.0))
    ai_app_score = cap100(ai_app_raw)

    def _diff_score(seg_accel, rel5, base_score, breadth, semi_over, hw_over):
        raw = (score_accel(pos_part(seg_accel), 2.0, 30.0)
               + (15.0 if (rel5 is not None and not np.isnan(rel5) and rel5 > 0) else 0.0)
               + (20.0 if (base_score is not None and not np.isnan(base_score) and base_score >= 60) else 0.0)
               + (15.0 if (breadth is not None and not np.isnan(breadth) and breadth >= 70) else (8.0 if (breadth is not None and not np.isnan(breadth) and breadth >= 50) else 0.0))
               + (10.0 if semi_over or hw_over else 0.0))
        return cap100(raw)

    semi_over = sc["semi"]["over_ext"]; hw_over = sc["hw"]["over_ext"]
    mem_score = _diff_score(mem_semi_accel, sc["mem"]["rel5"], sc["mem"]["base_score"], sc["mem"]["breadth"], semi_over, hw_over)
    net_score = _diff_score(net_semi_accel, sc["net"]["rel5"], sc["net"]["base_score"], sc["net"]["breadth"], semi_over, hw_over)
    opt_score = _diff_score(opt_semi_accel, sc["opt"]["rel5"], sc["opt"]["base_score"], sc["opt"]["breadth"], semi_over, hw_over)
    infra_score = _diff_score(infra_semi_accel, sc["infra"]["rel5"], sc["infra"]["base_score"], sc["infra"]["breadth"], semi_over, hw_over)
    diffusion_score = max(mem_score, net_score, opt_score, infra_score)

    crowd_unwind_raw = ((25.0 if (semi_over or hw_over) else 0.0)
                        + score_accel(neg_part(semi_accel), 3.0, 25.0)
                        + score_accel(neg_part(hw_accel), 3.0, 25.0)
                        + (15.0 if (sc["semi"]["rel5"] is not None and not np.isnan(sc["semi"]["rel5"]) and sc["semi"]["rel5"] < 0) or (sc["hw"]["rel5"] is not None and not np.isnan(sc["hw"]["rel5"]) and sc["hw"]["rel5"] < 0) else 0.0)
                        + (15.0 if sc["semi"]["distribution"] or sc["hw"]["distribution"] else 0.0))
    crowd_unwind_score = cap100(crowd_unwind_raw)

    buyer_raw = (score_accel(pos_part(hyp_hw_accel), 2.0, 35.0)
                 + score_accel(pos_part(sc["hyp"]["accel"]), 3.0, 20.0)
                 + score_accel(neg_part(hw_accel), 3.0, 15.0)
                 + (15.0 if (sc["hyp"]["rel5"] is not None and sc["hw"]["rel5"] is not None and sc["hyp"]["rel5"] > sc["hw"]["rel5"]) else 0.0)
                 + (15.0 if (sc["hyp"]["base_score"] is not None and not np.isnan(sc["hyp"]["base_score"]) and sc["hyp"]["base_score"] >= 60) else 0.0))
    buyer_score = cap100(buyer_raw)

    weak_count = sum(1 for k in THEME_KEYS if (sc[k]["rel5"] is not None and not np.isnan(sc[k]["rel5"]) and sc[k]["rel5"] < 0))
    abs_down_count = sum(1 for k in THEME_KEYS if (sc[k]["abs5"] is not None and not np.isnan(sc[k]["abs5"]) and sc[k]["abs5"] < 0))

    de_risk_raw = ((35.0 if weak_count >= 10 else (22.0 if weak_count >= 8 else (10.0 if weak_count >= 6 else 0.0)))
                   + (25.0 if abs_down_count >= 10 else (15.0 if abs_down_count >= 8 else 0.0))
                   + (10.0 if (sc["cyber"]["rel5"] is not None and not np.isnan(sc["cyber"]["rel5"]) and sc["semi"]["rel5"] is not None and not np.isnan(sc["semi"]["rel5"]) and sc["hw"]["rel5"] is not None and not np.isnan(sc["hw"]["rel5"]) and sc["cyber"]["rel5"] > sc["semi"]["rel5"] and sc["cyber"]["rel5"] > sc["hw"]["rel5"]) else 0.0)
                   + (5.0 if (sc["hyp"]["rel5"] is not None and not np.isnan(sc["hyp"]["rel5"]) and sc["hw"]["rel5"] is not None and not np.isnan(sc["hw"]["rel5"]) and sc["hyp"]["rel5"] > sc["hw"]["rel5"]) else 0.0))
    de_risk_score = cap100(de_risk_raw)

    # 月/季末再平衡
    mo = as_of.month; dom = as_of.day
    is_month_turn = dom >= 24 or dom <= 3
    is_quarter_turn = ((mo in (3, 6, 9, 12)) and dom >= 20) or ((mo in (1, 4, 7, 10)) and dom <= 3)
    winner_keys = ["semi", "hw", "mem", "net", "opt", "infra"]
    laggard_keys = ["soft", "cloud", "cyber", "robot", "hyp"]
    winner_decel = sum(1 for k in winner_keys if (sc[k]["rel20"] is not None and not np.isnan(sc[k]["rel20"]) and sc[k]["accel"] is not None and not np.isnan(sc[k]["accel"]) and sc[k]["rel20"] > 5 and sc[k]["accel"] < 0))
    laggard_recovery = sum(1 for k in laggard_keys if (sc[k]["rel20"] is not None and not np.isnan(sc[k]["rel20"]) and sc[k]["accel"] is not None and not np.isnan(sc[k]["accel"]) and sc[k]["rel20"] < 2 and sc[k]["accel"] > 0))
    rebal_raw = (20.0 if is_month_turn else 0.0) + (25.0 if is_quarter_turn else 0.0)
    rebal_raw += (25.0 if winner_decel >= 3 else (18.0 if winner_decel == 2 else (10.0 if winner_decel == 1 else 0.0)))
    rebal_raw += (25.0 if laggard_recovery >= 3 else (18.0 if laggard_recovery == 2 else (10.0 if laggard_recovery == 1 else 0.0)))
    rebal_raw += (10.0 if pair_score >= 45 else 0.0)
    rebalance_score = cap100(rebal_raw)

    # ---- 联动加分 ----
    def boost_score(base, boost):
        return cap100(base + boost) if base is not None and not np.isnan(base) else np.nan

    def _b(val, hi, lo):
        return (hi if val >= 60 else (lo if val >= 45 else 0.0))

    boosts = {
        "semi": 0.0,
        "soft": _b(pair_score, 10, 5),
        "cloud": 0.0,
        "cyber": 10.0 if de_risk_score >= 60 else 0.0,
        "robot": 0.0,
        "ai": _b(ai_app_score, 8, 0) if ai_app_score >= 60 else (6.0 if diffusion_score >= 60 else (_b(diffusion_score, 6, 3) if diffusion_score >= 45 else 0.0)),
        "app": _b(ai_app_score, 12, 6),
        "opt": _b(opt_score, 10, 5),
        "net": _b(net_score, 10, 5),
        "mem": _b(mem_score, 12, 6),
        "infra": _b(infra_score, 10, 5),
        "hyp": _b(buyer_score, 10, 5),
        "hw": -8.0 if crowd_unwind_score >= 60 else (-4.0 if crowd_unwind_score >= 45 else 0.0),
    }

    final: Dict[str, dict] = {}
    for idv, key, name, proxy in THEME_ORDER:
        s_ = sc[key]
        score = boost_score(s_["base_score"], boosts[key])
        state = state_from_score(score, s_["over_ext"], s_["distribution"], s_["rel5"], s_["rel20"])
        seg = {"opt": opt_score, "net": net_score, "mem": mem_score, "infra": infra_score}.get(key, 0.0)
        reason = theme_reason(idv, state, score, s_["rel5"], s_["rel20"], s_["abs5"], s_["beta_lift"],
                              de_risk_score, pair_score, ai_app_score,
                              opt_score, net_score, mem_score, infra_score, buyer_score, rebalance_score, crowd_unwind_score)
        final[key] = dict(id=idv, name=name, proxy=proxy, score=score, state=state, reason=reason,
                          rel5=s_["rel5"], rel20=s_["rel20"], rel60=s_["rel60"], abs5=s_["abs5"],
                          vol_r=s_["vol_r"], breadth=s_["breadth"], trend_code=s_["trend_code"],
                          base_score=s_["base_score"])

    # ---- 整体状态（派生，仅用于汇报标题）----
    valid_scores = [final[k]["score"] for k in THEME_KEYS if final[k]["score"] is not None and not np.isnan(final[k]["score"])]
    avg_score = float(np.mean(valid_scores)) if valid_scores else np.nan
    n_confirm = sum(1 for k in THEME_KEYS if final[k]["state"] == 2)
    n_crowd = sum(1 for k in THEME_KEYS if final[k]["state"] == 3)
    n_withdraw = sum(1 for k in THEME_KEYS if final[k]["state"] == -1)
    n_neutral = sum(1 for k in THEME_KEYS if final[k]["state"] == 0)
    if avg_score >= 70:
        overall = "强势/确认轮动区"
    elif avg_score >= 60:
        overall = "偏强/资金回流区"
    elif avg_score >= 50:
        overall = "中性观察/分化防御区"
    else:
        overall = "偏弱/资金撤出区"

    # ---- 所选主题 ----
    sel_key = args.theme if args.theme in THEME_KEYS else DEFAULT_SELECTED
    sel = final[sel_key]
    selected_score = sel["score"]

    # ===================== 输出 =====================
    expired = dt.date.today() > EXPIRY
    days_left = max(0, (EXPIRY - dt.date.today()).days)

    themes_out = []
    for idv, key, name, proxy in THEME_ORDER:
        t = final[key]
        themes_out.append({
            "id": idv, "name": name, "proxy": proxy,
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
        {"name": "软件/半导体 强弱对调", "score": round(float(pair_score), 2), "hint": "强的减速 + 弱的回血"},
        {"name": "AI应用追赶", "score": round(float(ai_app_score), 2), "hint": "应用对软件提速: " + fmt_pct(app_soft_accel)},
        {"name": "储存扩散", "score": round(float(mem_score), 2), "hint": "储存对半导体提速: " + fmt_pct(mem_semi_accel)},
        {"name": "AI网络扩散", "score": round(float(net_score), 2), "hint": "网络对半导体提速: " + fmt_pct(net_semi_accel)},
        {"name": "光通信扩散", "score": round(float(opt_score), 2), "hint": "光通信对半导体提速: " + fmt_pct(opt_semi_accel)},
        {"name": "电力散热扩散", "score": round(float(infra_score), 2), "hint": "基建对半导体提速: " + fmt_pct(infra_semi_accel)},
        {"name": "月/季末再平衡", "score": round(float(rebalance_score), 2), "hint": f"强者减速 {winner_decel} 个 / 弱者回血 {laggard_recovery} 个"},
        {"name": "硬件拥挤出清", "score": round(float(crowd_unwind_score), 2), "hint": "半导体和硬件在减速"},
        {"name": "云巨头接棒", "score": round(float(buyer_score), 2), "hint": "买方对卖方提速: " + fmt_pct(hyp_hw_accel)},
        {"name": "科技内部降风险", "score": round(float(de_risk_score), 2), "hint": f"转弱主题: {weak_count}/13"},
    ]

    quality = {
        "as_of": as_of.isoformat(),
        "expected_as_of": expected.isoformat(),
        "lag_days": lag_days,
        "stale": bool(lag_days >= 1 or from_cache or session.get("incomplete_bar")),
        "incomplete_bar": bool(session.get("incomplete_bar")),
        # Date alignment gates actionability; cache alone is a freshness warning (stale) not a date miss.
        "actionable": bool(session.get("actionable")) and lag_days == 0 and not bool(session.get("incomplete_bar")),
        "non_actionable": not (bool(session.get("actionable")) and lag_days == 0 and not bool(session.get("incomplete_bar"))),
        "actionability_reason": (
            "stale_lag" if lag_days >= 1
            else ("cache_fallback" if from_cache and session.get("actionable") else session.get("actionability_reason"))
        ),
        "reused_artifact": reused_artifact,
        "session_reason": session.get("actionability_reason"),
        "now_ny": session.get("now_ny"),
    }
    payload = {
        "as_of": as_of.isoformat(),
        "run_date": dt.date.today().isoformat(),
        "version": VER_TXT,
        "expired": expired,
        "days_left": days_left,
        "selected_theme": sel["name"],
        "selected_theme_score": None if (selected_score is not None and np.isnan(selected_score)) else round(float(selected_score), 2),
        "overall": {"status": overall, "avg_score": None if np.isnan(avg_score) else round(avg_score, 2),
                    "confirm": n_confirm, "crowd": n_crowd, "neutral": n_neutral, "withdraw": n_withdraw},
        "themes": themes_out,
        "attribution": attribution,
        "counts": {"weak": weak_count, "abs_down": abs_down_count},
        "data_source": {"from_cache": from_cache, "cache_used": bool(from_cache), "missing": missing},
        "quality": quality,
    }

    json_path = report_dir / f"tech_rotation_{as_of.isoformat()}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s", json_path)
    md = build_markdown(payload, days_left, expired)
    md_path = report_dir / f"tech_rotation_{as_of.isoformat()}.md"
    md_path.write_text(md, encoding="utf-8")
    logger.info("wrote %s", md_path)
    print(
        f"OK as_of={as_of} expected={expected} lag_td={lag_days} stale={quality['stale']} "
        f"actionable={quality['actionable']} selected={sel['name']}({selected_score:.0f}) "
        f"avg={avg_score:.1f} themes={len(themes_out)} -> {md_path.name}"
    )
    return 0


def build_markdown(p: dict, days_left: int, expired: bool) -> str:
    L = []
    ov = p["overall"]
    L.append(f"# 科技板块资金轮动观测机 v2.6.1r — 每日快照（headless 复刻）")
    L.append("")
    L.append(f"- 版本：{p['version']}（本地日频复刻，配方/阈值与 TV Pine 原版逐字一致）")
    q = p.get("quality") or {}
    exp = q.get("expected_as_of") or p.get("as_of")
    lag = q.get("lag_days")
    L.append(
        f"- 生成：{p['run_date']} · 数据锚点：{p['as_of']}"
        f" · expected：{exp} · lag_days：{lag if lag is not None else '—'}"
        f" · stale：{'是' if q.get('stale') else '否'}"
        f" · actionable：{'是' if q.get('actionable') else '否'}"
    )
    if q.get("non_actionable") or q.get("stale"):
        L.append(
            f"- ⚠️ **non_actionable/stale**：reason=`{q.get('actionability_reason')}`；"
            "勿把陈旧 as_of 当最新可执行读数。"
        )
    if expired:
        L.append(f"- ⚠️ **已过期**（有效期至 {EXP_TXT}）。")
    elif days_left <= 14:
        L.append(f"- ⚠️ 距脚本到期 {days_left} 天（{EXP_TXT}）。")
    _ds = p.get("data_source") or {}
    if _ds.get("from_cache"):
        L.append(f"- ⚠️ **数据回退**：以下标的联网拉取失败，已用本地缓存（非实时）—— {', '.join(_ds['from_cache'])}")
    if _ds.get("missing"):
        L.append(f"- ⚠️ **缺失标的**：{', '.join(_ds['missing'])}（已跳过，相关篮子广度/成交额可能偏低）")
    L.append(f"- **状态**：{ov['status']}（主题均分 ≈ {ov['avg_score']}；确认进入 {ov['confirm']} · 拥挤主升 {ov['crowd']} · 中性观察 {ov['neutral']} · 资金撤出 {ov['withdraw']}）")
    L.append("")
    L.append("> 本工具为教学与数据参考工具，全部输出仅为对公开市场数据的统计描述，不构成投资建议。")
    L.append("")

    # 一、主题仪表盘
    L.append("## 一、主题仪表盘（13 主题，按分数降序）")
    L.append("")
    L.append("| 主题 | 代理 | 5D相对 | 20D相对 | 60D相对 | 绝对5D | 趋势 | 成交额 | 广度 | 分数/状态 | 可能原因 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for t in sorted(p["themes"], key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True):
        L.append(
            f"| {t['name']} | {t['proxy']} | {fmt_pct(t['rel5_pct'])} | {fmt_pct(t['rel20_pct'])} | {fmt_pct(t['rel60_pct'])} | {fmt_pct(t['abs5_pct'])} | {t['trend']} | {fmt_num(t['vol_ratio'])}x | {fmt_pct(t['breadth_pct'])} | {fmt_score(t['score'])} / {t['state']} | {t['reason']} |"
        )
    L.append("")
    c = p["counts"]
    L.append(f"**广度统计**：确认进入 {ov['confirm']} · 拥挤主升 {ov['crowd']} · 中性观察 {ov['neutral']} · 资金撤出 {ov['withdraw']}。")
    L.append("")

    # 二、归因面板 Top 6
    L.append("## 二、归因面板 — 重点信号（Top 6）")
    L.append("")
    L.append("| 归因信号 | 分数 | 核心信号 |")
    L.append("|---|---|---|")
    for a in sorted(p["attribution"], key=lambda x: x["score"], reverse=True)[:6]:
        L.append(f"| {a['name']} | {fmt_score(a['score'])} | {a['hint']} |")
    L.append("")
    rest = sorted(p["attribution"], key=lambda x: x["score"], reverse=True)[6:]
    if rest:
        L.append("次信号：" + "；".join(f"{a['name']} {fmt_score(a['score'])}（{a['hint']}）" for a in rest) + "。")
    L.append("")

    # 三、所选主题
    L.append("## 三、所选主题轮动分数 / Selected Rotation Score")
    L.append("")
    L.append(f"- **所选主题**：{p['selected_theme']}（Pine input 默认「半导体」）")
    L.append(f"- **所选主题轮动分数**：**{fmt_score(p['selected_theme_score'])}** — 与仪表盘「{p['selected_theme']}」行分数一致（口径已校验，即该主题行分数本身）。")
    ss = next(t for t in p["themes"] if t["name"] == p["selected_theme"])
    L.append(f"- **状态**：{ss['state']} — {ss['rel5_pct']} 5D相对 / {ss['rel60_pct']} 60D相对。")
    L.append("")
    L.append("### 一句话结论")
    L.append(f"整体{ov['status']}：主题均分 ≈ {ov['avg_score']}；所选主题 {p['selected_theme']} {fmt_score(p['selected_theme_score'])} 为"
             + ("落后腿" if (p['selected_theme_score'] is not None and ov['avg_score'] is not None and p['selected_theme_score'] < ov['avg_score']) else "领先腿")
             + "，不宜视作全局轮动强度。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*仅供信息参考与教学演示，不构成投资建议，据此操作风险自负。*")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
