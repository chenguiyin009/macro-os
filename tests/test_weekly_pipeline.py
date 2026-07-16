"""Weekly research pipeline generation (offline mock)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_funding_price_weekly import (
    snapshot_from_features,
    write_weekly_artifacts,
)
from core.features import build_features
from adapters.tradingview import TradingViewAdapter


def test_weekly_pipeline_from_mock_writes_artifacts(tmp_path: Path, monkeypatch) -> None:
    root = Path(__file__).resolve().parents[1]
    # write into temp by monkeypatching project paths via write_weekly_artifacts project_root
    fs = TradingViewAdapter()._mock_snapshot()
    feats = build_features(fs)
    feats["_source"] = "MOCK"
    snap = snapshot_from_features(feats, week_start="2026-07-06", week_end="2026-07-10")
    assert snap["funding_price_quadrant"] == "Q1_STRESS_TEST"
    data_path, doc_path = write_weekly_artifacts(snap, project_root=tmp_path)
    assert data_path.exists()
    assert doc_path.exists()
    loaded = json.loads(data_path.read_text(encoding="utf-8"))
    assert loaded["macro_os_mapping"]["hard_regime_hint"] == "TIGHT_LIQUIDITY"
    text = doc_path.read_text(encoding="utf-8")
    assert "Q1_STRESS_TEST" in text
    assert "TIGHT_LIQUIDITY" in text
