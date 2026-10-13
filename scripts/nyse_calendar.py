"""NYSE trading calendar (hand-written rules; no pandas_market_calendars dependency).

Full-day NYSE holidays: New Year, MLK, Presidents, Good Friday, Memorial,
Juneteenth (2022+), Independence, Labor, Thanksgiving, Christmas.
Weekend observance: Sat -> Fri, Sun -> Mon (except New Year on Sat: not observed).
Columbus Day and Veterans Day are NOT NYSE holidays.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import numpy as np


def _nth_weekday(y: int, m: int, wd: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, wd: int) -> dt.date:
    d = dt.date(y, m + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def _easter(y: int) -> dt.date:
    a = y % 19; b, c = divmod(y, 100); d, e = divmod(b, 4)
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(y, month, day)


def _observed(d: dt.date) -> dt.date:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> frozenset:
    h = set()
    ny = dt.date(year, 1, 1)
    if ny.weekday() != 5:  # Saturday New Year is not observed on Dec 31
        h.add(_observed(ny))
    h.add(_nth_weekday(year, 1, 0, 3))   # MLK
    h.add(_nth_weekday(year, 2, 0, 3))   # Presidents
    h.add(_easter(year) - dt.timedelta(days=2))  # Good Friday
    h.add(_last_weekday(year, 5, 0))     # Memorial
    if year >= 2022:
        h.add(_observed(dt.date(year, 6, 19)))
    h.add(_observed(dt.date(year, 7, 4)))
    h.add(_nth_weekday(year, 9, 0, 1))   # Labor
    h.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    h.add(_observed(dt.date(year, 12, 25)))
    return frozenset(h)


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def prior_trading_day(d: dt.date) -> dt.date:
    x = d - dt.timedelta(days=1)
    while not is_trading_day(x):
        x -= dt.timedelta(days=1)
    return x


def holidays_between(start: dt.date, end: dt.date) -> list:
    out = []
    for y in range(min(start, end).year - 1, max(start, end).year + 2):
        out.extend(nyse_holidays(y))
    return sorted(out)


def trading_day_count(start: dt.date, end: dt.date) -> int:
    """Like np.busday_count(start, end) but with NYSE holidays."""
    return int(np.busday_count(start, end, holidays=holidays_between(start, end)))
