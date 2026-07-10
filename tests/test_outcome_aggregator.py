"""OutcomeSimulator + PerformanceAggregator 测试"""
from __future__ import annotations

import pytest

from trinity.aggregator import (
    BucketStat,
    DiagnosticReport,
    FactorAlpha,
    InteractionEffect,
    PerformanceAggregator,
)
from trinity.context import OHLCV
from trinity.core.engine import EvidenceFactor
from trinity.outcome import OutcomeResult, OutcomeSimulator


def make_ohlcv(n: int, start: float = 10.0, slope: float = 0.5) -> list[OHLCV]:
    """构造上涨趋势 K 线"""
    return [
        OHLCV(
            timestamp=i, open=start + i * slope,
            high=start + i * slope + 1, low=start + i * slope - 1,
            close=start + i * slope, volume=1000,
        )
        for i in range(n)
    ]


def make_factors(space=0.8, ma55=1.0, divergence=0.0, state=0.9):
    """构造测试用因子列表"""
    return [
        EvidenceFactor(module="J", factor="STRUCTURE", value=space, weight=0.25),
        EvidenceFactor(module="J-1", factor="MA55_PULLBACK", value=ma55, weight=0.2),
        EvidenceFactor(module="J-1", factor="DIVERGENCE", value=divergence, weight=0.15),
        EvidenceFactor(module="J+2", factor="STATE", value=state, weight=0.3),
    ]


# ========== OutcomeSimulator ==========

class TestOutcomeSimulator:
    def test_simulate_uptrend(self):
        """上涨趋势: 固定窗口收益为正"""
        data = make_ohlcv(100, start=10, slope=0.5)
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=50, market_data=data, fixed_horizon=20)
        assert result.fixed_return > 0
        assert result.fixed_mfe > 0
        assert result.fixed_mae <= 0  # 最低点不会高于入场价

    def test_simulate_downtrend(self):
        """下跌趋势: 固定窗口收益为负"""
        data = make_ohlcv(100, start=60, slope=-0.5)
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=50, market_data=data, fixed_horizon=20)
        assert result.fixed_return < 0

    def test_fixed_horizon(self):
        """固定窗口周期正确"""
        data = make_ohlcv(100)
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=10, market_data=data, fixed_horizon=15)
        # 固定窗口收益 = closes[15] / entry - 1
        entry = data[10].close
        end = data[25].close  # 10 + 15 = 25
        expected = end / entry - 1
        assert abs(result.fixed_return - expected) < 1e-10

    def test_dynamic_ma55_exit(self):
        """动态窗口: 跌破 MA55 平仓"""
        # 构造先涨后跌的走势
        data = []
        for i in range(80):
            if i < 50:
                close = 10 + i * 0.5
            else:
                close = 35 - (i - 50) * 0.8
            data.append(OHLCV(timestamp=i, open=close, high=close+1, low=close-1, close=close, volume=1000))
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=40, market_data=data, fixed_horizon=30, ma_period=55)
        # 动态持仓应因跌破 MA55 而提前结束
        assert result.dynamic_bars_held > 0
        assert result.dynamic_bars_held <= 60  # 不会超过最大持仓

    def test_edge_entry_at_end(self):
        """入场点在末尾: 返回零结果"""
        data = make_ohlcv(50)
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=49, market_data=data, fixed_horizon=20)
        assert result.fixed_return == 0

    def test_edge_invalid_index(self):
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=-1, market_data=make_ohlcv(50))
        assert result.fixed_return == 0

    def test_batch(self):
        data = make_ohlcv(100)
        sim = OutcomeSimulator()
        results = sim.simulate_batch([10, 20, 30], data, fixed_horizon=10)
        assert len(results) == 3
        assert all(r.fixed_return > 0 for r in results)  # 上涨趋势

    def test_risk_reward_ratio(self):
        data = make_ohlcv(100, start=10, slope=0.5)
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=50, market_data=data, fixed_horizon=20)
        assert result.risk_reward_ratio > 0

    def test_to_dict(self):
        r = OutcomeResult(0.05, 0.08, -0.02, 0.06, 0.10, -0.01, 15)
        d = r.to_dict()
        assert d["fixed_return"] == 0.05
        assert d["dynamic_bars_held"] == 15


# ========== PerformanceAggregator ==========

class TestBucketAnalysis:
    def test_basic_buckets(self):
        agg = PerformanceAggregator()
        # 高 Space Score 的决策盈利
        for _ in range(10):
            agg.add_decision(make_factors(space=0.9), OutcomeResult(0.05, 0.08, -0.02, 0.06, 0.10, -0.01, 15))
        # 低 Space Score 的决策亏损
        for _ in range(10):
            agg.add_decision(make_factors(space=0.1), OutcomeResult(-0.03, 0.01, -0.05, -0.02, 0.02, -0.04, 10))

        buckets = agg.bucket_analysis("STRUCTURE")
        assert len(buckets) >= 2
        high_bucket = next(b for b in buckets if b.bucket == "high")
        low_bucket = next(b for b in buckets if b.bucket == "low")
        assert high_bucket.avg_return > low_bucket.avg_return
        assert high_bucket.win_rate == 1.0
        assert low_bucket.win_rate == 0.0

    def test_empty_factor(self):
        agg = PerformanceAggregator()
        assert agg.bucket_analysis("NONEXISTENT") == []


class TestFactorAlpha:
    def test_positive_alpha(self):
        """高值盈利, 低值亏损 → 正 Alpha"""
        agg = PerformanceAggregator()
        for _ in range(10):
            agg.add_decision(make_factors(space=0.9), OutcomeResult(0.05, 0.08, -0.02, 0.06, 0.10, -0.01, 15))
        for _ in range(10):
            agg.add_decision(make_factors(space=0.1), OutcomeResult(-0.03, 0.01, -0.05, -0.02, 0.02, -0.04, 10))

        alpha = agg.factor_alpha("STRUCTURE")
        assert alpha is not None
        assert alpha.alpha > 0
        assert alpha.significance == "strong"

    def test_no_alpha(self):
        """高值低值收益相同 → 弱 Alpha"""
        agg = PerformanceAggregator()
        for _ in range(5):
            agg.add_decision(make_factors(space=0.9), OutcomeResult(0.02, 0.03, -0.01, 0.02, 0.03, -0.01, 10))
        for _ in range(5):
            agg.add_decision(make_factors(space=0.1), OutcomeResult(0.02, 0.03, -0.01, 0.02, 0.03, -0.01, 10))

        alpha = agg.factor_alpha("STRUCTURE")
        assert alpha is not None
        assert abs(alpha.alpha) < 0.01
        assert alpha.significance == "weak"

    def test_insufficient_data(self):
        agg = PerformanceAggregator()
        agg.add_decision(make_factors(space=0.9), OutcomeResult(0.05, 0.08, -0.02, 0, 0, 0, 0))
        assert agg.factor_alpha("STRUCTURE") is None  # 只有高组, 无低组


class TestInteractionEffect:
    def test_synergy(self):
        """协同效应: 两因子同时高时收益爆发"""
        agg = PerformanceAggregator()
        # A高+B高 → 大幅盈利
        for _ in range(5):
            agg.add_decision(
                make_factors(space=0.9, ma55=1.0),
                OutcomeResult(0.10, 0.15, -0.02, 0, 0, 0, 0),
            )
        # A高+B低 → 小亏
        for _ in range(5):
            agg.add_decision(
                make_factors(space=0.9, ma55=0.0),
                OutcomeResult(-0.01, 0.02, -0.03, 0, 0, 0, 0),
            )
        # A低+B高 → 小亏
        for _ in range(5):
            agg.add_decision(
                make_factors(space=0.1, ma55=1.0),
                OutcomeResult(-0.01, 0.02, -0.03, 0, 0, 0, 0),
            )
        # A低+B低 → 小亏
        for _ in range(5):
            agg.add_decision(
                make_factors(space=0.1, ma55=0.0),
                OutcomeResult(-0.01, 0.02, -0.03, 0, 0, 0, 0),
            )

        ie = agg.interaction_effect("STRUCTURE", "MA55_PULLBACK")
        assert ie is not None
        assert ie.interaction > 0.02  # 协同效应
        assert "协同" in ie.interpretation

    def test_no_interaction(self):
        """无交互: 两因子独立起作用"""
        agg = PerformanceAggregator()
        for _ in range(5):
            agg.add_decision(make_factors(space=0.9, ma55=1.0), OutcomeResult(0.03, 0.05, -0.01, 0, 0, 0, 0))
        for _ in range(5):
            agg.add_decision(make_factors(space=0.9, ma55=0.0), OutcomeResult(0.02, 0.04, -0.01, 0, 0, 0, 0))
        for _ in range(5):
            agg.add_decision(make_factors(space=0.1, ma55=1.0), OutcomeResult(0.01, 0.03, -0.01, 0, 0, 0, 0))
        for _ in range(5):
            agg.add_decision(make_factors(space=0.1, ma55=0.0), OutcomeResult(0.00, 0.02, -0.01, 0, 0, 0, 0))

        ie = agg.interaction_effect("STRUCTURE", "MA55_PULLBACK")
        assert ie is not None
        assert abs(ie.interaction) < 0.02
        assert "无明显交互" in ie.interpretation

    def test_insufficient_cells(self):
        """象限不足: 无法计算交互效应"""
        agg = PerformanceAggregator()
        for _ in range(5):
            agg.add_decision(make_factors(space=0.9, ma55=1.0), OutcomeResult(0.05, 0.08, -0.02, 0, 0, 0, 0))
        # 只有 1 个象限
        assert agg.interaction_effect("STRUCTURE", "MA55_PULLBACK") is None


class TestPartialCorrelation:
    def test_basic(self):
        """偏相关: 控制一个因子后, 另一个因子与收益的相关性"""
        agg = PerformanceAggregator()
        # 构造: STRUCTURE 与收益正相关, 但 MA55 也相关 (混淆)
        # 确保 MA55 有足够的高组(>0.66)和低组(<0.33)样本
        for i in range(10):
            space = 0.3 + i * 0.06
            ret = (space - 0.5) * 0.2
            agg.add_decision(
                make_factors(space=min(space, 1.0), ma55=0.9),  # MA55 恒高
                OutcomeResult(ret, ret + 0.02, ret - 0.02, 0, 0, 0, 0),
            )
        for i in range(10):
            space = 0.3 + i * 0.06
            ret = (space - 0.5) * 0.15  # MA55 低组中 STRUCTURE 仍有正相关但斜率不同
            agg.add_decision(
                make_factors(space=min(space, 1.0), ma55=0.1),  # MA55 恒低
                OutcomeResult(ret, ret + 0.02, ret - 0.02, 0, 0, 0, 0),
            )
        pc = agg.partial_correlation("STRUCTURE", "MA55_PULLBACK")
        assert pc is not None
        assert "partial_correlation" in pc

    def test_insufficient_data(self):
        agg = PerformanceAggregator()
        assert agg.partial_correlation("STRUCTURE", "MA55_PULLBACK") is None


class TestDiagnose:
    def test_full_diagnostic(self):
        """完整自诊断"""
        agg = PerformanceAggregator()
        for _ in range(10):
            agg.add_decision(
                make_factors(space=0.9, ma55=1.0),
                OutcomeResult(0.08, 0.12, -0.02, 0.06, 0.10, -0.01, 18),
            )
        for _ in range(10):
            agg.add_decision(
                make_factors(space=0.1, ma55=0.0),
                OutcomeResult(-0.04, 0.01, -0.06, -0.03, 0.02, -0.05, 8),
            )

        report = agg.diagnose()
        assert isinstance(report, DiagnosticReport)
        assert report.sample_size == 20
        assert report.avg_return > 0  # 整体盈利
        assert len(report.top_factors) > 0
        assert len(report.recommendations) > 0

    def test_recommendations_content(self):
        """建议应包含因子名称"""
        agg = PerformanceAggregator()
        for _ in range(10):
            agg.add_decision(make_factors(space=0.9), OutcomeResult(0.05, 0.08, -0.02, 0, 0, 0, 0))
        for _ in range(10):
            agg.add_decision(make_factors(space=0.1), OutcomeResult(-0.03, 0.01, -0.05, 0, 0, 0, 0))

        report = agg.diagnose()
        rec_text = " ".join(report.recommendations)
        assert "STRUCTURE" in rec_text or "权重" in rec_text

    def test_empty_diagnostic(self):
        """空数据自诊断不崩溃"""
        agg = PerformanceAggregator()
        report = agg.diagnose()
        assert report.sample_size == 0
        assert report.avg_return == 0

    def test_interaction_in_diagnostic(self):
        """自诊断应检测交互效应"""
        agg = PerformanceAggregator()
        for _ in range(5):
            agg.add_decision(make_factors(space=0.9, ma55=1.0), OutcomeResult(0.10, 0.15, -0.02, 0, 0, 0, 0))
        for _ in range(5):
            agg.add_decision(make_factors(space=0.9, ma55=0.0), OutcomeResult(-0.01, 0.02, -0.03, 0, 0, 0, 0))
        for _ in range(5):
            agg.add_decision(make_factors(space=0.1, ma55=1.0), OutcomeResult(-0.01, 0.02, -0.03, 0, 0, 0, 0))
        for _ in range(5):
            agg.add_decision(make_factors(space=0.1, ma55=0.0), OutcomeResult(-0.01, 0.02, -0.03, 0, 0, 0, 0))

        report = agg.diagnose()
        assert len(report.top_interactions) > 0
        assert any("协同" in ie.interpretation for ie in report.top_interactions)
