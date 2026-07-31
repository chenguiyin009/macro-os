"""MCP provider tests: recorded fixture -> SentimentRawInput, no fabrication.

Verifies the Workbuddy-side data leg (handoff:
`workbuddy_sentiment_backtest_handoff.md`):
- McpSentimentProvider maps a recorded MCP raw payload to SentimentRawInput
- missing fields stay None -> engine emits PARTIAL/DEGRADED (never fabricated)
- core cycle location (S3 / TACTICAL / TO_CSI1000 / DRAG_NOT_LIFT) is preserved
  on real LIVE data, consistent with the Phase0 golden mock sample.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.sentiment.mcp_provider import (
    McpSentimentProvider,
    build_raw_from_mcp_aggregates,
)
from core.sentiment.engine import SentimentShadowEngine

CFG = {
    "sse_anchor_px": 3750,
    "sse_anchor_buffer_pct": 0.008,
    "thresholds": {
        "margin_stress_limit_down": 10,
        "margin_release_rate_high": 0.7,
        "resonance_s4_min": 0.65,
        "corr_crowded": 0.7,
    },
    "playbook_hints": {
        "hs300_offset": -0.005,
        "chinext_offset": -0.01,
        "csi1000_offset": -0.02,
    },
}


def _engine() -> SentimentShadowEngine:
    return SentimentShadowEngine(CFG)


def test_mcp_provider_fetch_maps_recorded_fixture():
    raw = McpSentimentProvider().fetch("2026-07-21", "CLOSE")
    assert raw.as_of == "2026-07-21"
    # honesty guard: recorded data must be LIVE / LIVE_PARTIAL
    assert raw.source == "LIVE_PARTIAL"
    # real values preserved
    assert raw.sse_last == 3864.37
    assert raw.transmission_csi1000 is not None
    # missing stays None (no fabrication)
    assert raw.margin_top100_limit_down_n is None
    assert raw.drawdown_kr_semi is None


def test_mcp_provider_board_probe_lifts_quality_and_preserves_core_location():
    """Board-behavior substitute (tdx_screener consecutive limit-down) backfills
    the stress probe when margin Top100 is unavailable.

    Verifies:
    - margin_top100_* stay None (no fabrication)
    - board probe fields are populated from fixture
    - release_rate is now computed (board substitute: 8/17 ≈ 0.47)
    - quality upgraded from DEGRADED → OK (stress probe present + sse_last present)
    - core cycle location (S3 / TACTICAL / TO_CSI1000 / DRAG_NOT_LIFT) is preserved
    """
    eng = _engine()
    raw = McpSentimentProvider().fetch("2026-07-21", "CLOSE")
    snap = eng.compute(raw)
    # margin path untouched
    assert snap.margin_top100_limit_down_n is None
    # board probe backfilled
    assert snap.limit_down_consecutive_n == 17
    assert snap.limit_down_open_n == 8
    assert snap.limit_down_new_n == 12
    # release rate computed from board substitute
    assert snap.deleveraging_release_rate is not None
    assert 0.4 < snap.deleveraging_release_rate < 0.6
    # quality upgraded: stress probe present + sse_last present → OK
    assert snap.quality == "OK"
    # core cycle location consistent with golden mock sample
    assert snap.cycle.stage == "S3"
    assert snap.cycle.rebound_type == "TACTICAL"
    assert snap.liquidity_transmission_stage == "TO_CSI1000"
    assert "DRAG_NOT_LIFT" in snap.corroboration_flags
    assert "BOUNCE_NOT_REVERSAL" in snap.corroboration_flags


def test_build_raw_helper_preserves_missing_as_none():
    raw = build_raw_from_mcp_aggregates(
        "2026-07-21", "CLOSE", {"sse_last": 3800.0}, source="LIVE_PARTIAL"
    )
    assert raw.sse_last == 3800.0
    assert raw.margin_top100_limit_down_n is None
    assert raw.source == "LIVE_PARTIAL"


def test_derived_composites_fill_previously_dangling_fields():
    """ars / anchor_hold_quality / crowding_unwind_score / a_vs_global_rel /
    global_sync_score were defined-but-never-computed dead fields; the composite
    layer now populates them on real LIVE data (07-21)."""
    eng = _engine()
    snap = eng.compute(McpSentimentProvider().fetch("2026-07-21", "CLOSE"))
    # ARS in valid 0~100 range
    assert snap.ars is not None and 0.0 <= snap.ars <= 100.0
    # anchor hold quality in 0~1
    assert snap.anchor_hold_quality is not None and 0.0 <= snap.anchor_hold_quality <= 1.0
    # crowding unwind proxy present (cross_section_corr unavailable -> board proxy)
    assert snap.cross_section_corr_tech is None
    assert snap.crowding_unwind_score is not None and 0.0 <= snap.crowding_unwind_score <= 1.0
    # A vs global relative posture (A semis deeper than SOXX -> A_LAGGARD)
    assert snap.a_vs_global_rel == "A_LAGGARD"
    # global sync computed from >=2 available drawdowns
    assert snap.global_sync_score is not None and 0.0 <= snap.global_sync_score <= 1.0


def test_a_vs_global_rel_and_ars_edge_cases():
    from core.sentiment.engine import compute_a_vs_global_rel, compute_ars

    # A deeper than global -> laggard; shallower -> resilient; within band -> sync
    assert compute_a_vs_global_rel(-0.31, -0.20) == "A_LAGGARD"
    assert compute_a_vs_global_rel(-0.10, -0.20) == "A_RESILIENT"
    assert compute_a_vs_global_rel(-0.20, -0.20) == "A_SYNC"
    assert compute_a_vs_global_rel(None, -0.20) is None
    # ARS drops missing dims and reports coverage
    w = {"global": 0.2, "liquidity": 0.25, "breadth": 0.25, "sentiment": 0.15, "momentum": 0.15}
    val, cov = compute_ars({"global": 60.0, "liquidity": None, "breadth": None,
                            "sentiment": 50.0, "momentum": 40.0}, w)
    assert val is not None
    assert 0.0 < cov < 1.0  # liquidity+breadth missing -> partial coverage
    none_val, none_cov = compute_ars({"global": None}, w)
    assert none_val is None and none_cov == 0.0
