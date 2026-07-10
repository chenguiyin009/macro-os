"""结构解析器测试 - ZigZag 转折点 + ABCD 结构分类"""
from __future__ import annotations

import pytest

from trinity.context import OHLCV, StructureType, TrendDirection
from trinity.structure_parser import Pivot, StructureParser


def make_ohlcv(highs, lows, closes=None):
    """辅助: 从 highs/lows 构造 OHLCV 列表"""
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return [
        OHLCV(timestamp=i, open=closes[i], high=highs[i], low=lows[i], close=closes[i], volume=1000)
        for i in range(len(highs))
    ]


class TestFindPivots:
    """ZigZag 转折点识别"""

    def test_basic_zigzag(self):
        """简单的波浪走势应识别出高低交替的转折点"""
        # 构造: 上升 → 下降 → 上升
        highs = []
        lows = []
        for i in range(30):
            highs.append(100 + i * 2)
            lows.append(98 + i * 2)
        for i in range(30):
            highs.append(158 - i * 2)
            lows.append(156 - i * 2)
        for i in range(30):
            highs.append(100 + i * 2)
            lows.append(98 + i * 2)

        parser = StructureParser(threshold=0.05)
        pivots = parser.find_pivots(highs, lows)
        assert len(pivots) >= 2
        # 转折点应高低交替
        for i in range(1, len(pivots)):
            assert pivots[i].is_high != pivots[i - 1].is_high

    def test_monotonic_rise(self):
        """单边上涨: 只有一个方向"""
        highs = [100 + i for i in range(50)]
        lows = [98 + i for i in range(50)]
        parser = StructureParser(threshold=0.03)
        pivots = parser.find_pivots(highs, lows)
        assert len(pivots) >= 1
        assert pivots[0].is_high  # 单边上涨先找高点

    def test_empty_input(self):
        parser = StructureParser()
        assert parser.find_pivots([], []) == []
        assert parser.find_pivots([1], [1]) == []

    def test_threshold_sensitivity(self):
        """较大阈值应识别更少的转折点"""
        # 构造小幅波动
        highs = [100 + 5 * (1 if i % 10 < 5 else -1) for i in range(50)]
        lows = [h - 2 for h in highs]
        parser_small = StructureParser(threshold=0.01)
        parser_large = StructureParser(threshold=0.10)
        pivots_small = parser_small.find_pivots(highs, lows)
        pivots_large = parser_large.find_pivots(highs, lows)
        assert len(pivots_small) >= len(pivots_large)


class TestFractals:
    """顶底分型识别"""

    def test_top_fractal(self):
        highs = [1, 2, 5, 2, 1, 3, 7, 3, 1]
        lows = [0.5, 1, 4, 1, 0.5, 2, 6, 2, 0.5]
        parser = StructureParser()
        tops, bottoms = parser.find_fractals(highs, lows)
        assert 2 in tops   # highs[2]=5 是顶分型
        assert 6 in tops   # highs[6]=7 是顶分型

    def test_bottom_fractal(self):
        highs = [5, 3, 1, 3, 5, 7, 2, 7, 5]
        lows = [4, 2, 0, 2, 4, 6, 1, 6, 4]
        parser = StructureParser()
        tops, bottoms = parser.find_fractals(highs, lows)
        assert 2 in bottoms  # lows[2]=0 是底分型
        assert 6 in bottoms  # lows[6]=1 是底分型


class TestClassify:
    """ABCD 结构分类"""

    def test_d_structure_3_segments(self):
        """3段结构 → D 型"""
        # H-L-H-L: 高-低-高-低 (4 pivots, 3 segments)
        pivots = [
            Pivot(0, 100, is_high=True),
            Pivot(10, 80, is_high=False),
            Pivot(20, 95, is_high=True),
            Pivot(30, 75, is_high=False),
        ]
        parser = StructureParser()
        evidence = parser.classify(pivots)
        assert evidence.structure_type == StructureType.D
        assert evidence.segments == 3

    def test_a_structure_5_segments(self):
        """5段结构, 第3段最长 → A 型"""
        pivots = [
            Pivot(0, 100, is_high=True),
            Pivot(5, 90, is_high=False),
            Pivot(10, 120, is_high=True),   # 第3段 (90→120=30)
            Pivot(15, 110, is_high=False),
            Pivot(20, 130, is_high=True),
            Pivot(25, 125, is_high=False),
        ]
        parser = StructureParser()
        evidence = parser.classify(pivots)
        assert evidence.structure_type == StructureType.A
        assert evidence.segments == 5

    def test_unknown_less_than_3(self):
        """段数 < 3 → UNKNOWN"""
        pivots = [Pivot(0, 100, True), Pivot(5, 90, False)]
        parser = StructureParser()
        evidence = parser.classify(pivots)
        assert evidence.structure_type == StructureType.UNKNOWN

    def test_direction_up(self):
        """方向判断: 上涨"""
        pivots = [
            Pivot(0, 50, is_high=False),
            Pivot(5, 60, is_high=True),
            Pivot(10, 55, is_high=False),
            Pivot(15, 70, is_high=True),
        ]
        parser = StructureParser()
        evidence = parser.classify(pivots)
        assert evidence.direction == TrendDirection.UP

    def test_direction_down(self):
        """方向判断: 下跌"""
        pivots = [
            Pivot(0, 100, is_high=True),
            Pivot(5, 80, is_high=False),
            Pivot(10, 90, is_high=True),
            Pivot(15, 70, is_high=False),
        ]
        parser = StructureParser()
        evidence = parser.classify(pivots)
        assert evidence.direction == TrendDirection.DOWN


class TestEvaluateDStructure:
    """D 结构专项评估 (时空充分性用)"""

    def test_complete_d_structure(self):
        """完整 D 结构: 高低交替 3+ 段"""
        pivots = [
            Pivot(0, 100, is_high=True),
            Pivot(10, 80, is_high=False),
            Pivot(20, 95, is_high=True),    # D3 < D1 (95 < 100)
            Pivot(30, 75, is_high=False),   # D4 < D2 (75 < 80) → 破位确认
        ]
        parser = StructureParser()
        is_complete, score, evidence = parser.evaluate_d_structure(pivots)
        assert is_complete
        assert score >= 0.7
        assert any("D结构" in e for e in evidence)

    def test_insufficient_segments(self):
        """段数不足 → 不构成 D 结构"""
        pivots = [Pivot(0, 100, True), Pivot(5, 90, False)]
        parser = StructureParser()
        is_complete, score, evidence = parser.evaluate_d_structure(pivots)
        assert not is_complete
        assert score < 0.5

    def test_non_alternating(self):
        """非交替转折点 → 结构不完美"""
        pivots = [
            Pivot(0, 100, is_high=True),
            Pivot(5, 90, is_high=True),    # 连续两个高点
            Pivot(10, 80, is_high=False),
            Pivot(15, 70, is_high=False),
        ]
        parser = StructureParser()
        is_complete, score, evidence = parser.evaluate_d_structure(pivots)
        assert score < 0.5
        assert any("不完美" in e or "不充分" in e for e in evidence)

    def test_breakout_confirms(self):
        """D3<D1 且 D4<D2 → 结构必完美 (破位确认)"""
        pivots = [
            Pivot(0, 100, is_high=True),   # D1=100
            Pivot(10, 70, is_high=False),   # D2=70
            Pivot(20, 90, is_high=True),    # D3=90 < D1=100
            Pivot(30, 60, is_high=False),   # D4=60 < D2=70
        ]
        parser = StructureParser()
        _, score, evidence = parser.evaluate_d_structure(pivots)
        assert any("破位确认" in e for e in evidence)
        assert score >= 0.7


class TestParse:
    """从 OHLCV 一键解析"""

    def test_parse_returns_evidence(self):
        """parse 应返回 StructureEvidence"""
        highs = [100 + i * 2 for i in range(50)] + [198 - i * 2 for i in range(50)]
        lows = [h - 2 for h in highs]
        ohlcv = make_ohlcv(highs, lows)
        parser = StructureParser(threshold=0.05)
        evidence = parser.parse(ohlcv)
        assert evidence.structure_type != StructureType.UNKNOWN or evidence.segments < 3
        assert isinstance(evidence.segments, int)
