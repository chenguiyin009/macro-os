"""Trinity OS v2.1 - 决策路由器

实现「状态 × 结构」决策矩阵, 以及级别嵌套 J+2→J→J-1→J-2 的压制确认逻辑。

决策矩阵来源: 《三位一体时空要素和结构要素》对应表
  极强 + 上涨(A/B) → 强力加仓, 忽略顶部背离
  强   + 上涨(B)   → 回调加仓, 忽略小级别波动
  强   + 下跌(D)   → 谨慎减仓, d1卖d2接
  中偏强/弱 + (C)  → 试仓带止损
  弱   + 上涨(D)   → 谨慎买入, d1买d2卖
  弱   + 下跌(B)   → 反弹卖出
  极弱 + 下跌(A/B) → 强力减仓, 忽略底部背离

级别嵌套原则 (来源: 《7 Key Points》):
  J+2 做时空判断, J 判断结构, J-1 判断回抽布林带, J-2 辅助共振
  大级别压制小级别
"""
from __future__ import annotations

from trinity.context import (
    ActionType,
    Decision,
    JLevelContext,
    MacroState,
    SpacetimeScore,
    StructureType,
    TradingLevel,
    TrendDirection,
)


class DecisionRouter:
    """决策路由器

    根据 J+2/J/J-1/J-2 四级上下文, 输出决策。
    """

    # 决策矩阵: (state, direction) → (action, structures, note)
    MATRIX: dict[tuple[MacroState, TrendDirection], tuple[ActionType, list[str], str]] = {
        (MacroState.EXTREME_STRONG, TrendDirection.UP): (
            ActionType.STRONG_ADD, ["A", "B"],
            "忽略顶部背离, 逢回抽55线买入机会",
        ),
        (MacroState.EXTREME_STRONG, TrendDirection.DOWN): (
            ActionType.HOLD, [],
            "无对应下跌结构, 小心被洗走",
        ),
        (MacroState.STRONG, TrendDirection.UP): (
            ActionType.ADD_ON_PULLBACK, ["B"],
            "忽略小级别波动, b1/b3/b5/b7波段买入机会",
        ),
        (MacroState.STRONG, TrendDirection.DOWN): (
            ActionType.REDUCE_CAUTIOUSLY, ["D"],
            "卖出需谨慎, d1卖d2接, d3不稳定, d4买",
        ),
        (MacroState.MODERATE_STRONG, TrendDirection.UP): (
            ActionType.SCOUT_WITH_STOP, ["C"],
            "中枢底部可以试仓, 但要注意严格止损",
        ),
        (MacroState.MODERATE_STRONG, TrendDirection.DOWN): (
            ActionType.SCOUT_WITH_STOP, ["C"],
            "中枢底部可以试仓, 但要注意严格止损",
        ),
        (MacroState.MODERATE_WEAK, TrendDirection.UP): (
            ActionType.SCOUT_WITH_STOP, ["C"],
            "中枢底部可以试仓, 但要注意严格止损",
        ),
        (MacroState.MODERATE_WEAK, TrendDirection.DOWN): (
            ActionType.SCOUT_WITH_STOP, ["C"],
            "中枢底部可以试仓, 但要注意严格止损",
        ),
        (MacroState.WEAK, TrendDirection.UP): (
            ActionType.BUY_CAUTIOUSLY, ["D"],
            "买入需谨慎, d1买d2卖, d3不稳定, d4卖",
        ),
        (MacroState.WEAK, TrendDirection.DOWN): (
            ActionType.SELL_ON_BOUNCE, ["B"],
            "忽略小级别波动, b1/b3/b5/b7波段卖出机会",
        ),
        (MacroState.EXTREME_WEAK, TrendDirection.UP): (
            ActionType.HOLD, [],
            "无对应上涨结构, 小心被骗线",
        ),
        (MacroState.EXTREME_WEAK, TrendDirection.DOWN): (
            ActionType.STRONG_REDUCE, ["A", "B"],
            "忽略底部背离, 逢回抽55线卖出机会",
        ),
    }

    # 状态置信度基准
    STATE_CONFIDENCE: dict[MacroState, float] = {
        MacroState.EXTREME_STRONG: 0.9,
        MacroState.STRONG: 0.75,
        MacroState.MODERATE_STRONG: 0.55,
        MacroState.MODERATE_WEAK: 0.45,
        MacroState.WEAK: 0.3,
        MacroState.EXTREME_WEAK: 0.2,
    }

    def route(
        self,
        ctx_j2: JLevelContext,
        ctx_j: JLevelContext,
        ctx_j1: JLevelContext,
        ctx_j2_minus: JLevelContext,
        spacetime: SpacetimeScore | None = None,
    ) -> Decision:
        """执行决策路由

        级别嵌套逻辑:
          1. J+2 状态决定大方向 (大级别压制小级别)
          2. J 结构确认方向
          3. J-1 回抽布林带确认入场时机
          4. J-2 MA55 共振增加胜率

        Args:
            ctx_j2:      J+2 (周/月) 上下文
            ctx_j:       J (日) 上下文
            ctx_j1:      J-1 (60M) 上下文
            ctx_j2_minus: J-2 (15M) 上下文
            spacetime:   时空评分 (可选, 抄底时必查)
        """
        evidence: list[str] = []
        spacetime_provided = spacetime is not None
        spacetime = spacetime or SpacetimeScore()

        # === 1. J+2 时空判定 (大级别压制) ===
        j2_state = ctx_j2.state
        evidence.append(f"[J+2] 状态={j2_state.value} (大级别时空判定)")
        evidence.extend(f"  {e}" for e in ctx_j2.state_evidence)

        # 大级别方向: 极强/强/中偏强 → 多头; 中偏弱/弱/极弱 → 空头
        j2_bullish = j2_state.is_bullish

        # === 2. J 结构判定 ===
        j_structure = ctx_j.structure
        j_direction = j_structure.direction
        evidence.append(f"[J] 结构={j_structure.structure_type.value}, 方向={j_direction.value}")

        # === 3. 决策矩阵查找 ===
        # 以 J+2 状态 + J 结构方向 为主要决策依据
        matrix_key = (j2_state, j_direction)
        action, valid_structures, note = self.MATRIX.get(
            matrix_key, (ActionType.HOLD, [], "状态与方向无匹配, 观望")
        )

        # 结构类型匹配检查
        structure_match = (
            not valid_structures
            or j_structure.structure_type.value in valid_structures
            or j_structure.structure_type == StructureType.UNKNOWN
        )
        if not structure_match:
            evidence.append(
                f"  结构 {j_structure.structure_type.value} 不在匹配列表 {valid_structures}, 降级为观望"
            )
            action = ActionType.HOLD
            note = "结构不匹配, 观望"

        evidence.append(f"  决策矩阵: {j2_state.value}×{j_direction.value} → {action.value}")
        evidence.append(f"  操作备注: {note}")

        # === 4. J-1 回抽布林带确认 (主涨段狙击) ===
        # 蓝图 v2.1 §3.3: STRONG_ADD 要求 Space Score > 0.75
        main_surge = self._check_main_surge(ctx_j2, ctx_j, ctx_j1, ctx_j2_minus)
        if main_surge:
            evidence.append("[主涨段狙击] 条件满足:")
            evidence.append(f"  J+2 极强={j2_state == MacroState.EXTREME_STRONG}")
            evidence.append(f"  J-1 回抽布林带确认={ctx_j1.bollinger_pullback_confirmed}")
            evidence.append(f"  J-2 MA55共振={ctx_j2_minus.above_ma55}")
            # 蓝图 v2.1: STRONG_ADD 要求 Space Score > 0.75
            space_ok = (not spacetime_provided) or spacetime.space_score > 0.75
            if space_ok and j2_bullish and action not in (ActionType.STRONG_ADD,):
                action = ActionType.STRONG_ADD
                note = "主涨段狙击: J+2极强 + J-1布林带确认 + J-2共振"
                evidence.append("  → 升级为 STRONG_ADD (Space Score > 0.75)")
            elif not space_ok and action == ActionType.STRONG_ADD:
                action = ActionType.ADD_ON_PULLBACK
                note = "Space Score <= 0.75, 降级为回调加仓"
                evidence.append("  Space Score <= 0.75, 降级为 ADD_ON_PULLBACK")

        # === 4.5 TAKE_PROFIT_T 检查 (蓝图 v2.1 §3.3) ===
        # 条件: 突破遇阻 + 小级别背离 + 动能衰减
        if self._check_take_profit_t(ctx_j2, ctx_j1, j2_state):
            evidence.append("[做T止盈] 突破遇阻 + J-1背离 + 动能衰减")
            if action in (ActionType.STRONG_ADD, ActionType.ADD_ON_PULLBACK):
                action = ActionType.TAKE_PROFIT_T
                note = "突破遇阻+背离+动能衰减, 转为 TAKE_PROFIT_T"
                evidence.append("  → 转为 TAKE_PROFIT_T")

        # === 5. 时空充分性检查 (抄底必查) ===
        if action in (ActionType.SCOUT_WITH_STOP, ActionType.BUY_CAUTIOUSLY, ActionType.STRONG_ADD):
            if spacetime_provided and (spacetime.time_score > 0 or spacetime.space_score > 0):
                evidence.append(f"[时空] 综合={spacetime.overall:.2f} (时间={spacetime.time_score:.2f}, 空间={spacetime.space_score:.2f})")
                if not spacetime.sufficient:
                    evidence.append("  时空不充分, 降低置信度")
                    if action == ActionType.STRONG_ADD:
                        action = ActionType.ADD_ON_PULLBACK
                        note = "时空不充分, 降级为回调加仓"
                        evidence.append("  → 降级为 ADD_ON_PULLBACK")

        # === 5.5 EXIT/WAIT 检查 (蓝图 v2.1 §3.3) ===
        # 条件: 结构破坏 (顶背离 + 跌破 MA55) 或 时空严重不足 (overall < 0.5)
        if self._check_exit_wait(ctx_j, ctx_j1, spacetime, spacetime_provided):
            evidence.append("[退出/观望] 结构破坏或时空未达标")
            if action in (ActionType.STRONG_ADD, ActionType.ADD_ON_PULLBACK, ActionType.SCOUT_WITH_STOP):
                action = ActionType.HOLD
                note = "结构破坏/时空未达标, 转为观望"
                evidence.append("  → 转为 HOLD")

        # === 6. 置信度计算 ===
        confidence = self._calc_confidence(
            j2_state, structure_match, ctx_j1.bollinger_pullback_confirmed,
            ctx_j2_minus.above_ma55, spacetime, main_surge,
        )
        evidence.append(f"[置信度] {confidence:.2f}")

        # === 7. 风险等级 ===
        risk_level = self._calc_risk(j2_state, spacetime, action)
        evidence.append(f"[风险] {risk_level:.2f}")

        # 触发级别
        level = TradingLevel.J_PLUS_2 if j2_state.is_extreme else TradingLevel.J

        return Decision(
            action=action,
            confidence=confidence,
            spacetime=spacetime,
            risk_level=risk_level,
            level=level,
            evidence=evidence,
            note=note,
            symbol=ctx_j.symbol or ctx_j2.symbol,
        )

    def route_single_level(
        self,
        ctx: JLevelContext,
        spacetime: SpacetimeScore | None = None,
    ) -> Decision:
        """单级别决策 (简化版, 不考虑嵌套)

        用于测试和快速判断。
        """
        evidence: list[str] = []
        spacetime = spacetime or SpacetimeScore()

        state = ctx.state
        direction = ctx.structure.direction
        evidence.append(f"状态={state.value}, 方向={direction.value}")

        matrix_key = (state, direction)
        action, valid_structures, note = self.MATRIX.get(
            matrix_key, (ActionType.HOLD, [], "无匹配, 观望")
        )

        structure_match = (
            not valid_structures
            or ctx.structure.structure_type.value in valid_structures
            or ctx.structure.structure_type == StructureType.UNKNOWN
        )
        if not structure_match:
            action = ActionType.HOLD
            note = "结构不匹配, 观望"
            evidence.append("结构不匹配, 降级观望")

        confidence = self.STATE_CONFIDENCE.get(state, 0.3)
        if structure_match:
            confidence = min(1.0, confidence + 0.1)

        risk_level = self._calc_risk(state, spacetime, action)

        return Decision(
            action=action,
            confidence=confidence,
            spacetime=spacetime,
            risk_level=risk_level,
            level=ctx.level,
            evidence=evidence,
            note=note,
            symbol=ctx.symbol,
        )

    # ========== 主涨段狙击 ==========

    def _check_main_surge(
        self,
        ctx_j2: JLevelContext,
        ctx_j: JLevelContext,
        ctx_j1: JLevelContext,
        ctx_j2_minus: JLevelContext,
    ) -> bool:
        """检查主涨段狙击条件

        条件:
          1. J+2 极强
          2. J 结构主升 (上涨方向)
          3. J-1 回抽布林带中轨确认
          4. J-2 MA55 共振 (可选, 双重确认)

        来源: 《7 Key Points》第1点
        """
        if ctx_j2.state != MacroState.EXTREME_STRONG:
            return False
        if ctx_j.structure.direction != TrendDirection.UP:
            return False
        if not ctx_j1.bollinger_pullback_confirmed:
            return False
        # J-2 共振是双重确认, 非必须
        return True

    def _check_take_profit_t(
        self,
        ctx_j2: JLevelContext,
        ctx_j1: JLevelContext,
        j2_state: MacroState,
    ) -> bool:
        """TAKE_PROFIT_T 条件检查 (蓝图 v2.1 §3.3)

        条件: 突破遇阻 + 小级别背离 + 动能衰减
        - 突破遇阻: J 价格在 MA55 附近 (±2%)
        - 小级别背离: J-1 MACD 柱缩小 (动能背离)
        - 动能衰减: J+2 非极强
        """
        # 突破遇阻: 价格接近 MA55
        near_ma55 = (
            ctx_j2.ma55 > 0
            and abs(ctx_j2.price - ctx_j2.ma55) / ctx_j2.ma55 < 0.02
        )
        # 小级别背离: J-1 MACD 柱为负或正在缩小
        has_divergence = ctx_j1.macd_hist < 0 or (
            ctx_j1.dif > 0 and ctx_j1.dif < ctx_j1.dea
        )
        # 动能衰减: J+2 非极强
        momentum_fading = j2_state != MacroState.EXTREME_STRONG
        return near_ma55 and has_divergence and momentum_fading

    def _check_exit_wait(
        self,
        ctx_j: JLevelContext,
        ctx_j1: JLevelContext,
        spacetime: SpacetimeScore,
        spacetime_provided: bool,
    ) -> bool:
        """EXIT/WAIT 条件检查 (蓝图 v2.1 §3.3)

        条件: 结构破坏 (顶背离 + 跌破 MA55) 或 时空严重不足
        - 结构破坏: J-1 MACD 背离 + J 价格跌破 MA55
        - 时空严重不足: overall < 0.5
        """
        # 结构破坏: J-1 背离 + J 跌破 MA55
        j1_divergence = ctx_j1.macd_hist < 0 or (
            ctx_j1.dif > 0 and ctx_j1.dif < ctx_j1.dea
        )
        below_ma55 = ctx_j.ma55 > 0 and ctx_j.price < ctx_j.ma55
        structure_broken = j1_divergence and below_ma55

        # 时空严重不足
        spacetime_failed = spacetime_provided and spacetime.overall < 0.5

        return structure_broken or spacetime_failed

    # ========== 置信度与风险 ==========

    def _calc_confidence(
        self,
        state: MacroState,
        structure_match: bool,
        bollinger_confirmed: bool,
        ma55_resonance: bool,
        spacetime: SpacetimeScore,
        main_surge: bool,
    ) -> float:
        """计算置信度"""
        base = self.STATE_CONFIDENCE.get(state, 0.3)
        if structure_match:
            base = min(1.0, base + 0.1)
        if bollinger_confirmed:
            base = min(1.0, base + 0.1)
        if ma55_resonance:
            base = min(1.0, base + 0.05)
        if spacetime.sufficient:
            base = min(1.0, base + 0.1)
        if main_surge:
            base = min(1.0, base + 0.15)
        return round(base, 4)

    def _calc_risk(
        self,
        state: MacroState,
        spacetime: SpacetimeScore,
        action: ActionType,
    ) -> float:
        """计算风险等级 [0, 1], 越高越危险"""
        # 基础风险: 偏空状态风险高
        base = 0.3 if state.is_bullish else 0.6

        # 时空不充分增加风险
        if not spacetime.sufficient and spacetime.overall > 0:
            base = min(1.0, base + 0.2)

        # 买入类操作在弱状态中风险高
        if action in (ActionType.SCOUT_WITH_STOP, ActionType.BUY_CAUTIOUSLY):
            if not state.is_bullish:
                base = min(1.0, base + 0.15)

        # 极端状态: 顺势低风险, 逆势高风险
        if state.is_extreme:
            if state.is_bullish and action in (ActionType.STRONG_ADD, ActionType.ADD_ON_PULLBACK):
                base = max(0.1, base - 0.2)
            elif not state.is_bullish and action in (ActionType.STRONG_REDUCE,):
                base = max(0.1, base - 0.2)

        return round(base, 4)
