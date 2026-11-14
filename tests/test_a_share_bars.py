# -*- coding: utf-8 -*-
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import a_share_bars as ab  # noqa: E402

D = pd.to_datetime(["2026-11-11", "2026-11-12", "2026-11-13"])


def test_pick_latest_prefers_newer_then_yf():
    yf = pd.Series([2.59, 2.524, np.nan], index=D)
    sina = pd.Series([2.59, 2.524, 2.442], index=D)
    s, src = ab.pick_latest(yf, sina, dt.date(2026, 11, 13))
    assert src == "sina" and s.iloc[-1] == 2.442
    s, src = ab.pick_latest(sina, sina, dt.date(2026, 11, 13))
    assert src == "yfinance"
    s, src = ab.pick_latest(yf, None, dt.date(2026, 11, 13))
    assert src == "yfinance" and s.index[-1].date() == dt.date(2026, 11, 12)
    assert ab.pick_latest(None, None, dt.date(2026, 11, 13)) == (None, None)


def test_append_missing_never_touches_old_rows():
    cache = pd.Series([2.60, 2.50], index=D[:2])  # 故意与 fresh 不同
    fresh = pd.Series([2.59, 2.524, 2.442], index=D)
    m, added = ab.append_missing(cache, fresh, dt.date(2026, 11, 13))
    assert added == [dt.date(2026, 11, 13)]
    assert list(m.values) == [2.60, 2.50, 2.442]
    m, added = ab.append_missing(cache, fresh, dt.date(2026, 11, 12))
    assert added == []


def test_sina_daily_with_fake_get():
    s, e = ab.sina_daily("159915.SZ", get=lambda url: '[{"day":"2026-11-13","close":"2.442"}]')
    assert e is None and float(s.iloc[-1]) == 2.442
    s, e = ab.sina_daily("sz159915", get=lambda url: (_ for _ in ()).throw(OSError("x")))
    assert s is None and "sina" in e
