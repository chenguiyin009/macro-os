"""Trinity OS v2.1 - PerformanceAggregator (蓝图阶段二)

归因引擎的终环：将 OutcomeSimulator 的收益结果与 AttributionEngine 的因子数据
对接，进行分桶统计、因子 Alpha 归因、偏相关分析和交互效应检测。

交互效应检测是核心亮点：
  很多因子本身不强，但在特定组合下（如 Space Score 高 且 MA55 有效）
  会产生 1+1 > 2 的爆发力。通过 2x2 factorial 设计检测这种交互效应，
  让系统具备自我诊断能力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from trinity.core.engine import EvidenceFactor
from trinity.outcome import OutcomeResult


@dataclass
class BucketStat:
    """分桶统计"""
    factor_name: str
    bucket: str               # "high" / "medium" / "low"
    count: int
    avg_return: float         # 平均收益率
    win_rate: float           # 胜率
    avg_mfe: float            # 平均最大浮盈
    avg_mae: float            # 平均最大浮亏


@dataclass
class FactorAlpha:
    """单因子 Alpha 贡献"""
    factor_name: str
    alpha: float              # 因子 Alpha (高组收益 - 低组收益)
    high_avg: float           # 高组平均收益
    low_avg: float            # 低组平均收益
    high_count: int
    low_count: int
    significance: str         # "strong" / "moderate" / "weak"


@dataclass
class InteractionEffect:
    """交互效应 (2x2 factorial)

    interaction > 0: 协同效应 (1+1 > 2)
    interaction < 0: 拮抗效应 (1+1 < 2)
    interaction ≈ 0: 无交互
    """
    factor_a: str
    factor_b: str
    interaction: float        # 交互效应值
    both_high_avg: float      # A高+B高 平均收益
    a_high_b_low_avg: float   # A高+B低 平均收益
    a_low_b_high_avg: float   # A低+B高 平均收益
    both_low_avg: float       # A低+B低 平均收益
    interpretation: str       # 人可读解释


@dataclass
class DiagnosticReport:
    """自诊断报告"""
    top_factors: List[Tuple[str, float]]          # 按 Alpha 排序的因子
    top_interactions: List[InteractionEffect]      # 最强交互效应
    recommendations: List[str]                     # 权重调整建议
    sample_size: int
    avg_return: float
    win_rate: float
    symbol: str = ""                               # 报告归属标的 (蓝图语义兼容)


class PerformanceAggregator:
    """性能归因聚合器

    用法:
        agg = PerformanceAggregator()
        # 注册每次决策的因子和结果
        agg.add_decision(factors, outcome)
        # 分析
        report = agg.diagnose()
    """

    # 分桶阈值: 因子值 > 0.66 为 high, < 0.33 为 low
    HIGH_THRESHOLD = 0.66
    LOW_THRESHOLD = 0.33

    def __init__(self):
        self._records: List[Tuple[List[EvidenceFactor], OutcomeResult]] = []

    def add_decision(
        self,
        factors: List[EvidenceFactor],
        outcome: OutcomeResult,
    ) -> None:
        """注册一次决策的因子数据和收益结果"""
        self._records.append((factors, outcome))

    def add_batch(
        self,
        factors_list: List[List[EvidenceFactor]],
        outcomes: List[OutcomeResult],
    ) -> None:
        """批量注册"""
        for factors, outcome in zip(factors_list, outcomes):
            self.add_decision(factors, outcome)

    @property
    def count(self) -> int:
        return len(self._records)

    # ========== 1. 分桶统计 ==========

    def bucket_analysis(self, factor_name: str) -> List[BucketStat]:
        """对指定因子进行分桶统计

        将因子值分为 high/medium/low 三档, 计算各档的平均收益和胜率。
        """
        buckets: dict[str, list[OutcomeResult]] = {"high": [], "medium": [], "low": []}

        for factors, outcome in self._records:
            val = self._get_factor_value(factors, factor_name)
            if val is None:
                continue
            if val > self.HIGH_THRESHOLD:
                buckets["high"].append(outcome)
            elif val < self.LOW_THRESHOLD:
                buckets["low"].append(outcome)
            else:
                buckets["medium"].append(outcome)

        result: list[BucketStat] = []
        for bucket_name, outcomes in buckets.items():
            if not outcomes:
                continue
            returns = [o.fixed_return for o in outcomes]
            wins = sum(1 for r in returns if r > 0)
            result.append(BucketStat(
                factor_name=factor_name,
                bucket=bucket_name,
                count=len(outcomes),
                avg_return=sum(returns) / len(returns),
                win_rate=wins / len(outcomes),
                avg_mfe=sum(o.fixed_mfe for o in outcomes) / len(outcomes),
                avg_mae=sum(o.fixed_mae for o in outcomes) / len(outcomes),
            ))
        return result

    # ========== 2. 因子 Alpha 归因 ==========

    def factor_alpha(self, factor_name: str) -> Optional[FactorAlpha]:
        """计算单因子的 Alpha 贡献

        Alpha = 高组平均收益 - 低组平均收益
        正 Alpha: 因子高值时表现更好
        """
        high_returns: list[float] = []
        low_returns: list[float] = []

        for factors, outcome in self._records:
            val = self._get_factor_value(factors, factor_name)
            if val is None:
                continue
            if val > self.HIGH_THRESHOLD:
                high_returns.append(outcome.fixed_return)
            elif val < self.LOW_THRESHOLD:
                low_returns.append(outcome.fixed_return)

        if not high_returns or not low_returns:
            return None

        high_avg = sum(high_returns) / len(high_returns)
        low_avg = sum(low_returns) / len(low_returns)
        alpha = high_avg - low_avg

        # 显著性判断
        abs_alpha = abs(alpha)
        if abs_alpha >= 0.03:
            significance = "strong"
        elif abs_alpha >= 0.01:
            significance = "moderate"
        else:
            significance = "weak"

        return FactorAlpha(
            factor_name=factor_name,
            alpha=round(alpha, 6),
            high_avg=round(high_avg, 6),
            low_avg=round(low_avg, 6),
            high_count=len(high_returns),
            low_count=len(low_returns),
            significance=significance,
        )

    # ========== 3. 偏相关分析 ==========

    def partial_correlation(
        self,
        factor_a: str,
        factor_b: str,
    ) -> Optional[Dict[str, float]]:
        """偏相关分析: 控制 factor_b 后, factor_a 与收益的相关性

        使用分层控制法:
          1. 在 factor_b 高组中, 计算 factor_a 与收益的相关性
          2. 在 factor_b 低组中, 计算 factor_a 与收益的相关性
          3. 取平均, 剔除 factor_b 的混淆效应
        """
        # 收集数据
        a_vals_high_b: list[tuple[float, float]] = []  # (factor_a_value, return) in b_high
        a_vals_low_b: list[tuple[float, float]] = []   # (factor_a_value, return) in b_low

        for factors, outcome in self._records:
            val_a = self._get_factor_value(factors, factor_a)
            val_b = self._get_factor_value(factors, factor_b)
            if val_a is None or val_b is None:
                continue
            if val_b > self.HIGH_THRESHOLD:
                a_vals_high_b.append((val_a, outcome.fixed_return))
            elif val_b < self.LOW_THRESHOLD:
                a_vals_low_b.append((val_a, outcome.fixed_return))

        corr_high = self._pearson(a_vals_high_b) if len(a_vals_high_b) >= 3 else None
        corr_low = self._pearson(a_vals_low_b) if len(a_vals_low_b) >= 3 else None

        if corr_high is None and corr_low is None:
            return None

        corrs = [c for c in [corr_high, corr_low] if c is not None]
        partial_corr = sum(corrs) / len(corrs)

        return {
            "factor_a": factor_a,
            "controlled_by": factor_b,
            "partial_correlation": round(partial_corr, 4),
            "corr_in_b_high": round(corr_high, 4) if corr_high is not None else None,
            "corr_in_b_low": round(corr_low, 4) if corr_low is not None else None,
            "sample_high": len(a_vals_high_b),
            "sample_low": len(a_vals_low_b),
        }

    # ========== 4. 交互效应检测 (2x2 factorial) ==========

    def interaction_effect(
        self,
        factor_a: str,
        factor_b: str,
    ) -> Optional[InteractionEffect]:
        """检测两个因子间的交互效应

        2x2 factorial 设计:
                    B高      B低
            A高   cell_11  cell_10
            A低   cell_01  cell_00

        交互效应 = cell_11 - cell_10 - cell_01 + cell_00

        interaction > 0: 协同效应 (1+1 > 2), 两因子同时高时爆发力强
        interaction < 0: 拮抗效应, 两因子同时高反而不如单独高
        """
        cells: dict[tuple[bool, bool], list[float]] = {}

        for factors, outcome in self._records:
            val_a = self._get_factor_value(factors, factor_a)
            val_b = self._get_factor_value(factors, factor_b)
            if val_a is None or val_b is None:
                continue
            a_high = val_a > 0.5
            b_high = val_b > 0.5
            key = (a_high, b_high)
            cells.setdefault(key, []).append(outcome.fixed_return)

        # 需要至少 3 个象限有数据
        if len(cells) < 3:
            return None

        def avg(cell: list[float]) -> float:
            return sum(cell) / len(cell) if cell else 0.0

        both_high = avg(cells.get((True, True), []))
        a_high_b_low = avg(cells.get((True, False), []))
        a_low_b_high = avg(cells.get((False, True), []))
        both_low = avg(cells.get((False, False), []))

        interaction = both_high - a_high_b_low - a_low_b_high + both_low

        # 解释
        if interaction > 0.02:
            interpretation = (
                f"协同效应 (interaction={interaction:.4f}): "
                f"{factor_a}与{factor_b}同时高位时, 收益爆发 (1+1>2)"
            )
        elif interaction < -0.02:
            interpretation = (
                f"拮抗效应 (interaction={interaction:.4f}): "
                f"{factor_a}与{factor_b}同时高位反而不如单独有效"
            )
        else:
            interpretation = (
                f"无明显交互 (interaction={interaction:.4f}): "
                f"两因子独立起作用"
            )

        return InteractionEffect(
            factor_a=factor_a,
            factor_b=factor_b,
            interaction=round(interaction, 6),
            both_high_avg=round(both_high, 6),
            a_high_b_low_avg=round(a_high_b_low, 6),
            a_low_b_high_avg=round(a_low_b_high, 6),
            both_low_avg=round(both_low, 6),
            interpretation=interpretation,
        )

    # ========== 5. 自诊断 ==========

    def diagnose(self, symbol: str = "") -> DiagnosticReport:
        """系统自诊断: 输出因子排名、交互效应和权重调整建议

        回答核心问题:
          "下个阶段, 应该给 Space Score 加权重, 还是给 MA55 回踩权重?"

        铁律 #3 (统计显著性): 样本量 < 10 时禁止触发权重建议,
        防止在小样本上过度优化 (over-fitting)。此时返回样本不足警示,
        top_factors / top_interactions 置空, 不产出任何权重调仓建议。
        """
        n = self.count
        if n < 10:
            return DiagnosticReport(
                top_factors=[],
                top_interactions=[],
                recommendations=[
                    f"样本不足 (n={n} < 10), 置信度低, "
                    f"暂不触发权重建议 (统计显著性铁律)"
                ],
                sample_size=n,
                avg_return=0.0,
                win_rate=0.0,
                symbol=symbol,
            )

        # 收集所有因子名称
        all_factors: set[str] = set()
        for factors, _ in self._records:
            for f in factors:
                all_factors.add(f.factor)

        # 因子 Alpha 排名
        alphas: list[tuple[str, float]] = []
        for fname in all_factors:
            fa = self.factor_alpha(fname)
            if fa:
                alphas.append((fname, fa.alpha))
        alphas.sort(key=lambda x: abs(x[1]), reverse=True)

        # 交互效应检测 (检查所有因子对)
        interactions: list[InteractionEffect] = []
        factor_list = sorted(all_factors)
        for i in range(len(factor_list)):
            for j in range(i + 1, len(factor_list)):
                ie = self.interaction_effect(factor_list[i], factor_list[j])
                if ie and abs(ie.interaction) > 0.01:
                    interactions.append(ie)
        interactions.sort(key=lambda x: abs(x.interaction), reverse=True)

        # 生成建议
        recommendations: list[str] = []
        for fname, alpha in alphas[:3]:
            if alpha > 0.01:
                recommendations.append(f"增加 {fname} 权重 (Alpha={alpha:+.4f}, 正向贡献)")
            elif alpha < -0.01:
                recommendations.append(f"降低 {fname} 权重 (Alpha={alpha:+.4f}, 负向贡献)")

        for ie in interactions[:3]:
            if ie.interaction > 0.02:
                recommendations.append(
                    f"关注 {ie.factor_a}×{ie.factor_b} 协同效应 "
                    f"(interaction={ie.interaction:+.4f}), 组合信号值得加权"
                )

        if not recommendations:
            recommendations.append("样本不足或因子差异不显著, 暂不建议调整权重")

        # 整体统计
        all_returns = [o.fixed_return for _, o in self._records]
        avg_return = sum(all_returns) / len(all_returns) if all_returns else 0.0
        win_rate = sum(1 for r in all_returns if r > 0) / len(all_returns) if all_returns else 0.0

        return DiagnosticReport(
            top_factors=alphas,
            top_interactions=interactions[:5],
            recommendations=recommendations,
            sample_size=self.count,
            avg_return=round(avg_return, 6),
            win_rate=round(win_rate, 4),
            symbol=symbol,
        )

    # ========== 内部辅助 ==========

    def _get_factor_value(
        self, factors: List[EvidenceFactor], name: str
    ) -> Optional[float]:
        """从因子列表中取指定因子的 value"""
        for f in factors:
            if f.factor == name:
                return f.value
        return None

    @staticmethod
    def _pearson(pairs: List[Tuple[float, float]]) -> Optional[float]:
        """计算皮尔逊相关系数"""
        n = len(pairs)
        if n < 3:
            return None
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
        var_x = sum((x - mean_x) ** 2 for x in xs)
        var_y = sum((y - mean_y) ** 2 for y in ys)
        if var_x == 0 or var_y == 0:
            return 0.0
        return cov / (var_x * var_y) ** 0.5
