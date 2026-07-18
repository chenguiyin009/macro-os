"""FRED public CSV fetcher for macro funding-price features.

Uses fred.stlouisfed.org graph CSV endpoints (no API key required).
Pure network I/O adapter — does not call decision_kernel.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import urllib.error
import socket
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.schemas import DataSource, FeatureSchema

logger = logging.getLogger(__name__)

# series_id -> FeatureSchema field (+ unit note)
# Minimal set for runtime fallback latency. Weekly pipeline can pass a fuller map.
CORE_SERIES: Dict[str, str] = {
    "DFII10": "tips_yield",          # 10Y TIPS real yield, percent
    "DGS10": "nominal_10y",          # 10Y nominal, percent
    "DGS30": "nominal_30y",
    "VIXCLS": "vix",
    "BAMLH0A0HYM2": "hy_oas_pct",    # ICE BofA HY OAS, percent -> convert to bp
}
EXTENDED_SERIES: Dict[str, str] = {
    **CORE_SERIES,
    "DGS2": "nominal_2y",
    "T10YIE": "bei_10y",
    "DTWEXBGS": "dxy",               # broad USD index (level; not ICE DXY)
}
DEFAULT_SERIES = CORE_SERIES

# Bound history: full-history CSV is multi-MB and too slow for runtime fallback.
CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv?"
    "id={series_id}&cosd={start}&coed={end}"
)


@dataclass
class SeriesPoint:
    date: str
    value: float


def _parse_fred_csv(text: str) -> List[SeriesPoint]:
    rows: List[SeriesPoint] = []
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        return rows
    for row in reader:
        if len(row) < 2:
            continue
        date_s, raw = row[0].strip(), row[1].strip()
        if not date_s or raw in {"", "."}:
            continue
        try:
            rows.append(SeriesPoint(date=date_s, value=float(raw)))
        except ValueError:
            continue
    return rows


def fetch_fred_series(
    series_id: str,
    *,
    timeout_seconds: float = 12.0,
    opener: Any = None,
    lookback_days: int = 120,
) -> List[SeriesPoint]:
    """Fetch recent history for one FRED series as (date, value) points ascending."""
    from datetime import timedelta
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(lookback_days))
    url = CSV_URL.format(series_id=series_id, start=start.isoformat(), end=end.isoformat())
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MacroOS/1.0; +https://github.com/chenguiyin009/macro-os)",
            "Accept": "text/csv,text/plain,*/*",
        },
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=timeout_seconds) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    points = _parse_fred_csv(text)
    points.sort(key=lambda p: p.date)
    return points


def _bp_change(points: Sequence[SeriesPoint], lookback_trading_days: int = 5) -> Optional[float]:
    if len(points) < lookback_trading_days + 1:
        return None
    latest = points[-1].value
    past = points[-(lookback_trading_days + 1)].value
    # values are in percent points; convert delta to bp
    return round((latest - past) * 100.0, 4)


def build_feature_schema_from_series(
    series_map: Dict[str, List[SeriesPoint]],
    *,
    source: DataSource = DataSource.MCP,
    series: Optional[Dict[str, str]] = None,
) -> FeatureSchema:
    """Map fetched series into FeatureSchema + 5d bp changes where possible."""
    kwargs: Dict[str, Any] = {
        "source": source,
        "fetched_at": datetime.now(timezone.utc),
    }
    series_map_def = dict(series or DEFAULT_SERIES)
    # levels
    for sid, field in series_map_def.items():
        pts = series_map.get(sid) or []
        if not pts:
            continue
        val = pts[-1].value
        if field == "hy_oas_pct":
            kwargs["hy_credit_spread"] = round(val * 100.0, 4)  # percent -> bp
        elif field == "gold":
            kwargs["gold"] = val
        else:
            kwargs[field] = val

    # 5d changes for research quadrant
    if series_map.get("DFII10"):
        ch = _bp_change(series_map["DFII10"], 5)
        if ch is not None:
            kwargs["tips_yield_change_5d_bp"] = ch
    if series_map.get("DGS10"):
        ch = _bp_change(series_map["DGS10"], 5)
        if ch is not None:
            kwargs["nominal_10y_change_5d_bp"] = ch
    if series_map.get("DGS30"):
        ch = _bp_change(series_map["DGS30"], 5)
        if ch is not None:
            kwargs["nominal_30y_change_5d_bp"] = ch

    # simple risk score proxy from VIX (0-1-ish), non-constitutional
    vix = kwargs.get("vix")
    if vix is not None:
        kwargs["risk_score"] = max(0.0, min(1.0, float(vix) / 40.0))
        kwargs["danger_score"] = max(0.0, min(100.0, float(vix) * 2.0))

    return FeatureSchema(**kwargs)


class FredMacroAdapter:
    """Fetch live macro levels from FRED CSV endpoints."""

    def __init__(
        self,
        series: Optional[Dict[str, str]] = None,
        timeout_seconds: float = 20.0,
        opener: Any = None,
    ) -> None:
        self.series = dict(series or DEFAULT_SERIES)
        self.timeout_seconds = timeout_seconds
        self._opener = opener
        self._last_error: Optional[str] = None
        self._last_series_status: Dict[str, str] = {}

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def fetch(self) -> Optional[FeatureSchema]:
        import time

        series_map: Dict[str, List[SeriesPoint]] = {}
        errors: List[str] = []

        for sid in self.series:
            try:
                pts = fetch_fred_series(
                    sid,
                    timeout_seconds=self.timeout_seconds,
                    opener=self._opener,
                    lookback_days=120,
                )
                if not pts:
                    self._last_series_status[sid] = "empty"
                    errors.append(f"{sid}:empty")
                else:
                    series_map[sid] = pts
                    self._last_series_status[sid] = f"ok:{pts[-1].date}:{pts[-1].value}"
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                self._last_series_status[sid] = f"err:{exc}"
                errors.append(f"{sid}:{exc}")
                logger.warning("FRED fetch failed for %s: %s", sid, exc)
            time.sleep(0.15)

        # Require at least real + one nominal for funding-price research
        if "DFII10" not in series_map and "DGS10" not in series_map:
            self._last_error = "insufficient FRED series: " + "; ".join(errors[:6])
            return None

        try:
            # Prefer MANUAL when env forces, else MCP-like external
            source = DataSource.MCP
            fs = build_feature_schema_from_series(series_map, source=source, series=self.series)
            self._last_error = None if not errors else ("partial: " + "; ".join(errors[:6]))
            return fs
        except Exception as exc:  # validation
            self._last_error = f"schema build failed: {exc}"
            logger.warning("FRED FeatureSchema build failed: %s", exc)
            return None
