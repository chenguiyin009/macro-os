from __future__ import annotations

import importlib
from types import SimpleNamespace


class FakeFrame:
    def __init__(self, rows: list[dict[str, float | str]]) -> None:
        self._rows = rows

    @property
    def empty(self) -> bool:
        return not self._rows

    def get(self, key: str, default=None):
        if not self._rows or key not in self._rows[0]:
            return default
        return [self._rows[0][key]]

    def iterrows(self):
        for idx, row in enumerate(self._rows):
            yield idx, row


class FakeContext:
    def __init__(self, *args, **kwargs) -> None:
        self.closed = False

    def accinfo_query(self, trd_env=None, refresh_cache=None):
        return 0, FakeFrame([{"cash": 50.0}])

    def position_list_query(self, trd_env=None, refresh_cache=None):
        return 0, FakeFrame(
            [
                {
                    "code": "US.QQQ",
                    "qty": 2,
                    "market_val": 100.0,
                    "cost_price": 40.0,
                    "unrealized_pl": 20.0,
                    "pl_ratio": 0.5,
                },
                {
                    "code": "US.AAPL",
                    "qty": 1,
                    "market_val": 50.0,
                    "cost_price": 30.0,
                    "unrealized_pl": 20.0,
                    "pl_ratio": 0.6667,
                },
            ]
        )

    def close(self) -> None:
        self.closed = True


def test_fetch_positions_merges_cash_and_security_weights(monkeypatch) -> None:
    futu_module = importlib.import_module("adapters.futu")
    monkeypatch.setattr(futu_module, "FUTU_AVAILABLE", True, raising=False)
    monkeypatch.setattr(futu_module, "OpenSecTradeContext", FakeContext, raising=False)
    monkeypatch.setattr(futu_module, "TrdEnv", SimpleNamespace(REAL="REAL", SIMULATE="SIM"), raising=False)
    monkeypatch.setattr(futu_module, "TrdMarket", SimpleNamespace(US="US", HK="HK"), raising=False)

    sensor = futu_module.FutuSensor()
    snapshot = sensor.fetch_positions()

    assert [position.ticker for position in snapshot.positions] == ["QQQ", "AAPL", "CASH"]
    assert snapshot.total_market_value == 150.0
    assert snapshot.cash_balance == 50.0
    assert snapshot.total_assets == 200.0
    assert snapshot.to_dict() == {"QQQ": 0.5, "AAPL": 0.25, "CASH": 0.25}
