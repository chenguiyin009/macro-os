from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from adapters.tradingview import TradingViewAdapter
from core.schemas import DataSource


# Disable all network/cache fallbacks so tests are deterministic.
os.environ.setdefault("MACRO_OS_TV_MACRO_SIDECAR_ENABLED", "0")
os.environ.setdefault("MACRO_OS_YFINANCE_ENABLED", "0")
os.environ.setdefault("MACRO_OS_FRED_ENABLED", "0")
os.environ.setdefault("MACRO_OS_MACRO_CACHE_ENABLED", "0")


def test_fetch_reads_latest_feature_snapshot_from_relay_log(tmp_path) -> None:
    log_path = tmp_path / "tv-desktop-monitor.out.log"
    log_path.write_text(
        "\n".join(
            [
                '{"ts":"2026-07-06T00:00:00Z","level":"info","message":"relay started"}',
                json.dumps(
                    {
                        "dxy": 104.5,
                        "vix": 18.2,
                        "tip": 0.6,
                        "gld": 2350.0,
                        "jnk": 320.0,
                        "equity_tech_rotation": 0.15,
                    },
                    indent=2,
                ),
                json.dumps(
                    {
                        "ts": "2026-07-06T00:01:00Z",
                        "level": "info",
                        "message": "health ping",
                        "status": "ok",
                        "memory_usage": "128MB",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = TradingViewAdapter(mcp_script_path="", relay_log_path=log_path)
    snapshot = adapter.fetch()

    assert snapshot is not None
    assert snapshot.dxy == 104.5
    assert snapshot.vix == 18.2
    assert snapshot.tips_yield == 0.6
    assert snapshot.gold == 2350.0
    assert snapshot.hy_credit_spread == 320.0
    assert snapshot.equity_tech_rotation == 0.15
    assert snapshot.source == DataSource.MCP

    health = adapter.health()
    assert health["relay_log_exists"] is True
    assert health["last_success_time"] is not None
    assert health["last_error"] is None


def test_fetch_falls_back_to_mock_when_relay_log_is_stale(tmp_path) -> None:
    log_path = tmp_path / "tv-desktop-monitor.out.log"
    log_path.write_text(
        json.dumps(
            {
                "dxy": 104.5,
                "vix": 18.2,
                "tip": 0.6,
                "gld": 2350.0,
                "jnk": 320.0,
                "equity_tech_rotation": 0.15,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    stale_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    os.utime(log_path, (stale_at.timestamp(), stale_at.timestamp()))

    adapter = TradingViewAdapter(mcp_script_path="", relay_log_path=log_path)
    snapshot = adapter.fetch()
    health = adapter.health()

    assert snapshot is not None
    assert snapshot.source == DataSource.MOCK
    assert health["relay_log_exists"] is True
    assert health["relay_log_is_stale"] is True
    assert health["relay_log_age_seconds"] >= 360
    assert health["last_success_time"] is None
    assert health["last_error"]
