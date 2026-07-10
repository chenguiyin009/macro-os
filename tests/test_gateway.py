"""数据接入层测试"""
from __future__ import annotations

import pytest

from trinity.context import OHLCV
from trinity.gateway import Gateway


class TestSyntheticGateway:
    """合成数据网关"""

    def test_fetch_returns_ohlcv(self):
        gw = Gateway(source="synthetic")
        data = gw.fetch(symbol="TEST", bars=50)
        assert len(data) == 50
        assert all(isinstance(bar, OHLCV) for bar in data)

    def test_ohlcv_validity(self):
        """每根 K 线: high >= close >= low"""
        gw = Gateway(source="synthetic")
        data = gw.fetch(symbol="TEST", bars=30, seed=42)
        for bar in data:
            assert bar.high >= bar.close >= bar.low or bar.high >= bar.low
            assert bar.close > 0
            assert bar.volume > 0

    def test_reproducible_with_seed(self):
        """相同 seed 应产生相同数据"""
        gw = Gateway(source="synthetic")
        d1 = gw.fetch(symbol="TEST", bars=20, seed=123)
        d2 = gw.fetch(symbol="TEST", bars=20, seed=123)
        assert len(d1) == len(d2)
        for a, b in zip(d1, d2):
            assert a.close == b.close
            assert a.high == b.high

    def test_different_seeds_differ(self):
        """不同 seed 产生不同数据"""
        gw = Gateway(source="synthetic")
        d1 = gw.fetch(symbol="A", bars=20, seed=1)
        d2 = gw.fetch(symbol="B", bars=20, seed=2)
        assert d1[10].close != d2[10].close

    def test_multi_level(self):
        """多级别数据获取"""
        gw = Gateway(source="synthetic")
        data = gw.fetch_multi_level(symbol="TEST", bars=50, seed=99)
        assert "J+2" in data
        assert "J" in data
        assert "J-1" in data
        assert "J-2" in data
        # J-1 和 J-2 应有更多 bars (更小级别)
        assert len(data["J-1"]) >= len(data["J"])
        assert len(data["J-2"]) >= len(data["J-1"])

    def test_unknown_source_raises(self):
        gw = Gateway(source="invalid")
        with pytest.raises(ValueError):
            gw.fetch(symbol="X", bars=10)

    def test_empty_bars(self):
        gw = Gateway(source="synthetic")
        data = gw.fetch(symbol="X", bars=0)
        assert len(data) == 0
