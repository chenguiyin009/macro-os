"""Yahoo Finance runtime macro adapter (yfinance).

Provides fast, relatively stable market proxies for the research/runtime feature
set when TradingView MCP/relay is unavailable.

Important unit honesty:
- ^TNX/^TYX are nominal yield *levels* (percent points), good for nominal_10y/30y.
- TIP/HYG are ETF *prices*, NOT TIPS real yield or HY OAS bp. We expose them as
  optional proxies and derive direction/pressure features, but do not pretend they
  are DFII10 / BAMLH0A0HYM2.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.schemas import DataSource, FeatureSchema

logger = logging.getLogger(__name__)

# Logical field -> Yahoo ticker
DEFAULT_TICKERS: Dict[str, str] = {
    "vix": "^VIX",
    "nominal_10y": "^TNX",
    "nominal_30y": "^TYX",
    "nominal_2y_proxy": "^IRX",  # 13-week bill yield; not 2y UST, labeled proxy
    "dxy": "DX-Y.NYB",
    "gold": "GLD",
    "qqq_close": "QQQ",
    "tip_etf": "TIP",
    "hyg_etf": "HYG",
}


def _close_series(hist: Any) -> Optional[List[float]]:
    if hist is None:
        return None
    try:
        if getattr(hist, "empty", True):
            return None
        closes = [float(x) for x in hist["Close"].tolist() if x == x]
        return closes or None
    except Exception:
        return None


def _bp_change_from_yield_levels(closes: Sequence[float], lookback: int = 5) -> Optional[float]:
    """Yield levels are percent points; delta * 100 = bp."""
    if len(closes) < lookback + 1:
        if len(closes) >= 2:
            return round((closes[-1] - closes[0]) * 100.0, 4)
        return None
    return round((closes[-1] - closes[-(lookback + 1)]) * 100.0, 4)


def _pct_change(closes: Sequence[float], lookback: int = 5) -> Optional[float]:
    if len(closes) < lookback + 1:
        if len(closes) >= 2 and closes[0] != 0:
            return round((closes[-1] / closes[0] - 1.0) * 100.0, 4)
        return None
    base = closes[-(lookback + 1)]
    if base == 0:
        return None
    return round((closes[-1] / base - 1.0) * 100.0, 4)


class YFinanceMacroAdapter:
    """Fetch macro proxies via yfinance batch download."""

    def __init__(
        self,
        tickers: Optional[Dict[str, str]] = None,
        period: str = "15d",
        downloader: Any = None,
    ) -> None:
        self.tickers = dict(tickers or DEFAULT_TICKERS)
        self.period = period
        self._downloader = downloader  # injectable for tests
        self._last_error: Optional[str] = None
        self._last_status: Dict[str, str] = {}

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def fetch(self) -> Optional[FeatureSchema]:
        try:
            import yfinance as yf  # local import keeps module import light
        except Exception as exc:  # pragma: no cover
            self._last_error = f"yfinance import failed: {exc}"
            logger.warning(self._last_error)
            return None

        symbols = list(dict.fromkeys(self.tickers.values()))
        try:
            if self._downloader is not None:
                raw = self._downloader(symbols, period=self.period)
            else:
                raw = yf.download(
                    symbols,
                    period=self.period,
                    group_by="ticker",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
        except Exception as exc:
            self._last_error = f"yfinance download failed: {exc}"
            logger.warning(self._last_error)
            return None

        closes_by_symbol: Dict[str, List[float]] = {}
        for sym in symbols:
            try:
                # yfinance multi-ticker: columns MultiIndex (Ticker, Price)
                if hasattr(raw, "columns") and getattr(raw.columns, "nlevels", 1) > 1:
                    if sym in raw.columns.get_level_values(0):
                        sub = raw[sym]
                    else:
                        sub = None
                else:
                    # single ticker frame
                    sub = raw if len(symbols) == 1 else None
                series = _close_series(sub)
                if series:
                    closes_by_symbol[sym] = series
                    self._last_status[sym] = f"ok:n={len(series)}:last={series[-1]}"
                else:
                    self._last_status[sym] = "empty"
            except Exception as exc:
                self._last_status[sym] = f"err:{exc}"

        kwargs: Dict[str, Any] = {
            "source": DataSource.MCP,
            "fetched_at": datetime.now(timezone.utc),
        }

        def _lvl(field: str) -> Optional[float]:
            sym = self.tickers.get(field)
            if not sym:
                return None
            xs = closes_by_symbol.get(sym)
            return None if not xs else float(xs[-1])

        def _xs(field: str) -> Optional[List[float]]:
            sym = self.tickers.get(field)
            if not sym:
                return None
            return closes_by_symbol.get(sym)

        vix = _lvl("vix")
        if vix is not None:
            kwargs["vix"] = vix
            kwargs["risk_score"] = max(0.0, min(1.0, vix / 40.0))
            kwargs["danger_score"] = max(0.0, min(100.0, vix * 2.0))

        n10 = _lvl("nominal_10y")
        n30 = _lvl("nominal_30y")
        if n10 is not None:
            kwargs["nominal_10y"] = n10
        if n30 is not None:
            kwargs["nominal_30y"] = n30
        n2 = _lvl("nominal_2y_proxy")
        if n2 is not None:
            # Keep as proxy only; still useful for curve context
            kwargs["nominal_2y"] = n2

        xs10 = _xs("nominal_10y")
        xs30 = _xs("nominal_30y")
        if xs10:
            ch = _bp_change_from_yield_levels(xs10, 5)
            if ch is not None:
                kwargs["nominal_10y_change_5d_bp"] = ch
        if xs30:
            ch = _bp_change_from_yield_levels(xs30, 5)
            if ch is not None:
                kwargs["nominal_30y_change_5d_bp"] = ch

        dxy = _lvl("dxy")
        if dxy is not None:
            kwargs["dxy"] = dxy
        gold = _lvl("gold")
        if gold is not None:
            # GLD is ETF price; still maps to gold feature used by macro_mapper
            kwargs["gold"] = gold
        qqq = _lvl("qqq_close")
        if qqq is not None:
            kwargs["qqq_close"] = qqq

        # TIP ETF: do NOT set tips_yield (would be wrong unit/meaning).
        # Optionally derive a soft real-rate pressure proxy only if both TIP and TNX move.
        tip_xs = _xs("tip_etf")
        hyg_xs = _xs("hyg_etf")
        if tip_xs and len(tip_xs) >= 2:
            tip_chg = _pct_change(tip_xs, 5)
            # Inverse rough: TIP down often accompanies real-rate up; store as synthetic bp-ish
            if tip_chg is not None and "tips_yield_change_5d_bp" not in kwargs:
                kwargs["tips_yield_change_5d_bp"] = round(-tip_chg * 10.0, 4)  # soft proxy only
        if hyg_xs and len(hyg_xs) >= 2:
            hyg_chg = _pct_change(hyg_xs, 5)
            # HYG price down => credit stress up; convert soft proxy to bp-ish without claiming OAS
            if hyg_chg is not None and "hy_credit_spread" not in kwargs:
                # baseline mid 320bp, widen when HYG falls
                kwargs["hy_credit_spread"] = round(max(150.0, 320.0 - hyg_chg * 8.0), 4)

        # If we still lack tips_yield level, leave it absent (FRED can fill).
        required_any = ["vix", "nominal_10y", "nominal_30y", "dxy"]
        if not any(k in kwargs for k in required_any):
            self._last_error = "yfinance returned no usable macro fields"
            return None

        try:
            fs = FeatureSchema(**kwargs)
        except Exception as exc:
            self._last_error = f"schema build failed: {exc}"
            logger.warning(self._last_error)
            return None

        self._last_error = None
        return fs
