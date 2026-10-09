"""Incremental merge, 30/60/120 backoff, and fallback-does-not-clobber."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.bar_sources import (
    RATE_LIMIT_BACKOFFS,
    cache_covers,
    completed_us_cash_session,
    fetch_with_fallback,
    merge_bars,
    reset_yf_circuit,
    yf_download_bars,
)


def _frame(rows):
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {"Close": [r[1] for r in rows], "Volume": [r[2] for r in rows]},
        index=idx,
    )


def test_completed_session_before_close_is_prior_day():
    # Thursday 2026-10-08 10:00 ET -> completed session is Wednesday 2026-10-07
    now = datetime(2026, 10, 8, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert completed_us_cash_session(now) == date(2026, 10, 7)


def test_completed_session_after_close_is_same_day():
    now = datetime(2026, 10, 8, 16, 5, tzinfo=ZoneInfo("America/New_York"))
    assert completed_us_cash_session(now) == date(2026, 10, 8)


def test_merge_keeps_history_refreshes_only_completed_bar_drops_partial():
    existing = _frame([
        ("2026-10-01", 100.0, 1),
        ("2026-10-02", 101.0, 1),
        ("2026-10-06", 110.0, 5),
        ("2026-10-07", 90.0, 2),   # half bar, will be refreshed
        ("2026-10-08", 50.0, 1),   # partial session, must be dropped
    ])
    fresh = _frame([
        ("2026-10-01", 999.0, 9),  # must NOT replace
        ("2026-10-06", 999.0, 9),  # must NOT replace
        ("2026-10-07", 112.0, 8),  # completed bar refresh
        ("2026-10-08", 77.0, 3),   # still partial relative to completed=10-07
    ])
    out = merge_bars(existing, fresh, date(2026, 10, 7))
    assert list(out.index.date) == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 6), date(2026, 10, 7)]
    assert out.loc["2026-10-01", "Close"] == 100.0
    assert out.loc["2026-10-06", "Close"] == 110.0
    assert out.loc["2026-10-07", "Close"] == 112.0
    assert out.loc["2026-10-07", "Volume"] == 8


def test_merge_fills_only_missing_dates():
    existing = _frame([("2026-10-01", 100.0, 1), ("2026-10-02", 101.0, 1)])
    fresh = _frame([
        ("2026-10-02", 999.0, 9),
        ("2026-10-05", 105.0, 4),
        ("2026-10-06", 106.0, 4),
    ])
    out = merge_bars(existing, fresh, date(2026, 10, 6))
    assert out.loc["2026-10-02", "Close"] == 101.0
    assert out.loc["2026-10-05", "Close"] == 105.0
    assert out.loc["2026-10-06", "Close"] == 106.0


def test_rate_limit_backoff_is_30_60_120_then_circuit():
    reset_yf_circuit()
    sleeps = []

    class YFRateLimitError(Exception):
        pass

    def boom(ticker, **kwargs):
        raise YFRateLimitError("429 too many requests")

    frame, reason = yf_download_bars("SPY", period="5d", attempts=4, sleep=sleeps.append, download=boom)
    assert frame is None
    assert sleeps == list(RATE_LIMIT_BACKOFFS)
    assert "rate_limited" in reason
    calls = {"n": 0}

    def should_not_run(ticker, **kwargs):
        calls["n"] += 1
        raise AssertionError("second ticker must not hit yfinance after circuit trip")

    frame2, reason2 = yf_download_bars("QQQ", period="5d", attempts=4, sleep=sleeps.append, download=should_not_run)
    assert frame2 is None
    assert calls["n"] == 0
    assert "skipped" in reason2
    reset_yf_circuit()


def test_empty_payload_waits_30s_once_not_a_burst():
    reset_yf_circuit()
    sleeps = []

    def empty(ticker, **kwargs):
        return pd.DataFrame()

    frame, reason = yf_download_bars("SPY", period="5d", attempts=4, sleep=sleeps.append, download=empty)
    assert frame is None
    assert sleeps == [30.0]
    assert "empty" in reason
    reset_yf_circuit()


def test_fallback_does_not_replace_good_rows_and_tv_429_does_not_block():
    reset_yf_circuit()
    existing = _frame([
        ("2026-09-01", 10.0, 1),
        ("2026-10-06", 20.0, 2),
    ])
    # pad to min_bars so we take the incremental path
    extra = [("2026-08-%02d" % d, 10.0, 1) for d in range(1, 29)]
    existing = pd.concat([_frame(extra), existing])
    tv_calls = {"n": 0}
    stooq_calls = {"n": 0}

    def yf_fail(ticker, **kwargs):
        raise RuntimeError("429 too many requests")

    def tv(ticker, **kwargs):
        tv_calls["n"] += 1
        return None, "tv rate_limited AMEX:SPY: HTTP 429"

    def stooq(ticker, **kwargs):
        stooq_calls["n"] += 1
        # offers a conflicting history row plus the missing completed bar
        return _frame([
            ("2026-10-06", 999.0, 9),
            ("2026-10-07", 21.0, 3),
        ]), ""

    now = datetime(2026, 10, 8, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    merged, notes, source = fetch_with_fallback(
        "SPY",
        existing,
        min_bars=20,
        attempts=1,
        sleep=lambda s: None,
        download=yf_fail,
        tv_fetch=tv,
        stooq_fetch=stooq,
        now=now,
    )
    assert tv_calls["n"] == 1
    assert stooq_calls["n"] == 1
    assert source == "stooq"
    assert any("rate_limited" in n for n in notes)
    assert merged.loc["2026-10-06", "Close"] == 20.0
    assert merged.loc["2026-10-07", "Close"] == 21.0
    assert "2026-09-01" in merged.index.astype(str).tolist() or merged.loc["2026-09-01", "Close"] == 10.0
    reset_yf_circuit()


def test_fresh_cache_skips_network():
    reset_yf_circuit()
    rows = [("2026-08-%02d" % d, 1.0, 1) for d in range(1, 29)]
    rows += [("2026-10-06", 2.0, 1), ("2026-10-07", 3.0, 1)]
    existing = _frame(rows)
    now = datetime(2026, 10, 8, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert cache_covers(existing, date(2026, 10, 7), 20)

    def boom(ticker, **kwargs):
        raise AssertionError("network should not be called")

    merged, notes, source = fetch_with_fallback(
        "QQQ", existing, min_bars=20, download=boom, now=now,
    )
    assert source == "cache"
    assert notes == []
    assert merged.loc["2026-10-07", "Close"] == 3.0
    reset_yf_circuit()
