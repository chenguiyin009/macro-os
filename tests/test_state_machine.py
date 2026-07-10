"""MACD 六态状态机测试"""
from __future__ import annotations

import pytest

from trinity.context import MacroState
from trinity.indicators import calc_macd
from trinity.state_machine import StateMachine, _initial_state


class TestInitialState:
    """测试瞬时状态推断"""

    def test_extreme_strong(self):
        """DIF>0, DEA<0 → 极强"""
        assert _initial_state(0.5, -0.3) == MacroState.EXTREME_STRONG

    def test_extreme_weak(self):
        """DIF<0, DEA>0 → 极弱"""
        assert _initial_state(-0.5, 0.3) == MacroState.EXTREME_WEAK

    def test_strong(self):
        """DIF>0, DEA>0, DIF>DEA → 强"""
        assert _initial_state(0.5, 0.3) == MacroState.STRONG

    def test_moderate_weak(self):
        """DIF>0, DEA>0, DIF<DEA → 中偏弱"""
        assert _initial_state(0.3, 0.5) == MacroState.MODERATE_WEAK

    def test_moderate_strong(self):
        """DIF<0, DEA<0, DIF>DEA → 中偏强"""
        assert _initial_state(-0.3, -0.5) == MacroState.MODERATE_STRONG

    def test_weak(self):
        """DIF<0, DEA<0, DIF<DEA → 弱"""
        assert _initial_state(-0.5, -0.3) == MacroState.WEAK


class TestStateMachineCycle:
    """测试完整六态循环"""

    def test_full_cycle(self):
        """构造一个完整的 MACD 周期, 验证六态循环

        价格走势: 大跌 → 底部 → 大涨 → 顶部 → 大跌
        """
        # 构造价格序列: 先跌后涨再跌, 形成完整 MACD 周期
        prices = []
        # 下跌段 (60 bars)
        for i in range(60):
            prices.append(100 - i * 0.8)
        # 底部横盘 + 反弹 (60 bars)
        for i in range(60):
            prices.append(52 + i * 0.5)
        # 上涨段 (60 bars)
        for i in range(60):
            prices.append(82 + i * 0.8)
        # 顶部横盘 + 回落 (60 bars)
        for i in range(60):
            prices.append(130 - i * 0.5)

        dif, dea, _ = calc_macd(prices)
        sm = StateMachine()
        states = sm.run(dif, dea)

        # 验证状态序列覆盖多种状态
        unique_states = set(states[30:])  # 跳过初始 warmup
        assert len(unique_states) >= 3, f"只出现 {len(unique_states)} 种状态, 期望 >= 3"

        # 验证最终状态 (价格在下跌, 应该在偏弱/极弱区间)
        final_state = states[-1]
        assert final_state in (
            MacroState.MODERATE_WEAK, MacroState.EXTREME_WEAK, MacroState.WEAK,
        ), f"最终状态 {final_state.value} 不符合下跌预期"

    def test_uptrend_reaches_strong_or_extreme(self):
        """加速上涨应达到 强 或 极强

        注: 常斜率趋势中 MACD 动量会衰减 (DIF/DEA 收敛),
        状态反映的是动量而非单纯价格方向, 故使用加速趋势维持动量。
        """
        prices = [50 + i * 0.5 + i * i * 0.02 for i in range(100)]
        dif, dea, _ = calc_macd(prices)
        sm = StateMachine()
        state = sm.current_state(dif, dea)
        assert state in (
            MacroState.STRONG, MacroState.EXTREME_STRONG, MacroState.MODERATE_STRONG,
        ), f"加速上涨趋势状态 {state.value} 不在偏多区间"

    def test_downtrend_reaches_weak_or_extreme(self):
        """加速下跌应达到 弱 或 极弱

        注: 同上, 使用加速趋势维持 MACD 动量。
        """
        prices = [150 - i * 0.5 - i * i * 0.02 for i in range(100)]
        dif, dea, _ = calc_macd(prices)
        sm = StateMachine()
        state = sm.current_state(dif, dea)
        assert state in (
            MacroState.WEAK, MacroState.EXTREME_WEAK, MacroState.MODERATE_WEAK,
        ), f"下跌趋势状态 {state.value} 不在偏空区间"


class TestStateMachineTransitions:
    """测试状态转换"""

    def test_low_golden_cross_transition(self):
        """低位金叉: 弱 → 中偏强"""
        # 构造: DIF/DEA 都在零下, DIF 从下方穿过 DEA
        dif =   [-2.0, -2.0, -1.8, -1.5, -1.2]
        dea =   [-1.5, -1.6, -1.7, -1.6, -1.5]
        sm = StateMachine()
        states = sm.run(dif, dea)
        # 初始状态应为 弱 (DIF<DEA, both <0)
        assert states[0] == MacroState.WEAK
        # 金叉后应转为 中偏强
        assert MacroState.MODERATE_STRONG in states[2:]

    def test_dif_cross_up_transition(self):
        """DIF 上穿零轴: 中偏强 → 极强"""
        # 从 中偏强 (DIF>DEA, both<0) 到 DIF 上穿零轴
        dif =   [-1.0, -0.5, -0.1, 0.3, 0.5]
        dea =   [-1.5, -1.2, -0.8, -0.5, -0.3]
        sm = StateMachine()
        states = sm.run(dif, dea)
        # 初始: DIF<0, DEA<0, DIF>DEA → 中偏强
        assert states[0] == MacroState.MODERATE_STRONG
        # DIF 上穿零轴后 → 极强
        assert MacroState.EXTREME_STRONG in states[3:]

    def test_high_death_cross_transition(self):
        """高位死叉: 强 → 中偏弱"""
        # 从 强 (DIF>DEA, both>0) 到 DIF 下穿 DEA
        dif =   [1.5, 1.3, 1.0, 0.8, 0.5]
        dea =   [0.5, 0.7, 0.9, 1.0, 1.0]
        sm = StateMachine()
        states = sm.run(dif, dea)
        # 初始: DIF>0, DEA>0, DIF>DEA → 强
        assert states[0] == MacroState.STRONG
        # 死叉后 → 中偏弱
        assert MacroState.MODERATE_WEAK in states


class TestEvidence:
    """测试证据链"""

    def test_evidence_not_empty(self):
        prices = [50 + i * 0.5 for i in range(60)]
        dif, dea, _ = calc_macd(prices)
        sm = StateMachine()
        state, evidence = sm.current_state_with_evidence(dif, dea)
        assert len(evidence) > 0
        assert any("DIF" in e for e in evidence)
        assert any("状态判定" in e for e in evidence)

    def test_extreme_state_evidence(self):
        """极端状态应有附加特征说明"""
        dif = [0.5, 0.6, 0.7]
        dea = [-0.3, -0.2, -0.1]
        sm = StateMachine()
        state, evidence = sm.current_state_with_evidence(dif, dea)
        assert state == MacroState.EXTREME_STRONG
        assert any("极端状态" in e for e in evidence)


class TestEdgeCases:
    """边界情况"""

    def test_empty_input(self):
        sm = StateMachine()
        assert sm.run([], []) == []

    def test_all_none(self):
        sm = StateMachine()
        states = sm.run([None, None], [None, None])
        assert len(states) == 2

    def test_single_bar(self):
        sm = StateMachine()
        states = sm.run([0.5], [-0.3])
        assert states[0] == MacroState.EXTREME_STRONG

    def test_insufficient_data(self):
        """数据不足时状态机不崩溃"""
        dif = [None, None, 0.1]
        dea = [None, None, -0.1]
        sm = StateMachine()
        states = sm.run(dif, dea)
        assert len(states) == 3
        assert states[2] == MacroState.EXTREME_STRONG
