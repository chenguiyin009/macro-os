"""时空引擎测试 - 时间对称度 + 空间完整度"""
from __future__ import annotations

import pytest

from trinity.context import OHLCV, SpacetimeScore
from trinity.spacetime_engine import SpacetimeEngine


def make_decline_ohlcv(
    bars: int,
    start_price: float,
    decline_per_bar: float,
    volatility: float = 1.0,
) -> list[OHLCV]:
    """构造下跌 K 线序列"""
    result = []
    for i in range(bars):
        close = start_price - i * decline_per_bar
        high = close + volatility
        low = close - volatility
        result.append(OHLCV(timestamp=i, open=close, high=high, low=low, close=close, volume=1000))
    return result


def make_wave_ohlcv(
    segments: list[tuple[int, float, float]],  # (bars, start_price, slope)
    volatility: float = 1.0,
) -> list[OHLCV]:
    """构造多段波形 K 线

    Args:
        segments: [(bars, start_price, slope), ...]
          slope > 0 上涨, slope < 0 下跌
    """
    result = []
    ts = 0
    for bars, start, slope in segments:
        for i in range(bars):
            close = start + i * slope
            result.append(OHLCV(
                timestamp=ts, open=close, high=close + volatility,
                low=close - volatility, close=close, volume=1000,
            ))
            ts += 1
    return result


class TestTimeScore:
    """时间对称度"""

    def test_symmetric_declines(self):
        """两段等长下跌 → 高分"""
        # 上涨18周 → 下跌18周 → 上涨18周 → 下跌18周
        ohlcv = make_wave_ohlcv([
            (18, 100, 1.0),    # 上涨
            (18, 118, -1.0),   # 下跌
            (18, 100, 1.0),    # 上涨
            (18, 118, -1.0),   # 下跌
        ], volatility=2.0)
        engine = SpacetimeEngine(time_tolerance=3, zigzag_threshold=0.05)
        score, evidence = engine.calc_time_score(ohlcv)
        assert score >= 0.7, f"对称下跌时间分应高, got {score}"
        assert any("对称" in e or "容差" in e for e in evidence)

    def test_asymmetric_declines(self):
        """两段差异很大的下跌 → 低分"""
        ohlcv = make_wave_ohlcv([
            (30, 100, 1.0),     # 上涨30周
            (5, 130, -2.0),     # 下跌5周 (很短)
            (30, 120, 1.0),     # 上涨30周
            (30, 150, -1.0),    # 下跌30周 (很长)
        ], volatility=2.0)
        engine = SpacetimeEngine(time_tolerance=3, zigzag_threshold=0.05)
        score, evidence = engine.calc_time_score(ohlcv)
        assert score < 0.8, f"不对称下跌时间分应低, got {score}"

    def test_insufficient_data(self):
        """数据不足"""
        ohlcv = make_decline_ohlcv(3, 100, 1.0)
        engine = SpacetimeEngine()
        score, evidence = engine.calc_time_score(ohlcv)
        assert score == 0.0

    def test_time_window_bonus(self):
        """命中变盘窗口应加分"""
        # 18周变盘窗口
        ohlcv = make_wave_ohlcv([
            (18, 100, 1.0),
            (18, 118, -1.0),
            (18, 100, 1.0),
            (18, 118, -1.0),
        ], volatility=2.0)
        engine = SpacetimeEngine(time_tolerance=3, time_windows=(18,), zigzag_threshold=0.05)
        score, evidence = engine.calc_time_score(ohlcv)
        assert any("变盘窗口" in e for e in evidence)


class TestSpaceScore:
    """空间完整度"""

    def test_with_d_structure(self):
        """包含 D 结构 → 高分"""
        # 构造有高低交替转折的下跌
        ohlcv = make_wave_ohlcv([
            (15, 100, 1.0),     # 上涨
            (15, 115, -0.5),    # 小跌
            (10, 107, 0.5),     # 小涨
            (15, 112, -1.0),    # 大跌 (D结构)
        ], volatility=3.0)
        engine = SpacetimeEngine(zigzag_threshold=0.03)
        score, evidence = engine.calc_space_score(ohlcv)
        assert score > 0, "有结构的空间分应 > 0"

    def test_insufficient_data(self):
        """数据不足"""
        ohlcv = make_decline_ohlcv(3, 100, 1.0)
        engine = SpacetimeEngine()
        score, evidence = engine.calc_space_score(ohlcv)
        assert score == 0.0

    def test_with_daily_data(self):
        """有日线数据时应检查日线 D 结构"""
        weekly = make_wave_ohlcv([
            (15, 100, 1.0),
            (15, 115, -1.0),
            (10, 100, 0.5),
            (15, 105, -1.0),
        ], volatility=3.0)
        # 构造日线数据 (5x)
        daily = make_wave_ohlcv([
            (75, 100, 0.2),
            (75, 115, -0.2),
            (50, 100, 0.1),
            (75, 105, -0.2),
        ], volatility=1.0)
        engine = SpacetimeEngine(zigzag_threshold=0.03)
        score, evidence = engine.calc_space_score(weekly, daily)
        assert score > 0
        assert any("日线" in e for e in evidence)


class TestEvaluate:
    """综合评估"""

    def test_returns_spacetime_score(self):
        ohlcv = make_wave_ohlcv([
            (18, 100, 1.0),
            (18, 118, -1.0),
            (18, 100, 1.0),
            (18, 118, -1.0),
        ], volatility=2.0)
        engine = SpacetimeEngine(time_tolerance=3, zigzag_threshold=0.05)
        result = engine.evaluate(ohlcv)
        assert isinstance(result, SpacetimeScore)
        assert 0 <= result.time_score <= 1
        assert 0 <= result.space_score <= 1
        assert len(result.time_evidence) > 0
        assert len(result.space_evidence) > 0

    def test_overall_is_weighted(self):
        """overall = time*0.4 + space*0.6 (蓝图 v2.1 加权公式)"""
        ohlcv = make_wave_ohlcv([
            (15, 100, 1.0),
            (15, 115, -1.0),
        ], volatility=2.0)
        engine = SpacetimeEngine(zigzag_threshold=0.05)
        result = engine.evaluate(ohlcv)
        expected = result.time_score * 0.4 + result.space_score * 0.6
        assert abs(result.overall - expected) < 1e-10

    def test_space_weighted_higher(self):
        """空间权重更高: 同样分值下, space 高则 overall 更高"""
        s1 = SpacetimeScore(time_score=1.0, space_score=0.0)
        s2 = SpacetimeScore(time_score=0.0, space_score=1.0)
        assert s2.overall > s1.overall, "空间分应比时间分对 overall 贡献更大"

    def test_space_sufficient_for_strong_add(self):
        """蓝图 v2.1: STRONG_ADD 要求 Space Score > 0.75"""
        assert SpacetimeScore(time_score=0.3, space_score=0.8).space_sufficient_for_strong_add
        assert not SpacetimeScore(time_score=1.0, space_score=0.7).space_sufficient_for_strong_add

    def test_sufficient_threshold(self):
        """sufficient 判断 (overall >= 0.7)"""
        score = SpacetimeScore(time_score=0.8, space_score=0.8)
        assert score.sufficient

        score = SpacetimeScore(time_score=0.3, space_score=0.4)
        assert not score.sufficient
