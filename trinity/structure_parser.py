"""Trinity OS v2.1 - 结构解析器

实现 ABCD 四型结构识别, 基于缠论「笔」的概念与 ZigZag 转折点。

结构类型 (来源: 《三位一体交易系统集合》2.4):
  A: 五段式    - 第三浪为主升/跌浪, 5 段结构
  B: 双平台式  - 九段, 十个拐点, 双平台不重叠
  C: 单平台式  - 含一个整理平台
  D: 三段式    - 最简结构, 3 段

笔的概念: 顶底分型相连, 至少 5 根 K 线。
  - 顶分型: 中间 K 的高点高于两边
  - 底分型: 中间 K 的低点低于两边
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trinity.context import OHLCV, StructureEvidence, StructureType, TrendDirection


@dataclass
class Pivot:
    """转折点"""
    index: int
    price: float
    is_high: bool       # True=高点(顶分型), False=低点(底分型)


class StructureParser:
    """结构解析器

    用法:
        parser = StructureParser(threshold=0.03)
        pivots = parser.find_pivots(highs, lows)
        structure = parser.classify(pivots)
    """

    def __init__(self, threshold: float = 0.03):
        """
        Args:
            threshold: ZigZag 转折阈值 (价格变化比例), 默认 3%
        """
        self.threshold = threshold

    # ========== ZigZag 转折点识别 ==========

    def find_pivots(
        self,
        highs: list[float],
        lows: list[float],
    ) -> list[Pivot]:
        """ZigZag 高低点识别

        算法:
          1. 从第一个 bar 开始, 确定初始方向
          2. 跟踪当前极值 (最高/最低)
          3. 当价格反向运动超过 threshold 时, 标记当前极值为 Pivot
          4. 继续追踪新方向
        """
        n = len(highs)
        if n < 2:
            return []

        pivots: list[Pivot] = []
        # 初始化: 找第一个方向
        last_pivot_idx = 0
        last_pivot_price = highs[0]
        looking_for_high = True  # 先找高点

        # 确定初始方向: 比较前几根
        for i in range(1, n):
            if highs[i] > last_pivot_price:
                last_pivot_idx = i
                last_pivot_price = highs[i]
                looking_for_high = True
            elif lows[i] < last_pivot_price * (1 - self.threshold):
                # 价格下跌超过阈值, 第一个高点确认
                pivots.append(Pivot(last_pivot_idx, last_pivot_price, is_high=True))
                last_pivot_idx = i
                last_pivot_price = lows[i]
                looking_for_high = False
                break
        else:
            # 整个序列单调, 只有一个方向
            pivots.append(Pivot(last_pivot_idx, last_pivot_price, is_high=looking_for_high))
            return pivots

        # 继续追踪
        for i in range(last_pivot_idx + 1, n):
            if looking_for_high:
                # 找高点
                if highs[i] > last_pivot_price:
                    last_pivot_price = highs[i]
                    last_pivot_idx = i
                elif lows[i] < last_pivot_price * (1 - self.threshold):
                    # 反转: 确认高点, 开始找低点
                    pivots.append(Pivot(last_pivot_idx, last_pivot_price, is_high=True))
                    last_pivot_idx = i
                    last_pivot_price = lows[i]
                    looking_for_high = False
            else:
                # 找低点
                if lows[i] < last_pivot_price:
                    last_pivot_price = lows[i]
                    last_pivot_idx = i
                elif highs[i] > last_pivot_price * (1 + self.threshold):
                    # 反转: 确认低点, 开始找高点
                    pivots.append(Pivot(last_pivot_idx, last_pivot_price, is_high=False))
                    last_pivot_idx = i
                    last_pivot_price = highs[i]
                    looking_for_high = True

        # 添加最后一个未确认的极值
        if pivots:
            last = pivots[-1]
            if last.is_high and not looking_for_high:
                pivots.append(Pivot(last_pivot_idx, last_pivot_price, is_high=False))
            elif not last.is_high and looking_for_high:
                pivots.append(Pivot(last_pivot_idx, last_pivot_price, is_high=True))

        return pivots

    # ========== 分型识别 (笔的基础) ==========

    def find_fractals(
        self,
        highs: list[float],
        lows: list[float],
        window: int = 1,
    ) -> tuple[list[int], list[int]]:
        """识别顶底分型

        Args:
            window: 左右各 window 根 K 线进行比较, 默认 1

        Returns:
            (top_indices, bottom_indices)
        """
        n = len(highs)
        tops: list[int] = []
        bottoms: list[int] = []
        for i in range(window, n - window):
            is_top = all(highs[i] > highs[i - j] and highs[i] > highs[i + j] for j in range(1, window + 1))
            is_bottom = all(lows[i] < lows[i - j] and lows[i] < lows[i + j] for j in range(1, window + 1))
            if is_top:
                tops.append(i)
            if is_bottom:
                bottoms.append(i)
        return tops, bottoms

    # ========== ABCD 结构分类 ==========

    def classify(
        self,
        pivots: list[Pivot],
        closes: Optional[list[float]] = None,
    ) -> StructureEvidence:
        """根据转折点序列分类 ABCD 结构

        分类规则:
          - 段数 = len(pivots) - 1
          - 5 段 + 第3段最长 → A (五段式)
          - 9 段 + 双平台不重叠 → B (双平台式)
          - 3 段 → D (三段式)
          - 其余含平台 → C (单平台式)
          - 段数 < 3 → UNKNOWN
        """
        evidence = StructureEvidence()
        segments = max(0, len(pivots) - 1)
        evidence.pivots = [p.price for p in pivots]
        evidence.segments = segments

        if segments < 3:
            evidence.structure_type = StructureType.UNKNOWN
            evidence.evidence.append(f"段数 {segments} < 3, 结构不充分")
            return evidence

        # 判断方向: 第一个到最后一个 pivot 的趋势
        if pivots[-1].price > pivots[0].price:
            evidence.direction = TrendDirection.UP
        else:
            evidence.direction = TrendDirection.DOWN

        # 计算各段长度
        segment_lengths = []
        for i in range(1, len(pivots)):
            length = abs(pivots[i].price - pivots[i - 1].price)
            segment_lengths.append(length)
        evidence.evidence.append(f"段数={segments}, 方向={evidence.direction.value}")

        # 分类
        if segments >= 9 and self._has_dual_platform(pivots):
            evidence.structure_type = StructureType.B
            evidence.evidence.append("双平台式: 9段+双平台不重叠")
            evidence.nodes = self._label_b_nodes(pivots)
        elif segments >= 5 and self._is_third_wave_main(segment_lengths):
            evidence.structure_type = StructureType.A
            evidence.evidence.append("五段式: 第3段为主升/跌浪")
            evidence.nodes = self._label_a_nodes(pivots)
        elif segments >= 5:
            # 5段但第3段不是最长, 可能是 C 或 B 的变体
            if self._has_platform(pivots):
                evidence.structure_type = StructureType.C
                evidence.evidence.append("单平台式: 含整理平台")
            else:
                evidence.structure_type = StructureType.A
                evidence.evidence.append("五段式: 5段结构")
                evidence.nodes = self._label_a_nodes(pivots)
        elif segments == 3 or segments == 4:
            evidence.structure_type = StructureType.D
            evidence.evidence.append("三段式: 基础结构")
            evidence.nodes = self._label_d_nodes(pivots)
        else:
            evidence.structure_type = StructureType.C
            evidence.evidence.append("单平台式: 多段含平台")

        # 形态层级 (tier) 元数据 — 量化结构完整度 (蓝图 v2.1 §形态元数据)
        # 不改变返回结构, 仅作为可审计的附加证据字段。
        if evidence.structure_type in (StructureType.A, StructureType.B):
            evidence.tier = "T1"   # 高阶形态 (五段式 / 双平台式)
        elif evidence.structure_type == StructureType.C:
            evidence.tier = "T2"   # 标准形态 (含平台整理)
        elif evidence.structure_type == StructureType.D:
            evidence.tier = "T2" if segments >= 4 else "T3"  # D4 优于 D3
        else:
            evidence.tier = ""     # 无形态 (UNKNOWN)

        return evidence

    # ========== D 结构专项评估 ==========

    def evaluate_d_structure(self, pivots: list[Pivot]) -> tuple[bool, float, list[str]]:
        """评估是否存在完整的 D 型结构

        用于时空充分性验证: 周线下跌须含日线 D 结构 (结构必完美)。

        Returns:
            (is_complete, score, evidence)
            - is_complete: 是否包含完整 D 结构
            - score: 完整度 [0, 1]
            - evidence: 证据
        """
        evidence: list[str] = []
        segments = max(0, len(pivots) - 1)

        if segments < 3:
            return False, 0.0, [f"段数 {segments} < 3, 不构成 D 结构"]

        # D 结构: H-L-H-L 序列 (高低交替), 至少 3 段
        # 检查 pivot 是否高低交替
        alternating = True
        for i in range(1, len(pivots)):
            if pivots[i].is_high == pivots[i - 1].is_high:
                alternating = False
                break

        if not alternating:
            # 仍有价值, 降分
            score = 0.3
            evidence.append("转折点非严格交替, 结构不完美")
            return score >= 0.5, score, evidence

        # 检查 D3 是否破位 D1 (结构必完美)
        # D 结构节点: D1(第一个高点), D2(第一个低点), D3(第二个高点)
        # 如果是下跌 D 结构: D1(高) → D2(低) → D3(高, 低于D1) → D4(低, 破D2)
        d_nodes = self._label_d_nodes(pivots)
        evidence.append(f"D结构节点: {d_nodes}")
        evidence.append(f"段数: {segments}")

        # 完整度评分
        if segments >= 3:
            score = 0.7
            evidence.append("满足最小3段要求")
        if segments >= 4:
            score = 0.85
            evidence.append("4段结构, 结构较完整")
        if segments >= 5:
            score = 0.95
            evidence.append("5段+结构, 结构充分完整")

        # 检查是否破位 (D3/D4 是否突破前高/前低)
        if len(pivots) >= 4:
            if pivots[0].is_high:
                d1_high = pivots[0].price
                d3_high = pivots[2].price if len(pivots) > 2 else 0
                d2_low = pivots[1].price
                d4_low = pivots[3].price if len(pivots) > 3 else float("inf")
                if d3_high < d1_high and d4_low < d2_low:
                    score = min(1.0, score + 0.05)
                    evidence.append("D3<D1且D4<D2, 结构破位确认 (结构必完美)")
            else:
                d1_low = pivots[0].price
                d3_low = pivots[2].price if len(pivots) > 2 else float("inf")
                d2_high = pivots[1].price
                d4_high = pivots[3].price if len(pivots) > 3 else 0
                if d3_low > d1_low and d4_high > d2_high:
                    score = min(1.0, score + 0.05)
                    evidence.append("D3>D1且D4>D2, 结构破位确认 (结构必完美)")

        return score >= 0.7, score, evidence

    # ========== 内部辅助 ==========

    def _is_third_wave_main(self, segment_lengths: list[float]) -> bool:
        """判断第3段是否为最长 (波浪理论: 第3浪为主升浪)"""
        if len(segment_lengths) < 3:
            return False
        third = segment_lengths[2]
        # 主涨段未必最长, 但一定不是最短
        return third > min(segment_lengths)

    def _has_dual_platform(self, pivots: list[Pivot]) -> bool:
        """检查是否存在双平台 (两个不重叠的整理区间)"""
        if len(pivots) < 8:
            return False
        # 简化判断: 检查是否有两段价格区间不重叠
        mid = len(pivots) // 2
        first_platform = self._price_range(pivots[:mid])
        second_platform = self._price_range(pivots[mid:])
        if first_platform and second_platform:
            # 不重叠: 一个区间的最低点高于另一个的最高点
            return (first_platform[0] > second_platform[1] or
                    second_platform[0] > first_platform[1])
        return False

    def _has_platform(self, pivots: list[Pivot]) -> bool:
        """检查是否包含整理平台"""
        if len(pivots) < 4:
            return False
        # 简化: 存在相邻段价格幅度相近的区域
        for i in range(1, len(pivots) - 2):
            seg1 = abs(pivots[i].price - pivots[i - 1].price)
            seg2 = abs(pivots[i + 2].price - pivots[i + 1].price)
            if seg1 > 0 and seg2 > 0:
                ratio = min(seg1, seg2) / max(seg1, seg2)
                if ratio > 0.6:
                    return True
        return False

    def _price_range(self, pivots: list[Pivot]) -> Optional[tuple[float, float]]:
        """计算 pivot 组的价格区间 (min, max)"""
        if not pivots:
            return None
        prices = [p.price for p in pivots]
        return (min(prices), max(prices))

    def _label_d_nodes(self, pivots: list[Pivot]) -> list[str]:
        """标记 D 结构节点 D1/D2/D3/D4"""
        nodes = []
        for i, p in enumerate(pivots[:4]):
            nodes.append(f"D{i + 1}={'高' if p.is_high else '低'}@{p.price:.2f}")
        return nodes

    def _label_a_nodes(self, pivots: list[Pivot]) -> list[str]:
        """标记 A 结构波段节点"""
        nodes = []
        wave_labels = ["1", "2", "3", "4", "5"]
        for i, p in enumerate(pivots[:5]):
            label = wave_labels[i] if i < len(wave_labels) else str(i + 1)
            nodes.append(f"W{label}={'高' if p.is_high else '低'}@{p.price:.2f}")
        return nodes

    def _label_b_nodes(self, pivots: list[Pivot]) -> list[str]:
        """标记 B 结构节点"""
        nodes = []
        for i, p in enumerate(pivots[:10]):
            nodes.append(f"P{i}={'高' if p.is_high else '低'}@{p.price:.2f}")
        return nodes

    # ========== 从 OHLCV 一键解析 ==========

    def parse(self, ohlcv: list[OHLCV]) -> StructureEvidence:
        """从 OHLCV 列表一键解析结构"""
        highs = [bar.high for bar in ohlcv]
        lows = [bar.low for bar in ohlcv]
        pivots = self.find_pivots(highs, lows)
        return self.classify(pivots, [bar.close for bar in ohlcv])
