"""Equity-stress overlay data adapter (SOXX 20-day drawdown -> tech_drawdown).

Bridges the daily denominator-state-machine automation's SOXX reading into
``features['tech_drawdown']`` so the C-grade microstructural dampener inside
``core.decision_kernel`` actually fires in live trading.

Channel assessment (see 2026-07-18 working memory):
  * yfinance  -> DIRECT ticker "SOXX", no chart-switch gymnastics, proven in
                 repo backtests, file-cacheable. CHOSEN as the authoritative source.
  * TV MCP data_get_ohlcv -> tied to the *current chart symbol*; would require
                 switching the chart to SOXX then back, disrupting the QQQ Pine
                 read. Kept as an optional live cross-check only.
  * Pine denominator script -> outputs a SOXX *Z-score* thermometer, NOT a
                 20-day peak-to-trough drawdown. Cross-check signal only.

The kernel expects ``tech_drawdown`` as a negative fraction of peak-to-trough
drawdown over the trailing window, e.g. -0.084 == -8.4%.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

import numpy as np
import pandas as pd

# Asymmetric hysteresis-band sensor (L1 feature producer). Keeps core.decision_kernel
# pure: the kernel only sees a generic negative "tech_drawdown"; whether it is the raw
# peak-to-trough drawdown or the hysteresis-smoothed one is decided here, at the edge.
from adapters.equity_stress_sensor import EquityStressSensor

logger = logging.getLogger(__name__)

DEFAULT_SOX_TICKER = "SOXX"
DEFAULT_CACHE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "_tech_drawdown_sox.csv"
)
DEFAULT_QQQ_TICKER = "QQQ"
DEFAULT_QQQ_CACHE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "_tech_drawdown_qqq.csv"
)
DEFAULT_CACHE_DATED_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "_tech_drawdown_sox_dated.csv"
)
# Local proxy default documented for this machine; only applied when actually
# downloading and not already present in the environment.
DEFAULT_PROXY = "http://127.0.0.1:7890"

# Default HTTP proxy is forced only when absent, so offline/dev stays clean.
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY")

# 外部环境（如 grokbot 云机无本地代理）可设 SECTOR_PROXY 重定向或关闭：
#   SECTOR_PROXY=off|none|0|false|no|""  → 完全不设代理（依赖直连/环境自带代理）
#   SECTOR_PROXY=http://host:port         → 使用指定代理
# 若已设置系统变量 HTTPS_PROXY/HTTP_PROXY，则优先级最高、本函数不覆盖。
_PROXY_OFF_VALUES = ("", "off", "none", "0", "false", "no")


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
    except Exception:  # pragma: no cover - env writes are best-effort
        pass


def peak_to_trough_drawdown(closes: Sequence[float], days: int = 20) -> Optional[float]:
    """Pure peak-to-trough drawdown over the trailing ``days`` window.

    Returns the most negative (close / running-max - 1) observed across the last
    ``days + 1`` bars, or ``None`` if there is not enough data.
    """
    if closes is None or len(closes) < 2:
        return None
    window = closes[-(days + 1):] if len(closes) > days else closes
    if len(window) < 2:
        return None
    running_max = window[0]
    worst = 0.0
    for price in window:
        if price > running_max:
            running_max = price
        if running_max > 0:
            dd = price / running_max - 1.0
            if dd < worst:
                worst = dd
    return float(worst) if worst < 0 else 0.0


def _read_cache(cache_path: Path, max_age_seconds: int) -> Optional[List[float]]:
    if not cache_path.exists():
        return None
    try:
        age = datetime.now().timestamp() - cache_path.stat().st_mtime
        if age > max_age_seconds:
            return None
        import csv

        closes: List[float] = []
        with cache_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                v = (row.get("Close") or row.get("close"))
                if v in (None, ""):
                    continue
                try:
                    closes.append(float(v))
                except ValueError:
                    continue
        return closes or None
    except Exception as exc:  # pragma: no cover
        logger.warning("tech_drawdown cache read failed: %s", exc)
        return None


def _write_cache(cache_path: Path, closes: List[float]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        import csv

        with cache_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Close"])
            for c in closes:
                writer.writerow([f"{c:.4f}"])
    except Exception as exc:  # pragma: no cover
        logger.warning("tech_drawdown cache write failed: %s", exc)


def _yf_download(
    ticker: str,
    downloader: Optional[Callable[[Any], Any]] = None,
) -> Optional[List[float]]:
    """Fetch SOXX close series via yfinance (or an injected downloader for tests).

    yfinance is only imported on the real-download path (``downloader is None``);
    an injected downloader must work without yfinance installed (CI has no yfinance).
    """
    try:
        if downloader is not None:
            raw = downloader(ticker)
        else:
            try:
                import yfinance as yf  # local import keeps module light
            except Exception as exc:  # pragma: no cover
                logger.warning("yfinance import failed: %s", exc)
                return None
            _ensure_proxy()
            raw = yf.download(
                ticker,
                period="3mo",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
    except Exception as exc:
        logger.warning("SOXX yfinance download failed: %s", exc)
        return None

    if raw is None:
        return None
    try:
        if getattr(raw, "empty", True):
            return None
        # yfinance 1.x returns a MultiIndex column. The field name ("Close") and
        # the ticker may sit on either level depending on version, so detect the
        # level that holds "Close" and cross-section the ticker from the other.
        close_col = None
        if getattr(raw.columns, "nlevels", 1) > 1:
            price_level = 0 if "Close" in raw.columns.get_level_values(0) else 1
            ticker_level = 1 - price_level
            levels = raw.columns.get_level_values(ticker_level)
            if ticker in levels:
                sub = raw.xs(ticker, axis=1, level=ticker_level)
                close_col = sub["Close"] if "Close" in sub.columns else None
        else:
            close_col = raw["Close"] if "Close" in raw.columns else None
        if close_col is None:
            logger.warning("SOXX Close column not found in yfinance frame")
            return None
        closes = [float(x) for x in close_col.tolist() if x == x]
        return closes or None
    except Exception as exc:  # pragma: no cover
        logger.warning("SOXX close parse failed: %s", exc)
        return None


def compute_soxx_drawdown(
    days: int = 20,
    *,
    ticker: str = DEFAULT_SOX_TICKER,
    cache_path: Optional[Path] = None,
    max_age_seconds: int = 2 * 86400,
    force_refresh: bool = False,
    downloader: Optional[Callable[[Any], Any]] = None,
) -> Optional[float]:
    """Compute SOXX trailing ``days``-day peak-to-trough drawdown.

    Returns a negative fraction (e.g. -0.084) or ``None`` if data is unavailable.
    Caches the close series to ``data/_tech_drawdown_sox.csv`` (refreshed when
    older than ``max_age_seconds`` or ``force_refresh``).
    """
    cache_path = Path(cache_path or DEFAULT_CACHE_PATH)

    closes: Optional[List[float]] = None
    if not force_refresh:
        closes = _read_cache(cache_path, max_age_seconds)

    if closes is None:
        closes = _yf_download(ticker, downloader=downloader)
        if closes is not None:
            _write_cache(cache_path, closes)

    if closes is None:
        return None
    return peak_to_trough_drawdown(closes, days=days)


def soxx_drawdown_from_ohlcv(closes: Sequence[float], days: int = 20) -> Optional[float]:
    """Convenience for the TV-MCP cross-check path: compute from an externally
    supplied close series (e.g. data_get_ohlcv bars) without touching yfinance.
    """
    return peak_to_trough_drawdown(list(closes), days=days)


def compute_soxx_drawdown_smoothed(
    days: int = 20,
    *,
    lag: int = 2,
    ticker: str = DEFAULT_SOX_TICKER,
    cache_path: Optional[Path] = None,
    max_age_seconds: int = 2 * 86400,
    force_refresh: bool = False,
    downloader: Optional[Callable[[Any], Any]] = None,
) -> Optional[float]:
    """Compute SOXX trailing drawdown with the asymmetric hysteresis band applied.

    Returns the LATEST smoothed (hysteresis-band) drawdown — the value that should
    be injected into ``features['tech_drawdown']`` for live trading. The kernel is
    unchanged; only the sensor reading is smoothed (进档快 / 出档慢).

    Reuses the same cached SOXX close series as ``compute_soxx_drawdown``.
    """
    cache_path = Path(cache_path or DEFAULT_CACHE_PATH)

    closes: Optional[List[float]] = None
    if not force_refresh:
        closes = _read_cache(cache_path, max_age_seconds)

    if closes is None:
        closes = _yf_download(ticker, downloader=downloader)
        if closes is not None:
            _write_cache(cache_path, closes)

    if closes is None:
        return None

    sensor = EquityStressSensor(lookback_window=days, smoothing_lag_days=lag)
    return sensor.compute_smoothed_drawdown(pd.Series(closes))


def _yf_download_dated(
    ticker: str,
    downloader: Optional[Callable[[Any], Any]] = None,
) -> Optional[List[tuple]]:
    """Fetch dated SOXX ``(date, close)`` series via yfinance for trajectory analysis.

    Mirrors ``_yf_download`` but preserves the date axis so callers can locate the
    drawdown trough and measure recovery/slope. Returns None on any failure.
    """
    try:
        if downloader is not None:
            raw = downloader(ticker)
        else:
            try:
                import yfinance as yf  # local import keeps module light
            except Exception as exc:  # pragma: no cover
                logger.warning("yfinance import failed: %s", exc)
                return None
            _ensure_proxy()
            raw = yf.download(
                ticker,
                period="3mo",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("SOXX dated yfinance download failed: %s", exc)
        return None
    if raw is None or getattr(raw, "empty", True):
        return None
    try:
        close_col = None
        if getattr(raw.columns, "nlevels", 1) > 1:
            price_level = 0 if "Close" in raw.columns.get_level_values(0) else 1
            ticker_level = 1 - price_level
            levels = raw.columns.get_level_values(ticker_level)
            if ticker in levels:
                sub = raw.xs(ticker, axis=1, level=ticker_level)
                close_col = sub["Close"] if "Close" in sub.columns else None
        else:
            close_col = raw["Close"] if "Close" in raw.columns else None
        if close_col is None:
            logger.warning("SOXX Close column not found in dated frame")
            return None
        out: List[tuple] = []
        for idx, val in close_col.items():
            if val == val and val is not None:
                d = str(idx.date()) if hasattr(idx, "date") else str(idx)
                out.append((d, float(val)))
        return out or None
    except Exception as exc:  # pragma: no cover
        logger.warning("SOXX dated parse failed: %s", exc)
        return None


def _read_dated_cache(cache_path: Path, max_age_seconds: int) -> Optional[List[tuple]]:
    if not cache_path.exists():
        return None
    try:
        age = datetime.now().timestamp() - cache_path.stat().st_mtime
        if age > max_age_seconds:
            return None
        import csv

        rows: List[tuple] = []
        with cache_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                d = row.get("date")
                v = row.get("Close") or row.get("close")
                if not d or v in (None, ""):
                    continue
                try:
                    rows.append((d, float(v)))
                except ValueError:
                    continue
        return rows or None
    except Exception as exc:  # pragma: no cover
        logger.warning("dated tech_drawdown cache read failed: %s", exc)
        return None


def _write_dated_cache(cache_path: Path, series: List[tuple]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        import csv

        with cache_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "Close"])
            for d, c in series:
                w.writerow([d, f"{c:.4f}"])
    except Exception as exc:  # pragma: no cover
        logger.warning("dated tech_drawdown cache write failed: %s", exc)


def compute_soxx_trajectory(
    days: int = 20,
    *,
    ticker: str = DEFAULT_SOX_TICKER,
    cache_path: Optional[Path] = None,
    max_age_seconds: int = 2 * 86400,
    force_refresh: bool = False,
    downloader: Optional[Callable[[Any], Any]] = None,
) -> Optional[dict]:
    """Trajectory of SOXX 20-day-peak drawdown: trough, rebound, slope, label.

    The single drawdown number is a *level* (how far below the recent high) and is
    lagging; this adds *direction* so readers do not mistake a lagging level for a
    still-falling price. Returns None if data is unavailable.

    ``label`` is one of ``deepening`` (探底加深) / ``bottoming`` (摸底横盘) /
    ``rebuilding`` (反弹恢复中).
    """
    cache_path = Path(cache_path or DEFAULT_CACHE_DATED_PATH)
    series: Optional[List[tuple]] = None
    if not force_refresh:
        series = _read_dated_cache(cache_path, max_age_seconds)
    if series is None:
        series = _yf_download_dated(ticker, downloader=downloader)
        if series is not None:
            _write_dated_cache(cache_path, series)
    if not series or len(series) < 5:
        return None

    dates = [d for d, _ in series]
    closes = [c for _, c in series]
    s = pd.Series(closes)
    runmax = s.rolling(days, min_periods=1).max()
    dd = (s / runmax - 1.0).values  # negative

    win = min(30, len(closes))
    local_trough = int(np.argmin(closes[-win:]))
    trough_i = len(closes) - win + local_trough
    trough_close = float(closes[trough_i])
    trough_date = dates[trough_i]
    last = float(closes[-1])
    recovery = last / trough_close - 1.0 if trough_close > 0 else 0.0
    sessions_since_trough = (len(closes) - 1) - trough_i

    def _slope(n: int) -> float:
        x = np.arange(n, dtype=float)
        y = dd[-n:]
        return float(np.polyfit(x, y, 1)[0])

    s3 = _slope(3)
    s5 = _slope(5)
    s10 = _slope(10)
    n_recent = min(5, len(closes))
    recent_psl = float(
        np.polyfit(np.arange(n_recent, dtype=float), np.array(closes[-n_recent:]), 1)[0]
    )

    if s5 * 100.0 > 0.4:
        label = "rebuilding"
    elif s5 * 100.0 < -0.4:
        label = "deepening"
    else:
        label = "bottoming"

    return {
        "trough_date": trough_date,
        "trough_close": round(trough_close, 2),
        "last_close": round(last, 2),
        "recovery_from_trough_pct": round(recovery * 100.0, 2),
        "sessions_since_trough": int(sessions_since_trough),
        "drawdown_slope_3d_pp": round(s3 * 100.0, 3),
        "drawdown_slope_5d_pp": round(s5 * 100.0, 3),
        "drawdown_slope_10d_pp": round(s10 * 100.0, 3),
        "recent_price_slope_5d": round(recent_psl, 2),
        "naive_20d_peak_dd_pct": round(float(dd[-1]) * 100.0, 2),
        "label": label,
    }


def compute_qqq_drawdown_smoothed(
    days: int = 20,
    *,
    lag: int = 2,
    ticker: str = DEFAULT_QQQ_TICKER,
    cache_path: Optional[Path] = None,
    max_age_seconds: int = 2 * 86400,
    force_refresh: bool = False,
    downloader: Optional[Callable[[Any], Any]] = None,
) -> Optional[float]:
    """QQQ trailing drawdown (hysteresis-smoothed) -> features['qqq_drawdown'].

    Mirrors ``compute_soxx_drawdown_smoothed``. Feeds the QQQ leg of the Phase-2
    cross-asset AND-gate structural-weakness test inside core.decision_kernel
    (struct_weak = SOXX OR QQQ 20d peak-to-trough <= -0.07). Uses a separate cache
    so the SOXX and QQQ close series never collide.
    """
    cache_path = Path(cache_path or DEFAULT_QQQ_CACHE_PATH)

    closes: Optional[List[float]] = None
    if not force_refresh:
        closes = _read_cache(cache_path, max_age_seconds)

    if closes is None:
        closes = _yf_download(ticker, downloader=downloader)
        if closes is not None:
            _write_cache(cache_path, closes)

    if closes is None:
        return None

    sensor = EquityStressSensor(lookback_window=days, smoothing_lag_days=lag)
    return sensor.compute_smoothed_drawdown(pd.Series(closes))

