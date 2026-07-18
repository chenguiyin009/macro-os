"""Tests for FRED series parsing and FeatureSchema mapping (no live network)."""

from __future__ import annotations

import io
from adapters.fred import (
    EXTENDED_SERIES,
    FredMacroAdapter,
    SeriesPoint,
    _bp_change,
    build_feature_schema_from_series,
    _parse_fred_csv,
    fetch_fred_series,
)


SAMPLE_CSV = """observation_date,DGS10
2026-07-07,4.40
2026-07-08,4.45
2026-07-09,4.50
2026-07-10,4.52
2026-07-11,4.53
2026-07-14,4.58
"""


class _FakeResp:
    def __init__(self, text: str):
        self._text = text.encode("utf-8")

    def read(self):
        return self._text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_parse_fred_csv_skips_dots() -> None:
    text = "observation_date,X\n2026-01-01,.\n2026-01-02,1.25\n"
    pts = _parse_fred_csv(text)
    assert len(pts) == 1
    assert pts[0].value == 1.25


def test_bp_change_five_sessions() -> None:
    pts = _parse_fred_csv(SAMPLE_CSV)
    ch = _bp_change(pts, 5)
    assert ch == 18.0


def test_build_feature_schema_converts_hy_oas_to_bp() -> None:
    series = {
        "DFII10": [SeriesPoint(f"2026-07-{i:02d}", 2.0 + i * 0.01) for i in range(1, 8)],
        "DGS10": [SeriesPoint(f"2026-07-{i:02d}", 4.0 + i * 0.01) for i in range(1, 8)],
        "DGS30": [SeriesPoint(f"2026-07-{i:02d}", 4.8 + i * 0.01) for i in range(1, 8)],
        "BAMLH0A0HYM2": [SeriesPoint("2026-07-14", 2.72)],
        "VIXCLS": [SeriesPoint("2026-07-14", 16.5)],
    }
    fs = build_feature_schema_from_series(series)
    assert fs.tips_yield is not None
    assert fs.hy_credit_spread is not None
    assert abs(fs.hy_credit_spread - 272.0) < 1e-6
    assert fs.vix == 16.5


def test_build_feature_schema_includes_extended_series_fields() -> None:
    series = {
        "DFII10": [SeriesPoint(f"2026-07-{i:02d}", 2.0 + i * 0.01) for i in range(1, 8)],
        "DGS10": [SeriesPoint(f"2026-07-{i:02d}", 4.0 + i * 0.01) for i in range(1, 8)],
        "DGS30": [SeriesPoint(f"2026-07-{i:02d}", 4.8 + i * 0.01) for i in range(1, 8)],
        "DGS2": [SeriesPoint(f"2026-07-{i:02d}", 3.6 + i * 0.01) for i in range(1, 8)],
        "T10YIE": [SeriesPoint("2026-07-14", 2.25)],
        "DTWEXBGS": [SeriesPoint("2026-07-14", 100.7)],
        "BAMLH0A0HYM2": [SeriesPoint("2026-07-14", 2.72)],
        "VIXCLS": [SeriesPoint("2026-07-14", 16.5)],
    }
    fs = build_feature_schema_from_series(series, series=EXTENDED_SERIES)
    assert fs.nominal_2y == 3.67
    assert fs.bei_10y == 2.25
    assert fs.dxy == 100.7


def test_fetch_fred_series_uses_opener() -> None:
    def opener(req, timeout=0):
        return _FakeResp(SAMPLE_CSV)

    pts = fetch_fred_series("DGS10", opener=opener, timeout_seconds=1)
    assert len(pts) == 6
    assert pts[-1].value == 4.58


def test_fred_adapter_fetch_with_fake_opener() -> None:
    def opener(req, timeout=0):
        # return enough rows for 5d change
        sid = "X"
        if "id=" in req.full_url:
            sid = req.full_url.split("id=")[1].split("&")[0]
        rows = ["observation_date," + sid]
        base = 2.0 if sid == "DFII10" else (2.7 if sid == "BAMLH0A0HYM2" else 4.0)
        for i in range(1, 10):
            rows.append(f"2026-07-{i:02d},{base + i * 0.01}")
        return _FakeResp("\n".join(rows) + "\n")

    a = FredMacroAdapter(opener=opener, timeout_seconds=1)
    fs = a.fetch()
    assert fs is not None
    assert fs.tips_yield is not None
    assert fs.nominal_10y is not None
