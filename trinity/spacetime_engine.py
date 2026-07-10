"""Trinity OS v2.1 - 时空引擎

计算时间对称度 (Time Score) 与空间完整度 (Space Score),
用于验证"时空调整是否充分"——抄底动作的必经之路。

空间规则 (来源: 《三位一体时空要素高阶》):
  上一笔周线级别下跌中, 必须包含完整的日线 D 型结构 (结构必完美)。
  不是简单判断三段式下跌, 而是判断第一段是否归属于更大结构。

时间规则:
  周线级别两段下跌时间高度对称 (偏差不超过 3 周)。
  不能用日线级别判断 (节假日毛刺)。
  参考变盘窗口: 13/18/21/34/55 周。
"""
from __future__ import annotations

from typing import Optional

from trinity.context import OHLCV, SpacetimeScore
from trinity.structure_parser import Pivot, StructureParser


class SpacetimeEngine:
    """时空充分性引擎

    用法:
        engine = SpacetimeEngine(time_tolerance=3)
        score = engine.evaluate(weekly_ohlcv, daily_ohlcv)
    """

    def __init__(
        self,
        time_tolerance: int = 3,
        time_windows: tuple[int, ...] = (13, 18, 21, 34, 55),
        zigzag_threshold: float = 0.03,
    ):
        """
        Args:
            time_tolerance: 时间对称容差 (周), 两段下跌时间偏差不超过此值
            time_windows:   变盘窗口参考 (斐波那契/江恩)
            zigzag_threshold: ZigZag 转折阈值
        """
        self.time_tolerance = time_tolerance
        self.time_windows = time_windows
        self.parser = StructureParser(threshold=zigzag_threshold)

    # ========== 时间对称度 ==========

    def calc_time_score(
        self,
        weekly_ohlcv: list[OHLCV],
    ) -> tuple[float, list[str]]:
        """计算时间对称度

        逻辑:
          1. 在周线上识别下跌段的转折点
          2. 比较最近两段下跌的持续时间
          3. 偏差越小, score 越高

        Returns:
            (score [0,1], evidence)
        """
        evidence: list[str] = []
        if len(weekly_ohlcv) < 5:
            return 0.0, ["周线数据不足, 无法计算时间对称度"]

        highs = [bar.high for bar in weekly_ohlcv]
        lows = [bar.low for bar in weekly_ohlcv]
        pivots = self.parser.find_pivots(highs, lows)

        if len(pivots) < 4:
            return 0.2, [f"周线转折点 {len(pivots)} < 4, 时间对称度不充分"]

        # 提取下跌段持续时间 (高点到低点的 bar 数)
        decline_segments = self._extract_decline_durations(pivots)
        evidence.append(f"周线下跌段: {decline_segments}")

        if len(decline_segments) < 2:
            return 0.3, ["仅有一段下跌, 无法比较时间对称性"]

        # 比较最近两段下跌
        last_two = decline_segments[-2:]
        t1, t2 = last_two
        evidence.append(f"最近两段下跌时长: {t1}周, {t2}周")

        if t1 == 0 and t2 == 0:
            return 0.0, ["两段下跌时长均为0"]

        diff = abs(t1 - t2)
        evidence.append(f"时间偏差: {diff}周 (容差 {self.time_tolerance}周)")

        if diff == 0:
            score = 1.0
            evidence.append("时间完全对称")
        elif diff <= self.time_tolerance:
            score = 1.0 - (diff / (self.time_tolerance + 1)) * 0.3
            evidence.append(f"时间偏差在容差内, score={score:.2f}")
        else:
            # 超出容差, 大幅降分
            score = max(0.0, 0.5 - (diff - self.time_tolerance) * 0.1)
            evidence.append(f"时间偏差超出容差, score={score:.2f}")

        # 检查是否命中变盘窗口
        for w in self.time_windows:
            if t2 == w or t1 == w:
                score = min(1.0, score + 0.1)
                evidence.append(f"命中变盘窗口 {w}周, score 加成")
                break

        return score, evidence

    # ========== 空间完整度 ==========

    def calc_space_score(
        self,
        weekly_ohlcv: list[OHLCV],
        daily_ohlcv: Optional[list[OHLCV]] = None,
    ) -> tuple[float, list[str]]:
        """计算空间完整度

        逻辑:
          1. 在周线上识别最近一笔下跌
          2. 检查该笔下跌中是否包含完整的日线 D 型结构 (结构必完美)
          3. 如无日线数据, 用周线自身的结构完整度降级评估

        Returns:
            (score [0,1], evidence)
        """
        evidence: list[str] = []
        if len(weekly_ohlcv) < 5:
            return 0.0, ["周线数据不足, 无法计算空间完整度"]

        highs = [bar.high for bar in weekly_ohlcv]
        lows = [bar.low for bar in weekly_ohlcv]
        pivots = self.parser.find_pivots(highs, lows)

        if len(pivots) < 4:
            return 0.2, [f"周线转折点 {len(pivots)} < 4, 空间结构不充分"]

        # 评估周线自身的 D 结构
        is_complete, d_score, d_evidence = self.parser.evaluate_d_structure(pivots)
        evidence.extend(d_evidence)

        if daily_ohlcv and len(daily_ohlcv) >= 10:
            # 有日线数据: 检查周线下跌段中是否包含日线 D 结构
            daily_evidence = self._check_daily_d_structure(
                weekly_ohlcv, daily_ohlcv, pivots
            )
            evidence.extend(daily_evidence)

            if any("日线D结构完整" in e for e in daily_evidence):
                score = min(1.0, d_score + 0.1)
                evidence.append("周线下跌包含完整日线D结构, 空间充分 (结构必完美)")
            else:
                score = d_score * 0.7
                evidence.append("未检测到完整日线D结构, 空间完整度降级")
        else:
            # 无日线数据: 仅用周线结构评估
            score = d_score
            evidence.append("无日线数据, 仅用周线结构评估空间完整度")

        return score, evidence

    # ========== 综合评估 ==========

    def evaluate(
        self,
        weekly_ohlcv: list[OHLCV],
        daily_ohlcv: Optional[list[OHLCV]] = None,
    ) -> SpacetimeScore:
        """综合评估时空充分性"""
        time_score, time_evidence = self.calc_time_score(weekly_ohlcv)
        space_score, space_evidence = self.calc_space_score(weekly_ohlcv, daily_ohlcv)
        return SpacetimeScore(
            time_score=time_score,
            space_score=space_score,
            time_evidence=time_evidence,
            space_evidence=space_evidence,
        )

    # ========== 内部辅助 ==========

    def _extract_decline_durations(self, pivots: list[Pivot]) -> list[int]:
        """从转折点中提取下跌段持续时间 (bar 数)

        下跌段: 高点 → 低点
        """
        durations: list[int] = []
        for i in range(1, len(pivots)):
            if pivots[i - 1].is_high and not pivots[i].is_high:
                duration = pivots[i].index - pivots[i - 1].index
                durations.append(duration)
        return durations

    def _check_daily_d_structure(
        self,
        weekly_ohlcv: list[OHLCV],
        daily_ohlcv: list[OHLCV],
        weekly_pivots: list[Pivot],
    ) -> list[str]:
        """检查周线下跌段中是否包含日线 D 结构

        逻辑:
          1. 找到周线最近一笔下跌的时间范围
          2. 提取该范围内的日线数据
          3. 在日线上识别 D 结构
        """
        evidence: list[str] = []

        # ���最近的周线下跌段 (高点→低点)
        decline_start_idx = None
        decline_end_idx = None
        for i in range(len(weekly_pivots) - 1, 0, -1):
            if weekly_pivots[i - 1].is_high and not weekly_pivots[i].is_high:
                decline_start_idx = weekly_pivots[i - 1].index
                decline_end_idx = weekly_pivots[i].index
                break

        if decline_start_idx is None:
            evidence.append("未找到周线下跌段")
            return evidence

        # 映射周线索引到日线时间范围
        # 假设每根周线对应 5 根日线 (简化映射)
        daily_start = decline_start_idx * 5
        daily_end = min(decline_end_idx * 5 + 5, len(daily_ohlcv))

        if daily_end - daily_start < 10:
            evidence.append("日线数据范围不足, 无法评估D结构")
            return evidence

        daily_segment = daily_ohlcv[daily_start:daily_end]
        daily_highs = [bar.high for bar in daily_segment]
        daily_lows = [bar.low for bar in daily_segment]
        daily_pivots = self.parser.find_pivots(daily_highs, daily_lows)

        if len(daily_pivots) >= 4:
            is_complete, d_score, d_evidence = self.parser.evaluate_d_structure(daily_pivots)
            if is_complete:
                evidence.append(f"日线D结构完整 (score={d_score:.2f})")
                evidence.extend(d_evidence)
            else:
                evidence.append(f"日线D结构不完整 (score={d_score:.2f})")
        else:
            evidence.append(f"日线转折点 {len(daily_pivots)} < 4, D结构不充分")

        return evidence
