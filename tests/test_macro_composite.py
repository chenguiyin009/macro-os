"""macro composite merge + last-good cache tests."""

from __future__ import annotations

from pathlib import Path

from adapters.macro_composite import (
    fetch_merged_macro_snapshot,
    load_last_good,
    merge_feature_snapshots,
    save_last_good,
)
from core.schemas import DataSource, FeatureSchema


def test_merge_prefers_first_then_fred_high_quality() -> None:
    yf = FeatureSchema(vix=16.0, nominal_10y=4.5, hy_credit_spread=300.0, source=DataSource.MCP)
    fred = FeatureSchema(tips_yield=2.33, hy_credit_spread=272.0, nominal_10y=4.58, source=DataSource.MCP)
    merged = merge_feature_snapshots([("yfinance", yf), ("fred", fred)], prefer_high_quality_from=["fred"])
    assert merged is not None
    assert merged.vix == 16.0  # from yfinance first
    assert merged.tips_yield == 2.33  # fred high quality
    assert merged.hy_credit_spread == 272.0  # fred overwrites proxy
    assert merged.nominal_10y == 4.5  # general field keeps first (yfinance)


def test_last_good_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "macro_last_good.json"
    fs = FeatureSchema(vix=17.0, nominal_10y=4.4, tips_yield=2.1, dxy=101.0)
    save_last_good(fs, path)
    loaded = load_last_good(path)
    assert loaded is not None
    assert loaded.vix == 17.0
    assert loaded.tips_yield == 2.1


def test_fetch_merged_uses_injectable_sources(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    yf_fs = FeatureSchema(vix=16.5, nominal_10y=4.55, nominal_30y=5.06, dxy=100.5)
    fred_fs = FeatureSchema(tips_yield=2.3, hy_credit_spread=280.0, nominal_10y=4.6)

    class Y:
        def fetch(self):
            return yf_fs

    class F:
        def fetch(self):
            return fred_fs

    merged = fetch_merged_macro_snapshot(
        include_tv=False,
        include_yfinance=True,
        include_fred=True,
        use_cache=True,
        cache_path=cache,
        yf_adapter=Y(),
        fred_adapter=F(),
    )
    assert merged is not None
    assert merged.vix == 16.5
    assert merged.tips_yield == 2.3
    assert merged.hy_credit_spread == 280.0
    assert cache.exists()
