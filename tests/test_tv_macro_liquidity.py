"""Map macro-liquidity monitor cards to FeatureSchema."""

from __future__ import annotations

import json
from pathlib import Path

from adapters.tv_macro_liquidity import (
    extract_driver_values,
    feature_schema_from_macro_card,
    load_macro_liquidity_features,
)


def test_extract_driver_values() -> None:
    modules = [
        {
            "name": "全球美元风险",
            "drivers": ["VIX: 18.5", "DXY: 101.2", "HY Stress: 12"],
        }
    ]
    d = extract_driver_values(modules)
    assert d["vix"] == 18.5
    assert d["dxy"] == 101.2


def test_feature_schema_from_macro_card_maps_danger() -> None:
    card = {
        "macro": {
            "state": "防御",
            "score": 72,
            "symbol": "BATS:QQQ",
            "summary": {"headline": "久期压力"},
            "modules": [
                {"name": "全球美元风险", "state": "防御", "score": 70, "drivers": ["VIX: 19.2", "DXY: 100.8"]},
                {"name": "利率曲线", "state": "观察", "score": 55, "drivers": ["BEI: 2.2"]},
            ],
        }
    }
    fs = feature_schema_from_macro_card(card)
    assert fs.danger_score >= 72
    assert fs.vix == 19.2
    assert fs.dxy == 100.8
    assert fs.bei_10y == 2.2
    assert fs.tips_yield is None  # honesty: not invented


def test_load_macro_liquidity_features_from_sidecar(tmp_path: Path) -> None:
    features = {
        "source": "MCP",
        "danger_score": 66,
        "risk_score": 0.66,
        "vix": 17.5,
        "dxy": 101.0,
    }
    fpath = tmp_path / "macro-os-features.latest.json"
    fpath.write_text(json.dumps(features), encoding="utf-8")
    loaded = load_macro_liquidity_features(features_path=fpath, latest_path=tmp_path / "missing.json", max_age_seconds=9999)
    assert loaded is not None
    assert loaded.vix == 17.5
    assert loaded.danger_score == 66
