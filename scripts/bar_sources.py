#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incremental daily bars with a long rate-limit backoff and two fallbacks.

Order when a symbol is missing recent completed bars:
  1. yfinance (only the recent window if history is already cached)
  2. TradingView MCP daily OHLCV (mcp-tv-get-ohlcv shape)
  3. Stooq public CSV (no API key)

429 / rate-limit on yfinance sleeps 30s, 60s, then 120s. It does not retry
within a few seconds. After one symbol exhausts that schedule, later symbols
in the same process skip yfinance instead of blocking the report.

TradingView is a single attempt. A 429, auth failure, or missing bridge is
recorded and the caller continues (Stooq, then the existing cache).

Merge rule: dates strictly before the last completed US cash session are never
replaced. Only missing dates and that one completed-session bar are written.
Intraday partial bars (session date after the completed session) are dropped.
This module does not score and does not touch the composite min.
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("bar-sources")

RATE_LIMIT_BACKOFFS: Tuple[float, ...] = (30.0, 60.0, 120.0)
EMPTY_RETRY_SEC = 30.0
TV_TIMEOUT_SEC = 20
STOOQ_TIMEOUT_SEC = 25

_NY = ZoneInfo("America/New_York")
_US_CASH_CLOSE_HOUR = 16
# Same lightweight NYSE holiday proxy the daily reports already use.
_US_FED_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25),
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
}

# yfinance ticker -> TradingView EXCHANGE:TICKER (mcp-tv-get-ohlcv `symbol`)
TV_SYMBOLS: Dict[str, str] = {
    "SPY": "AMEX:SPY", "QQQ": "NASDAQ:QQQ", "IWM": "AMEX:IWM", "RSP": "AMEX:RSP",
    "XLK": "AMEX:XLK", "XLF": "AMEX:XLF", "XLV": "AMEX:XLV", "XLY": "AMEX:XLY",
    "XLP": "AMEX:XLP", "XLE": "AMEX:XLE", "XLI": "AMEX:XLI", "XLB": "AMEX:XLB",
    "XLU": "AMEX:XLU", "XLRE": "AMEX:XLRE", "XLC": "AMEX:XLC",
    "TLT": "NASDAQ:TLT", "HYG": "AMEX:HYG", "LQD": "AMEX:LQD", "GLD": "AMEX:GLD",
    "KRE": "AMEX:KRE", "TIP": "AMEX:TIP", "UUP": "AMEX:UUP", "FXY": "AMEX:FXY",
    "SOXX": "NASDAQ:SOXX", "SMH": "NASDAQ:SMH", "IGV": "BATS:IGV", "SKYY": "NASDAQ:SKYY",
    "CIBR": "NASDAQ:CIBR", "BOTZ": "NASDAQ:BOTZ", "AIQ": "NASDAQ:AIQ",
    "PLTR": "NASDAQ:PLTR", "APP": "NASDAQ:APP", "SNOW": "NYSE:SNOW",
    "DDOG": "NASDAQ:DDOG", "MDB": "NASDAQ:MDB", "COHR": "NYSE:COHR",
    "LITE": "NASDAQ:LITE", "AAOI": "NASDAQ:AAOI", "ANET": "NYSE:ANET",
    "CIEN": "NYSE:CIEN", "CSCO": "NASDAQ:CSCO", "MU": "NASDAQ:MU",
    "WDC": "NASDAQ:WDC", "STX": "NASDAQ:STX", "SNDK": "NASDAQ:SNDK",
    "VRT": "NYSE:VRT", "ETN": "NYSE:ETN", "PWR": "NYSE:PWR",
    "CEG": "NASDAQ:CEG", "GEV": "NYSE:GEV", "MSFT": "NASDAQ:MSFT",
    "AMZN": "NASDAQ:AMZN", "GOOGL": "NASDAQ:GOOGL", "META": "NASDAQ:META",
    "NVDA": "NASDAQ:NVDA", "AVGO": "NASDAQ:AVGO", "AMD": "NASDAQ:AMD",
    "MRVL": "NASDAQ:MRVL",
    "^GSPC": "SP:SPX", "^VIX": "CBOE:VIX", "^TNX": "TVC:US10Y",
    "^TYX": "TVC:US30Y", "^IRX": "TVC:US03M", "VIX3M": "CBOE:VIX3M",
    "DX-Y.NYB": "TVC:DXY", "EURUSD=X": "FX:EURUSD", "JPY=X": "FX:USDJPY",
    "BTC-USD": "BITSTAMP:BTCUSD", "CL=F": "NYMEX:CL1!", "HG=F": "COMEX:HG1!",
    "GC=F": "COMEX:GC1!", "ES=F": "CME_MINI:ES1!", "NQ=F": "CME_MINI:NQ1!",
    "ZN=F": "CBOT:ZN1!", "UB=F": "CBOT:UB1!",
}

# yfinance ticker -> Stooq public CSV symbol. None means "do not guess".
STOOQ_SYMBOLS: Dict[str, Optional[str]] = {
    "^GSPC": "^spx", "^VIX": "^vix", "^TNX": "^tnx", "^TYX": "^tyx",
    "^IRX": "^irx", "VIX3M": None,
    "DX-Y.NYB": "dx.f", "EURUSD=X": "eurusd", "JPY=X": "usdjpy",
    "BTC-USD": "btcusd", "CL=F": "cl.f", "HG=F": "hg.f", "GC=F": "gc.f",
    "ES=F": "es.f", "NQ=F": "nq.f", "ZN=F": "zn.f", "UB=F": "ub.f",
}


class _Circuit:
    tripped: bool = False
    reason: str = ""


def reset_yf_circuit() -> None:
    _Circuit.tripped = False
    _Circuit.reason = ""


def yf_circuit_reason() -> str:
    return _Circuit.reason


def is_us_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _US_FED_HOLIDAYS


def prior_us_business_day(d: date) -> date:
    x = d - timedelta(days=1)
    while not is_us_business_day(x):
        x -= timedelta(days=1)
    return x


def completed_us_cash_session(now: Optional[datetime] = None) -> date:
    """Last completed US cash session. Before 16:00 ET, that is the prior business day."""
    now_ny = now.astimezone(_NY) if now is not None else datetime.now(_NY)
    today = now_ny.date()
    if now_ny.hour >= _US_CASH_CLOSE_HOUR and is_us_business_day(today):
        return today
    return prior_us_business_day(today)


def is_rate_limited(exc: BaseException) -> bool:
    if type(exc).__name__ == "YFRateLimitError":
        return True
    msg = str(exc).lower()
    return any(tok in msg for tok in ("429", "rate limit", "too many requests", "ratelimit"))


def rate_limit_delay(attempt_idx: int) -> float:
    """attempt 0 -> 30s, 1 -> 60s, 2 -> 120s, then stay at 120s."""
    if attempt_idx < 0:
        attempt_idx = 0
    if attempt_idx >= len(RATE_LIMIT_BACKOFFS):
        return RATE_LIMIT_BACKOFFS[-1]
    return RATE_LIMIT_BACKOFFS[attempt_idx]


def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if "Close" not in out.columns:
        raise ValueError("Close column missing")
    out["Close"] = pd.to_numeric(out["Close"], errors="coerce")
    if "Volume" not in out.columns:
        out["Volume"] = float("nan")
    else:
        out["Volume"] = pd.to_numeric(out["Volume"], errors="coerce")
    out = out[out["Close"].notna()]
    return out[["Close", "Volume"]]


def cache_covers(
    existing: Optional[pd.DataFrame],
    completed: date,
    min_bars: int,
    cached_mtime: Optional[float] = None,
) -> bool:
    """True when history contains the completed session, has no later partial, and was written after that close.

    ``cached_mtime`` is the cache file mtime (epoch seconds). When omitted, only the
    dates are checked (unit tests). A file written before 16:00 ET on the completed
    session still holds an intraday print and must be refreshed.
    """
    if existing is None or len(existing) < min_bars:
        return False
    dates = [pd.Timestamp(ts).date() for ts in existing.index]
    if completed not in dates or any(d > completed for d in dates):
        return False
    if cached_mtime is None:
        return True
    close_dt = datetime(completed.year, completed.month, completed.day, _US_CASH_CLOSE_HOUR, 0, tzinfo=_NY)
    mtime = datetime.fromtimestamp(cached_mtime, _NY)
    return mtime >= close_dt


def merge_bars(
    existing: Optional[pd.DataFrame],
    fresh: Optional[pd.DataFrame],
    completed: date,
) -> Optional[pd.DataFrame]:
    """Keep good history. Fill holes. Refresh only the completed-session bar. Drop partials."""
    ex = None
    if existing is not None and len(existing):
        ex = _normalize_index(existing)
    fr = None
    if fresh is not None and len(fresh):
        fr = _normalize_index(fresh)
        completed_ts = pd.Timestamp(completed)
        fr = fr[fr.index <= completed_ts]
        if fr.empty:
            fr = None
    if ex is None and fr is None:
        return None
    if fr is None:
        if ex is None:
            return None
        completed_ts = pd.Timestamp(completed)
        trimmed = ex[ex.index <= completed_ts]
        return trimmed if len(trimmed) else None
    if ex is None:
        return fr
    completed_ts = pd.Timestamp(completed)
    keep = ex[ex.index < completed_ts]
    missing = fr[(fr.index < completed_ts) & (~fr.index.isin(keep.index))]
    tail_new = fr[fr.index == completed_ts]
    tail_old = ex[ex.index == completed_ts]
    tail = tail_new if len(tail_new) else tail_old
    parts = [p for p in (keep, missing, tail) if p is not None and len(p)]
    if not parts:
        return None
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out[["Close", "Volume"]]


def ohlcv_frame_from_download(raw: Any, ticker: str) -> Optional[pd.DataFrame]:
    """Parse a yfinance download frame into Close/Volume. None if unusable."""
    if raw is None or getattr(raw, "empty", True):
        return None
    cols = raw.columns

    def _field(field: str):
        if getattr(cols, "nlevels", 1) > 1:
            if (field, ticker) in cols:
                return raw[(field, ticker)]
            if (ticker, field) in cols:
                return raw[(ticker, field)]
            if field in cols.get_level_values(0):
                cd = raw.xs(field, axis=1, level=0)
                if ticker in cd.columns:
                    return cd[ticker]
                return cd.iloc[:, 0] if not cd.empty else None
            return None
        return raw[field] if field in cols else None

    close = _field("Close")
    if close is None:
        return None
    vol = _field("Volume")
    df = pd.DataFrame({"Close": close})
    df["Volume"] = vol if vol is not None else float("nan")
    df = _normalize_index(df)
    return df if len(df) else None


def yf_download_bars(
    ticker: str,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    period: Optional[str] = None,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
    download: Optional[Callable[..., Any]] = None,
    min_rows: int = 1,
) -> Tuple[Optional[pd.DataFrame], str]:
    """Download one ticker. Rate-limit sleeps are 30/60/120. Never raises.

    `end` is exclusive, matching yfinance. A tripped circuit returns immediately.
    """
    if _Circuit.tripped:
        return None, f"yfinance skipped after earlier rate limit ({_Circuit.reason})"

    def _default_download(code: str, **kwargs: Any) -> Any:
        import yfinance as yf
        return yf.download(code, auto_adjust=True, progress=False, threads=False, **kwargs)

    do_download = download or _default_download
    n = max(1, int(attempts))
    last_reason = "no data"
    rate_hits = 0
    for i in range(n):
        kwargs: Dict[str, Any] = {}
        if start is not None:
            kwargs["start"] = start.isoformat()
            if end is not None:
                kwargs["end"] = end.isoformat()
        else:
            kwargs["period"] = period or "2y"
        try:
            raw = do_download(ticker, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_reason = str(exc)
            if is_rate_limited(exc):
                if i + 1 >= n:
                    _Circuit.tripped = True
                    _Circuit.reason = last_reason[:180]
                    return None, f"yfinance rate_limited {ticker}: {last_reason}"
                delay = rate_limit_delay(rate_hits)
                rate_hits += 1
                logger.warning(
                    "yfinance %s rate limited (attempt %d/%d), backoff %.0fs",
                    ticker, i + 1, n, delay,
                )
                sleep(delay)
                continue
            if i + 1 >= n:
                return None, f"yfinance failed {ticker}: {last_reason}"
            delay = min(8.0, 2.0 * (i + 1))
            logger.warning("yfinance %s error (attempt %d/%d), backoff %.0fs: %s", ticker, i + 1, n, delay, exc)
            sleep(delay)
            continue
        frame = ohlcv_frame_from_download(raw, ticker)
        if frame is not None and len(frame) >= min_rows:
            return frame, ""
        last_reason = "empty"
        # Empty payload is often a silent throttle. One 30s wait, not a seconds-long burst.
        if i + 1 >= n or i >= 1:
            return None, f"yfinance empty {ticker}"
        logger.warning("yfinance %s empty (attempt %d/%d), backoff %.0fs", ticker, i + 1, n, EMPTY_RETRY_SEC)
        sleep(EMPTY_RETRY_SEC)
    return None, f"yfinance failed {ticker}: {last_reason}"


def tv_symbol(ticker: str) -> Optional[str]:
    if ticker in TV_SYMBOLS:
        return TV_SYMBOLS[ticker]
    return None


def bars_payload_to_frame(payload: Any) -> Optional[pd.DataFrame]:
    """Accept mcp-tv-get-ohlcv JSON: rows [{t,o,h,l,c,v}] or columns, or a bare list."""
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    bars = payload
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return None
        bars = payload.get("bars", payload.get("data", payload))
        if isinstance(bars, dict) and "c" in bars and "t" in bars:
            rows = []
            times = bars.get("t") or []
            closes = bars.get("c") or []
            vols = bars.get("v") or []
            for i, t in enumerate(times):
                if i >= len(closes) or closes[i] is None:
                    continue
                vol = vols[i] if i < len(vols) else None
                rows.append((t, closes[i], vol))
            if not rows:
                return None
            idx = pd.to_datetime([int(t) for t, _, _ in rows], unit="s")
            df = pd.DataFrame(
                {"Close": [c for _, c, _ in rows], "Volume": [v if v is not None else float("nan") for _, _, v in rows]},
                index=idx,
            )
            return _normalize_index(df)
    if not isinstance(bars, list) or not bars:
        return None
    idx = []
    closes = []
    vols = []
    for row in bars:
        if not isinstance(row, dict):
            continue
        t = row.get("t", row.get("time"))
        c = row.get("c", row.get("close"))
        if t is None or c is None:
            continue
        idx.append(int(t))
        closes.append(c)
        v = row.get("v", row.get("volume"))
        vols.append(v if v is not None else float("nan"))
    if not closes:
        return None
    df = pd.DataFrame({"Close": closes, "Volume": vols}, index=pd.to_datetime(idx, unit="s"))
    return _normalize_index(df)


def _tv_rate_limited_text(text: str) -> bool:
    low = text.lower()
    return "429" in text or "rate limit" in low or "too many requests" in low or "ratelimit" in low


def fetch_tv_bars(
    ticker: str,
    *,
    date_from: str,
    date_to: str,
    count: int = 15,
    timeout: float = TV_TIMEOUT_SEC,
    transport: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Tuple[Optional[pd.DataFrame], str]:
    """One shot at TradingView daily OHLCV. Never retries. Never raises.

    Default transport runs ``TV_MCP_CMD`` with a JSON object on stdin matching
    mcp-tv-get-ohlcv arguments (symbol, interval=1D, count, date_from, date_to)
    and expects the tool JSON on stdout. No bridge -> recorded skip, not a hang.
    """
    symbol = tv_symbol(ticker)
    if not symbol:
        return None, f"tv unmapped {ticker}"

    payload = {
        "symbol": symbol,
        "interval": "1D",
        "count": int(count),
        "date_from": date_from,
        "date_to": date_to,
    }

    def _cmd_transport(body: Dict[str, Any]) -> Any:
        cmd = os.environ.get("TV_MCP_CMD", "").strip()
        if not cmd:
            return {
                "success": False,
                "error": "tradingview mcp bridge not configured (set TV_MCP_CMD to invoke mcp-tv-get-ohlcv)",
            }
        proc = subprocess.run(
            cmd,
            shell=True,
            input=json.dumps(body),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 429 or _tv_rate_limited_text(out + " " + err):
            return {"success": False, "error": f"rate_limited {out or err}"[:300]}
        if not out:
            return {"success": False, "error": err or f"tv mcp exit {proc.returncode}"}
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"success": False, "error": f"tv mcp bad json: {out[:180]}"}

    try:
        raw = (transport or _cmd_transport)(payload)
    except subprocess.TimeoutExpired:
        return None, f"tv mcp timeout {symbol}"
    except Exception as exc:  # noqa: BLE001
        return None, f"tv mcp failed {symbol}: {exc}"

    if isinstance(raw, dict) and raw.get("success") is False:
        err = str(raw.get("error") or raw)
        if _tv_rate_limited_text(err):
            return None, f"tv rate_limited {symbol}: {err[:180]}"
        return None, f"tv unavailable {symbol}: {err[:180]}"
    frame = bars_payload_to_frame(raw)
    if frame is None or frame.empty:
        return None, f"tv empty {symbol}"
    return frame, ""


def stooq_symbol(ticker: str) -> Optional[str]:
    if ticker in STOOQ_SYMBOLS:
        return STOOQ_SYMBOLS[ticker]
    if ticker.endswith("=F") or ticker.startswith("^") or ticker.endswith("=X") or "-" in ticker:
        return None
    if ticker.isalpha():
        return ticker.lower() + ".us"
    return None


def fetch_stooq_bars(
    ticker: str,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    timeout: float = STOOQ_TIMEOUT_SEC,
    opener: Optional[Callable[[str], str]] = None,
) -> Tuple[Optional[pd.DataFrame], str]:
    """One Stooq CSV request. No API key. No retry loop. Never raises."""
    sym = stooq_symbol(ticker)
    if not sym:
        return None, f"stooq unmapped {ticker}"
    params = {"s": sym, "i": "d"}
    if date_from:
        params["d1"] = date_from.replace("-", "")
    if date_to:
        params["d2"] = date_to.replace("-", "")
    url = "https://stooq.com/q/d/l/?" + urllib.parse.urlencode(params)

    def _default_open(u: str) -> str:
        req = urllib.request.Request(u, headers={"User-Agent": "macro-os-bar-sources/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200) or 200
            body = resp.read().decode("utf-8", "replace")
        if code == 429:
            raise urllib.error.HTTPError(u, 429, "rate limited", hdrs=None, fp=None)
        return body

    try:
        body = (opener or _default_open)(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return None, f"stooq rate_limited {sym}"
        return None, f"stooq HTTP {exc.code} {sym}"
    except Exception as exc:  # noqa: BLE001
        return None, f"stooq failed {sym}: {exc}"
    text = (body or "").lstrip("\ufeff").strip()
    if not text or text.lower().startswith("no data") or not text.lower().startswith("date,"):
        return None, f"stooq no data {sym}"
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception as exc:  # noqa: BLE001
        return None, f"stooq parse {sym}: {exc}"
    if "Date" not in df.columns or "Close" not in df.columns:
        return None, f"stooq columns {sym}"
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    if "Volume" not in df.columns:
        df["Volume"] = float("nan")
    frame = _normalize_index(df[["Close", "Volume"]])
    if frame.empty:
        return None, f"stooq empty {sym}"
    return frame, ""


def fetch_with_fallback(
    ticker: str,
    existing: Optional[pd.DataFrame],
    *,
    full_period: str = "2y",
    history_start: Optional[date] = None,
    overlap_days: int = 10,
    min_bars: int = 60,
    force_refresh: bool = False,
    cached_mtime: Optional[float] = None,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
    download: Optional[Callable[..., Any]] = None,
    tv_fetch: Optional[Callable[..., Tuple[Optional[pd.DataFrame], str]]] = None,
    stooq_fetch: Optional[Callable[..., Tuple[Optional[pd.DataFrame], str]]] = None,
    now: Optional[datetime] = None,
) -> Tuple[Optional[pd.DataFrame], List[str], str]:
    """Return (merged frame, notes, source).

    source is cache | yfinance | tradingview | stooq | cache-stale | none.
    Fallback frames only fill holes and the completed bar; they do not replace older rows.
    """
    notes: List[str] = []
    completed = completed_us_cash_session(now)
    ex = None
    if existing is not None and len(existing):
        try:
            ex = _normalize_index(existing)
            if ex.empty:
                ex = None
        except Exception as exc:  # noqa: BLE001
            notes.append(f"cache parse {ticker}: {exc}")
            ex = None

    if not force_refresh and cache_covers(ex, completed, min_bars, cached_mtime):
        logger.info("cache covers %s through %s (%d bars)", ticker, completed, len(ex))
        return ex, notes, "cache"

    need_full = ex is None or len(ex) < min_bars
    start: Optional[date] = None
    period: Optional[str] = None
    end = completed + timedelta(days=1)  # yfinance end is exclusive; excludes the partial session
    if need_full:
        if history_start is not None:
            start = history_start
        else:
            period = full_period
    else:
        last = max(pd.Timestamp(ts).date() for ts in ex.index)
        start = last - timedelta(days=max(1, overlap_days))
    logger.info(
        "fetch %s mode=%s start=%s period=%s end=%s cache_rows=%s",
        ticker,
        "full" if need_full else "incremental",
        start,
        period,
        end if start is not None else None,
        0 if ex is None else len(ex),
    )

    frame, reason = yf_download_bars(
        ticker,
        start=start,
        end=end if start is not None else None,
        period=period,
        attempts=attempts,
        sleep=sleep,
        download=download,
        min_rows=1,
    )
    source = "yfinance"
    if frame is None:
        notes.append(reason or f"yfinance failed {ticker}")
        date_from = (start or (completed - timedelta(days=900))).isoformat()
        date_to = completed.isoformat()
        count = 800 if need_full else max(overlap_days + 5, 15)
        tv_call = tv_fetch or fetch_tv_bars
        try:
            tv_frame, tv_note = tv_call(ticker, date_from=date_from, date_to=date_to, count=count)
        except TypeError:
            tv_frame, tv_note = tv_call(ticker, date_from=date_from, date_to=date_to)
        if tv_frame is None:
            notes.append(tv_note or f"tv failed {ticker}")
        else:
            frame = tv_frame
            source = "tradingview"
        if frame is None:
            st_call = stooq_fetch or fetch_stooq_bars
            try:
                st_frame, st_note = st_call(ticker, date_from=date_from, date_to=date_to)
            except TypeError:
                st_frame, st_note = st_call(ticker)
            if st_frame is None:
                notes.append(st_note or f"stooq failed {ticker}")
            else:
                frame = st_frame
                source = "stooq"

    merged = merge_bars(ex, frame, completed)
    if merged is None or (min_bars and len(merged) < 1):
        if ex is not None and len(ex) >= min_bars:
            return ex, notes, "cache-stale"
        return merged, notes, "none"
    if frame is None:
        return merged, notes, "cache-stale" if ex is not None else "none"
    # If yfinance/fallback added nothing and we already had the file, say so.
    if ex is not None and len(merged) == len(ex) and source != "cache":
        # completed bar may have been replaced in place; still a network refresh
        return merged, notes, source
    return merged, notes, source
