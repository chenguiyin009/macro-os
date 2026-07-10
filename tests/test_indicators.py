"""指标引擎测试 - 验证 MACD / MA / Bollinger 数值正确性"""
from __future__ import annotations

import math

import pytest

from trinity.context import OHLCV
from trinity.indicators import (
    calc_bollinger,
    calc_macd,
    compute_indicators,
    ema,
    last_valid,
    sma,
)


# ========== SMA 测试 ==========

class TestSMA:
    def test_basic(self):
        vals = [1, 2, 3, 4, 5]
        result = sma(vals, 3)
        assert result[:2] == [None, None]
        assert result[2] == pytest.approx(2.0)   # (1+2+3)/3
        assert result[3] == pytest.approx(3.0)   # (2+3+4)/3
        assert result[4] == pytest.approx(4.0)   # (3+4+5)/3

    def test_period_equals_length(self):
        vals = [10, 20, 30]
        result = sma(vals, 3)
        assert result[2] == pytest.approx(20.0)

    def test_insufficient_data(self):
        assert sma([1, 2], 5) == [None, None]

    def test_invalid_period(self):
        with pytest.raises(ValueError):
            sma([1, 2, 3], 0)

    def test_empty(self):
        assert sma([], 3) == []


# ========== EMA 测试 ==========

class TestEMA:
    def test_seed_is_sma(self):
        """EMA 种子值应等于前 period 个值的 SMA"""
        vals = [1, 2, 3, 4, 5, 6]
        period = 3
        result = ema(vals, period)
        assert result[:2] == [None, None]
        # 种子 = (1+2+3)/3 = 2.0
        assert result[2] == pytest.approx(2.0)

    def test_recurrence(self):
        """验证 EMA 递推公式"""
        vals = [1, 2, 3, 4, 5, 6]
        period = 3
        result = ema(vals, period)
        mult = 2.0 / (period + 1)
        # result[3] = vals[3] * mult + result[2] * (1-mult)
        expected = 4 * mult + 2.0 * (1 - mult)
        assert result[3] == pytest.approx(expected)

    def test_insufficient(self):
        assert ema([1, 2], 5) == [None, None]


# ========== MACD 测试 ==========

class TestMACD:
    def test_dif_is_ema_fast_minus_ema_slow(self):
        closes = [float(i) for i in range(1, 60)]
        dif, dea, hist = calc_macd(closes, 12, 26, 9)
        # DIF 在 index 25 (slow-1) 开始有值
        assert dif[24] is None
        assert dif[25] is not None

    def test_hist_formula(self):
        """hist = (dif - dea) * 2"""
        closes = [float(i) for i in range(1, 80)]
        dif, dea, hist = calc_macd(closes, 12, 26, 9)
        # 找到 dif/dea/hist 都非 None 的位置
        for i in range(len(closes)):
            if dif[i] is not None and dea[i] is not None and hist[i] is not None:
                expected = (dif[i] - dea[i]) * 2
                assert hist[i] == pytest.approx(expected, abs=1e-10)

    def test_dea_starts_after_dif(self):
        closes = [float(i) for i in range(1, 80)]
        dif, dea, _ = calc_macd(closes, 12, 26, 9)
        dif_start = next(i for i, v in enumerate(dif) if v is not None)
        dea_start = next(i for i, v in enumerate(dea) if v is not None)
        assert dea_start > dif_start

    def test_uptrend_positive_dif(self):
        """持续上涨 → DIF 为正"""
        closes = [10 + i * 0.5 for i in range(60)]
        dif, _, _ = calc_macd(closes)
        assert last_valid(dif) > 0

    def test_downtrend_negative_dif(self):
        """持续下跌 → DIF 为负"""
        closes = [60 - i * 0.5 for i in range(60)]
        dif, _, _ = calc_macd(closes)
        assert last_valid(dif) < 0


# ========== Bollinger 测试 ==========

class TestBollinger:
    def test_mid_is_sma(self):
        closes = [float(i) for i in range(1, 31)]
        upper, mid, lower = calc_bollinger(closes, 20, 2.0)
        sma20 = sma(closes, 20)
        for i in range(len(closes)):
            assert mid[i] == sma20[i]

    def test_symmetry(self):
        """上下轨关于中轨对称"""
        closes = [float(i) for i in range(1, 31)]
        upper, mid, lower = calc_bollinger(closes, 20, 2.0)
        for i in range(19, 30):
            if mid[i] is not None:
                assert upper[i] - mid[i] == pytest.approx(mid[i] - lower[i])

    def test_constant_series(self):
        """常数序列 → 上下轨 = 中轨 (std=0)"""
        closes = [50.0] * 25
        upper, mid, lower = calc_bollinger(closes, 20, 2.0)
        assert mid[24] == pytest.approx(50.0)
        assert upper[24] == pytest.approx(50.0)
        assert lower[24] == pytest.approx(50.0)

    def test_insufficient(self):
        upper, mid, lower = calc_bollinger([1, 2], 20)
        assert all(v is None for v in upper)
        assert all(v is None for v in mid)


# ========== compute_indicators 集成测试 ==========

class TestComputeIndicators:
    def test_returns_all_keys(self):
        ohlcv = [
            OHLCV(timestamp=i, open=10 + i, high=11 + i, low=9 + i, close=10 + i, volume=1000)
            for i in range(100)
        ]
        result = compute_indicators(ohlcv)
        for key in ("closes", "ma55", "ma233", "dif", "dea", "macd_hist",
                     "boll_upper", "boll_mid", "boll_lower"):
            assert key in result

    def test_ma55_correct_length(self):
        ohlcv = [
            OHLCV(timestamp=i, open=10, high=11, low=9, close=10 + i * 0.1, volume=1000)
            for i in range(60)
        ]
        result = compute_indicators(ohlcv)
        assert len(result["ma55"]) == 60
