# -*- coding: utf-8 -*-
from datetime import date

import pandas as pd

from scripts.loaders import relative_return, session_return


def _s(rows):
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.Series([r[1] for r in rows], index=idx, dtype=float)


def test_session_return_1d():
    s = _s([("2026-10-07", 100.0), ("2026-10-08", 102.0), ("2026-10-09", 101.0)])
    r, d = session_return(s, date(2026, 10, 8), 1)
    assert d == date(2026, 10, 9)
    assert abs(r - (101 / 102 - 1)) < 1e-9


def test_session_return_missing_horizon():
    s = _s([("2026-10-08", 100.0), ("2026-10-09", 101.0)])
    r, d = session_return(s, date(2026, 10, 9), 1)
    assert r is None and d is None


def test_relative_return():
    a = _s([("2026-10-08", 100.0), ("2026-10-09", 110.0)])
    b = _s([("2026-10-08", 100.0), ("2026-10-09", 105.0)])
    rel = relative_return(a, b, date(2026, 10, 8), 1)
    assert abs(rel - ((1.10 - 1) - (1.05 - 1))) < 1e-9
