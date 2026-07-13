"""Pine conclusion bridge integration tests.

Covers:
- PineConclusionSchema / FeatureSchema.pine parsing.
- TradingViewAdapter.fetch_pine_conclusions (mock fallback + live CDP bridge when
  TradingView Desktop is running with a debug port on 127.0.0.1:9222).
- PineAnalysisNode staging the conclusion onto the pipeline context.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import pytest

from core.schemas import DataSource, FeatureSchema, PineConclusionSchema
from adapters.tradingview import TradingViewAdapter
from core.pipeline import PipelineContext, PipelineEngine, PineAnalysisNode


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SCRIPT = REPO_ROOT / "relay" / "pine-bridge.mjs"
LIVE_SCRIPT = "Global Sentinel"


def _cdp_alive() -> bool:
    try:
        out = subprocess.run(
            ["node", str(BRIDGE_SCRIPT), "--dry"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return False


def test_pine_conclusion_schema_roundtrip() -> None:
    c = PineConclusionSchema(
        source_script="Gold Execution Layer v1",
        symbol="TVC:GOLD",
        tf="1D",
        signal="WATCH LONG",
        value=7.0,
        confidence=0.8,
        payload={"study_name": "Gold Execution Layer v1", "values": []},
    )
    dumped = c.model_dump()
    assert dumped["source_script"] == "Gold Execution Layer v1"
    assert dumped["signal"] == "WATCH LONG"
    assert dumped["value"] == 7.0
    assert PineConclusionSchema(**dumped).source_script == "Gold Execution Layer v1"


def test_feature_schema_accepts_pine() -> None:
    c = PineConclusionSchema(source_script="x", symbol="TVC:GOLD", value=3.0)
    f = FeatureSchema(pine=c, gold=2350.0, source=DataSource.MOCK)
    assert f.pine is not None
    assert f.pine.source_script == "x"
    # round-trip through JSON still parses
    FeatureSchema(**json.loads(f.model_dump_json()))


def _make_adapter() -> TradingViewAdapter:
    return TradingViewAdapter(mcp_command="node", timeout_seconds=40)


def test_fetch_pine_mock_fallback() -> None:
    """Without a live bridge match, the adapter returns a clearly-labeled mock."""
    adapter = _make_adapter()
    # Force the script to be absent by pointing at a script name that won't exist
    # on any chart; the subprocess errors and we fall back to mock.
    conclusion = adapter.fetch_pine_conclusions(symbol="TVC:GOLD", script_name="__definitely_not_a_real_script__")
    assert conclusion is not None
    assert conclusion.source_script == "__definitely_not_a_real_script__"
    assert conclusion.payload.get("study_name") == "MOCK"


@pytest.mark.skipif(not _cdp_alive(), reason="TradingView Desktop CDP not reachable on 127.0.0.1:9222")
def test_fetch_pine_live_bridge() -> None:
    adapter = _make_adapter()
    conclusion = adapter.fetch_pine_conclusions(symbol="TVC:GOLD", script_name=LIVE_SCRIPT)
    assert conclusion is not None
    assert conclusion.source_script == LIVE_SCRIPT
    assert conclusion.symbol.upper().endswith("GOLD")
    assert "values" in conclusion.payload


@pytest.mark.skipif(not _cdp_alive(), reason="TradingView Desktop CDP not reachable on 127.0.0.1:9222")
def test_pine_analysis_node_stages_conclusion() -> None:
    adapter = _make_adapter()
    node = PineAnalysisNode(adapter, symbol="TVC:GOLD", script_name=LIVE_SCRIPT)
    ctx = PipelineContext()
    ok = node.execute(ctx)
    assert ok is True
    assert "pine_conclusion" in ctx.data
    assert ctx.data["pine_conclusion"]["source_script"] == LIVE_SCRIPT
    assert ctx.data["signals"]["pine"]["symbol"].upper().endswith("GOLD")


def test_pine_analysis_node_pipeline() -> None:
    """Node runs inside the engine and updates the state file on success."""
    adapter = _make_adapter()
    node = PineAnalysisNode(adapter, symbol="TVC:GOLD", script_name="__definitely_not_a_real_script__")
    engine = PipelineEngine(nodes=[node], state_file=str(REPO_ROOT / "docs" / "_pine_test_state.md"))
    ctx = PipelineContext()
    ok = engine.run(ctx)
    assert ok is True
    assert ctx.data["pine_conclusion"]["payload"]["study_name"] == "MOCK"
