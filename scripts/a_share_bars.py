# -*- coding: utf-8 -*-
"""A 股 ETF 日线补位（2026-11-14）：tech-rotation 对A结构标签与 calibration 对A列共用。

Yahoo 的 159915.SZ / 510300.SS 常把最新交易日留成 NaN（次日才补），直接用会晚一天。
这里提供 Sina 日 K（未复权收盘）读取、按「最新日期 ≤ as_of 者优先、同日 yfinance 优先」挑来源，
以及只追加缺失日期的缓存补位。失败返回 None，不编造。
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from typing import Callable, Optional, Tuple

import pandas as pd

SINA_SYMBOL = {"159915.SZ": "sz159915", "510300.SS": "sh510300"}
SINA_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}")


def sina_rows_to_series(rows) -> Tuple[Optional[pd.Series], Optional[str]]:
    if not rows:
        return None, "sina empty"
    try:
        s = pd.Series({pd.Timestamp(r["day"]).normalize(): float(r["close"]) for r in rows}).sort_index()
    except Exception as exc:  # noqa: BLE001
        return None, f"sina parse {exc}"
    s = s[s > 0]
    return (s, None) if len(s) else (None, "sina empty")


def sina_daily(symbol: str, datalen: int = 40, get: Optional[Callable[[str], str]] = None):
    """symbol 可传 Sina 代码（sz159915）或 Yahoo 代码（159915.SZ）。"""
    sym = SINA_SYMBOL.get(symbol, symbol)
    url = SINA_URL.format(sym=sym, n=int(datalen))
    try:
        if get is None:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read().decode("utf-8", "replace")
        else:
            text = get(url)
        rows = json.loads(text or "[]")
    except Exception as exc:  # noqa: BLE001
        return None, f"sina {exc}"
    return sina_rows_to_series(rows)


def _clip(s: Optional[pd.Series], as_of: dt.date) -> Optional[pd.Series]:
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).normalize()
    s = s[s.index.date <= as_of]
    return s if len(s) >= 2 else None


def pick_latest(yf_series: Optional[pd.Series], sina_series: Optional[pd.Series], as_of: dt.date
                ) -> Tuple[Optional[pd.Series], Optional[str]]:
    """返回 (series, source)：最新日期 ≤ as_of 的那个来源；同日优先 yfinance（复权）。"""
    cands = []
    for prio, src, ser in ((1, "yfinance", _clip(yf_series, as_of)), (0, "sina", _clip(sina_series, as_of))):
        if ser is not None:
            cands.append((ser.index[-1], prio, src, ser))
    if not cands:
        return None, None
    cands.sort(key=lambda c: (c[0], c[1]))
    best = cands[-1]
    return best[3], best[2]


def append_missing(cache: Optional[pd.Series], fresh: Optional[pd.Series], through: dt.date
                   ) -> Tuple[Optional[pd.Series], list]:
    """只追加 cache 里没有、且 ≤ through 的日期；不改旧行。返回 (merged, 追加的日期列表)。"""
    if fresh is None or not len(fresh):
        return cache, []
    f = pd.to_numeric(fresh, errors="coerce").dropna()
    f.index = pd.to_datetime(f.index).normalize()
    f = f[f.index.date <= through]
    if cache is None or not len(cache):
        return f, [d.date() for d in f.index]
    c = cache.copy()
    c.index = pd.to_datetime(c.index).normalize()
    last = c.index.max()
    new = f[(f.index > last)]
    if not len(new):
        return c, []
    return pd.concat([c, new]).sort_index(), [d.date() for d in new.index]
