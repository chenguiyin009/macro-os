"""Trinity OS v2.1 - MACD 拓扑六态状态机

基于 MACD 与零轴的拓扑关系，定义六大状态循环:
  弱 → 中偏强 → 极强 → 强 → 中偏弱 → 极弱 → 弱 → ...

状态定义来源: 《三位一体时空要素和结构要素》对应表
  1. 低位金叉后, DIF 首穿零轴       = 中偏强
  2. DIF 首穿零轴 → DEA 首穿零轴    = 极强
  3. DEA 首穿零轴 → 高位死叉        = 强
  4. 高位死叉 → DIF 首穿零轴(下)    = 中偏弱
  5. DIF 首穿零轴(下) → DEA 首穿零轴(下) = 极弱
  6. DEA 首穿零轴(下) → 低位金叉    = 弱

核心思想: 状态不由本级别决定, 而是由上 1-2 个级别决定。
极强/极弱状态下, 顶部/底部背离无意义, 结构降为从属, 55 线重要性大幅上升。
"""
from __future__ import annotations

from typing import Optional

from trinity.context import MacroState


# ========== 事件检测 ==========

def _detect_events(
    dif: list[Optional[float]],
    dea: list[Optional[float]],
) -> list[str]:
    """检测 MACD 关键事件序列

    事件类型:
      LOW_GOLDEN_CROSS:   低位金叉 (DIF 上穿 DEA, 两者均 < 0)
      DIF_CROSS_UP:       DIF 上穿零轴
      DEA_CROSS_UP:       DEA 上穿零轴
      HIGH_DEATH_CROSS:   高位死叉 (DIF 下穿 DEA, 两者均 > 0)
      DIF_CROSS_DOWN:     DIF 下穿零轴
      DEA_CROSS_DOWN:     DEA 下穿零轴
      GOLDEN_CROSS:       普通金叉 (不在低位)
      DEATH_CROSS:        普通死叉 (不在高位)
    """
    events: list[str] = []
    start = None
    for i in range(len(dif)):
        if dif[i] is not None and dea[i] is not None:
            start = i
            break
    if start is None or start >= len(dif) - 1:
        return events

    for i in range(start + 1, len(dif)):
        if dif[i] is None or dea[i] is None:
            continue
        prev_dif, prev_dea = dif[i - 1], dea[i - 1]
        if prev_dif is None or prev_dea is None:
            continue
        cur_dif, cur_dea = dif[i], dea[i]

        # 金叉: DIF 从下方穿过 DEA
        if prev_dif <= prev_dea and cur_dif > cur_dea:
            if cur_dif < 0 and cur_dea < 0:
                events.append("LOW_GOLDEN_CROSS")
            else:
                events.append("GOLDEN_CROSS")

        # 死叉: DIF 从上方穿过 DEA
        if prev_dif >= prev_dea and cur_dif < cur_dea:
            if cur_dif > 0 and cur_dea > 0:
                events.append("HIGH_DEATH_CROSS")
            else:
                events.append("DEATH_CROSS")

        # DIF 穿零轴
        if prev_dif <= 0 and cur_dif > 0:
            events.append("DIF_CROSS_UP")
        elif prev_dif >= 0 and cur_dif < 0:
            events.append("DIF_CROSS_DOWN")

        # DEA 穿零轴
        if prev_dea <= 0 and cur_dea > 0:
            events.append("DEA_CROSS_UP")
        elif prev_dea >= 0 and cur_dea < 0:
            events.append("DEA_CROSS_DOWN")

    return events


# ========== 状态循环定义 ==========

# 状态转换表: (当前状态, 事件) → 新状态
# 完整循环: WEAK → MODERATE_STRONG → EXTREME_STRONG → STRONG → MODERATE_WEAK → EXTREME_WEAK → WEAK
_TRANSITIONS: dict[tuple[MacroState, str], MacroState] = {
    # 弱 → 中偏强: 低位金叉触发
    (MacroState.WEAK, "LOW_GOLDEN_CROSS"): MacroState.MODERATE_STRONG,
    # 中偏强 → 极强: DIF 上穿零轴
    (MacroState.MODERATE_STRONG, "DIF_CROSS_UP"): MacroState.EXTREME_STRONG,
    # 极强 → 强: DEA 上穿零轴
    (MacroState.EXTREME_STRONG, "DEA_CROSS_UP"): MacroState.STRONG,
    # 强 → 中偏弱: 高位死叉
    (MacroState.STRONG, "HIGH_DEATH_CROSS"): MacroState.MODERATE_WEAK,
    # 中偏弱 → 极弱: DIF 下穿零轴
    (MacroState.MODERATE_WEAK, "DIF_CROSS_DOWN"): MacroState.EXTREME_WEAK,
    # 极弱 → 弱: DEA 下穿零轴
    (MacroState.EXTREME_WEAK, "DEA_CROSS_DOWN"): MacroState.WEAK,
}


def _initial_state(dif_val: float, dea_val: float) -> MacroState:
    """根据当前 DIF/DEA 值推断初始状态 (无历史事件时的瞬时判定)

    映射表:
      DIF<0, DEA<0, DIF>DEA → 中偏强 (金叉后, DIF 未上零轴)
      DIF>0, DEA<0          → 极强   (DIF 上零轴, DEA 未上)
      DIF>0, DEA>0, DIF>DEA → 强     (DEA 上零轴, 未死叉)
      DIF>0, DEA>0, DIF<DEA → 中偏弱 (高位死叉后, DIF 未下零轴)
      DIF<0, DEA>0          → 极弱   (DIF 下零轴, DEA 未下)
      DIF<0, DEA<0, DIF<DEA → 弱     (DEA 下零轴, 未金叉)
    """
    if dif_val > 0 and dea_val < 0:
        return MacroState.EXTREME_STRONG
    if dif_val < 0 and dea_val > 0:
        return MacroState.EXTREME_WEAK
    if dif_val > 0 and dea_val > 0:
        return MacroState.STRONG if dif_val > dea_val else MacroState.MODERATE_WEAK
    if dif_val < 0 and dea_val < 0:
        return MacroState.MODERATE_STRONG if dif_val > dea_val else MacroState.WEAK
    # DIF 或 DEA 恰好为 0
    return MacroState.MODERATE_STRONG


# ========== 状态机 ==========

class StateMachine:
    """MACD 拓扑状态机

    用法:
        sm = StateMachine()
        states = sm.run(dif_series, dea_series)
        current = states[-1]
    """

    def run(
        self,
        dif: list[Optional[float]],
        dea: list[Optional[float]],
    ) -> list[MacroState]:
        """运行状态机, 返回每根 K 线对应的状态

        策略:
          1. 检测事件序列
          2. 从初始状态开始, 按事件驱动状态转换
          3. 未匹配转换规则的事件被忽略 (维持当前状态)
        """
        n = len(dif)
        if n == 0:
            return []

        states: list[MacroState] = [MacroState.MODERATE_STRONG] * n

        # 找到第一个有效值
        start = None
        for i in range(n):
            if dif[i] is not None and dea[i] is not None:
                start = i
                break
        if start is None:
            return states

        # 初始状态: 用第一个有效值推断
        current = _initial_state(dif[start], dea[start])
        states[start] = current

        # 逐 bar 推进, 检测事件并转换
        for i in range(start + 1, n):
            if dif[i] is None or dea[i] is None:
                states[i] = current
                continue

            prev_dif, prev_dea = dif[i - 1], dea[i - 1]
            cur_dif, cur_dea = dif[i], dea[i]
            if prev_dif is None or prev_dea is None:
                states[i] = current
                continue

            # 检测当前 bar 的事件
            event = self._detect_event_at(prev_dif, prev_dea, cur_dif, cur_dea)

            # 尝试状态转换
            if event is not None:
                key = (current, event)
                if key in _TRANSITIONS:
                    current = _TRANSITIONS[key]
                # 也检查是否可以用瞬时状态修正 (防止状态机卡住)
                else:
                    current = self._maybe_correct(current, cur_dif, cur_dea)

            states[i] = current

        return states

    def _detect_event_at(
        self,
        prev_dif: float, prev_dea: float,
        cur_dif: float, cur_dea: float,
    ) -> Optional[str]:
        """检测单根 K 线上的 MACD 事件"""
        # 金叉
        if prev_dif <= prev_dea and cur_dif > cur_dea:
            return "LOW_GOLDEN_CROSS" if cur_dif < 0 and cur_dea < 0 else "GOLDEN_CROSS"
        # 死叉
        if prev_dif >= prev_dea and cur_dif < cur_dea:
            return "HIGH_DEATH_CROSS" if cur_dif > 0 and cur_dea > 0 else "DEATH_CROSS"
        # DIF 穿零轴
        if prev_dif <= 0 and cur_dif > 0:
            return "DIF_CROSS_UP"
        if prev_dif >= 0 and cur_dif < 0:
            return "DIF_CROSS_DOWN"
        # DEA 穿零轴
        if prev_dea <= 0 and cur_dea > 0:
            return "DEA_CROSS_UP"
        if prev_dea >= 0 and cur_dea < 0:
            return "DEA_CROSS_DOWN"
        return None

    def _maybe_correct(
        self,
        current: MacroState,
        dif_val: float,
        dea_val: float,
    ) -> MacroState:
        """当事件未触发预定义转换时, 用瞬时值校正状态

        防止状态机因遗漏事件而卡在不正确的状态。
        只在状态明显矛盾时才纠正。
        """
        expected = _initial_state(dif_val, dea_val)
        # 如果瞬时状态和当前状态在强弱方向上一致, 不纠正
        if expected.is_bullish == current.is_bullish:
            return current
        # 方向矛盾时, 信任瞬时值
        return expected

    def current_state(
        self,
        dif: list[Optional[float]],
        dea: list[Optional[float]],
    ) -> MacroState:
        """获取最新 K 线的状态"""
        states = self.run(dif, dea)
        if not states:
            return MacroState.MODERATE_STRONG
        return states[-1]

    def current_state_with_evidence(
        self,
        dif: list[Optional[float]],
        dea: list[Optional[float]],
    ) -> tuple[MacroState, list[str]]:
        """获取最新状态及证据链"""
        state = self.current_state(dif, dea)
        evidence = self._build_evidence(state, dif, dea)
        return state, evidence

    def _build_evidence(
        self,
        state: MacroState,
        dif: list[Optional[float]],
        dea: list[Optional[float]],
    ) -> list[str]:
        """构建状态判定的证据链"""
        evidence: list[str] = []
        # 取最新有效值
        cur_dif = None
        cur_dea = None
        for i in range(len(dif) - 1, -1, -1):
            if dif[i] is not None:
                cur_dif = dif[i]
                break
        for i in range(len(dea) - 1, -1, -1):
            if dea[i] is not None:
                cur_dea = dea[i]
                break
        if cur_dif is not None and cur_dea is not None:
            evidence.append(f"DIF={cur_dif:.6f}, DEA={cur_dea:.6f}")
            evidence.append(f"DIF{'>' if cur_dif > 0 else '<='}0, DEA{'>' if cur_dea > 0 else '<='}0")
            if cur_dif > cur_dea:
                evidence.append("DIF>DEA (金叉状态)")
            else:
                evidence.append("DIF<DEA (死叉状态)")
        evidence.append(f"状态判定: {state.value}")
        # 极端状态附加特征
        if state.is_extreme:
            evidence.append("极端状态: 忽略顶部/底部背离, 55线重要性大幅上升")
        return evidence
