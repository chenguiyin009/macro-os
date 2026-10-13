import datetime as dt
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from nyse_calendar import is_trading_day, prior_trading_day, trading_day_count


def test_open_days():
    assert is_trading_day(dt.date(2026, 10, 12))  # Columbus
    assert is_trading_day(dt.date(2026, 11, 11))  # Veterans


def test_closed_days():
    for d in [(2026, 11, 26), (2026, 12, 25), (2026, 4, 3), (2026, 7, 3), (2026, 1, 19)]:
        assert not is_trading_day(dt.date(*d))


def test_prior_and_count():
    assert prior_trading_day(dt.date(2026, 10, 13)) == dt.date(2026, 10, 12)
    assert trading_day_count(dt.date(2026, 10, 9), dt.date(2026, 10, 12)) == 1
