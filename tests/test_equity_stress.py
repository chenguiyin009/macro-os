"""Tests for the SOXX tech-drawdown bridge (adapters/equity_stress.py)."""

import os
from pathlib import Path

import pandas as pd
import pytest

from adapters.equity_stress import (
    compute_soxx_drawdown,
    peak_to_trough_drawdown,
)
from core.schemas import DataSource, FeatureSchema


def test_peak_to_trough_drawdown_basic():
    closes = [100, 102, 101, 95, 90, 92, 98]
    # running max peaks at 102; worst close 90 -> 90/102 - 1 = -0.1176
    dd = peak_to_trough_drawdown(closes, days=20)
    assert dd is not None
    assert abs(dd - (90 / 102 - 1.0)) < 1e-9


def test_peak_to_trough_drawdown_flat_is_zero():
    assert peak_to_trough_drawdown([100, 100, 100, 100]) == 0.0


def test_peak_to_trough_drawdown_short_series_none():
    assert peak_to_trough_drawdown([100]) is None


def _fake_yf_frame(ticker: str = "SOXX") -> pd.DataFrame:
    # 45 bars; the trailing 21-bar window holds the peak (100) -> trough (78)
    # drawdown so peak_to_trough_drawdown(days=20) returns -0.22.
    close = [110.0] * 24 + [
        100.0, 100.0, 95.0, 90.0, 85.0, 80.0, 78.0, 82.0, 88.0, 90.0,
        92.0, 95.0, 97.0, 99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0,
    ]
    cols = ["Close", "Open", "High", "Low", "Volume"]
    df = pd.DataFrame({c: close for c in cols})
    df.columns = pd.MultiIndex.from_product([cols, [ticker]])
    return df


def test_compute_soxx_drawdown_via_injected_downloader(tmp_path: Path):
    cache = tmp_path / "sox.csv"
    calls = {"n": 0}

    def dl(ticker):
        calls["n"] += 1
        return _fake_yf_frame(ticker)

    dd = compute_soxx_drawdown(days=20, cache_path=cache, downloader=dl)
    # worst 20d dd: peak 100 -> trough 78 => -0.22
    assert dd is not None
    assert abs(dd - (78.0 / 100.0 - 1.0)) < 1e-6
    assert calls["n"] == 1
    # cache written
    assert cache.exists()

    # second call should hit cache, not the downloader
    dd2 = compute_soxx_drawdown(days=20, cache_path=cache, downloader=dl)
    assert dd2 is not None
    assert calls["n"] == 1


def test_compute_soxx_drawdown_downloader_failure_returns_none(tmp_path: Path):
    def dl(ticker):
        raise RuntimeError("network down")

    assert compute_soxx_drawdown(days=20, cache_path=tmp_path / "x.csv", downloader=dl) is None


def test_orchestrator_injects_tech_drawdown(monkeypatch):
    # Production path: non-MOCK source + env enabled -> smoothed tech_drawdown set.
    from runtime import orchestrator as orch_module

    monkeypatch.setenv("MACRO_OS_TECH_DRAWDOWN_ENABLED", "1")
    monkeypatch.setattr(orch_module, "compute_soxx_drawdown_smoothed", lambda: -0.10)

    orch = object.__new__(orch_module.Orchestrator)
    raw = FeatureSchema(source=DataSource.MCP)
    features: dict = {}
    orch._inject_tech_drawdown(features, raw)
    assert features.get("tech_drawdown") == -0.10


def test_orchestrator_skips_on_mock_source(monkeypatch):
    from runtime import orchestrator as orch_module

    monkeypatch.setenv("MACRO_OS_TECH_DRAWDOWN_ENABLED", "1")
    monkeypatch.setattr(orch_module, "compute_soxx_drawdown_smoothed", lambda: -0.10)

    orch = object.__new__(orch_module.Orchestrator)
    raw = FeatureSchema(source=DataSource.MOCK)
    features: dict = {}
    orch._inject_tech_drawdown(features, raw)
    assert "tech_drawdown" not in features


def test_orchestrator_skips_when_env_disabled(monkeypatch):
    from runtime import orchestrator as orch_module

    monkeypatch.setenv("MACRO_OS_TECH_DRAWDOWN_ENABLED", "0")
    monkeypatch.setattr(orch_module, "compute_soxx_drawdown_smoothed", lambda: -0.10)

    orch = object.__new__(orch_module.Orchestrator)
    raw = FeatureSchema(source=DataSource.MCP)
    features: dict = {}
    orch._inject_tech_drawdown(features, raw)
    assert "tech_drawdown" not in features
