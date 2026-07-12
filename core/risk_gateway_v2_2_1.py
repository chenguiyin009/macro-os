"""Trinity OS v2.2.1 — 风险与仓位管理子系统（独立可插拔组件，Phase 1）。

提供固定硬止损、正金字塔加仓（含宏观熔断）、倒金字塔跟踪止盈的核心引擎。
本模块与 Trinity 其他模块**零耦合**：仅消费 `MarketState` 标准化契约，不依赖
StructureParser / SpacetimeEngine / Orchestrator / Scheduler；Phase 2 再注入 Ledger 钩子。

关键设计：
  * 止损优先于仓位：初始入场即锁定 D3 低点或 ATR 派生的硬止损。
  * 正金字塔加仓：ladder = [0.40, 0.30, 0.20, 0.10]，右侧 J-1 回抽确认后逐级加仓。
  * 宏观熔断：VIX >= 35 或 大宗商品异动 >= 15% 时禁止加仓。
  * 倒金字塔跟踪止盈：ATR + swing low 跟踪，跌破阈值按比例减仓（−5%→−15%，−8%→−30%），
    跟踪止损永不破初始硬止损。

注：本文件实现严格贴合架构设计；`PyramidManager.current_step` 初始化为 0
（非源码草稿中的 1），以正确使用 ladder[0]=0.40 并完成 4 级加仓。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class MarketState:
    """标准化市场状态契约（与 Trinity 其他模块解耦）"""
    current_price: float
    d3_low: float                    # Structure Parser 输出的初始 D3 低点
    atr: float
    spacetime_score: float
    j1_confirmed: bool               # J-1 回抽确认标志
    open: Optional[float] = None     # 当根 bar 开盘价（用于检测跳空低开穿透硬止损）
    macro_vix: float = 20.0          # VIX 或等效宏观波动率
    macro_commodity_shock: float = 0.0  # 布伦特等大宗商品异动幅度（可选）
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    """持仓对象"""
    entry_price: float
    size: float
    hard_stop: float
    remaining_size: float = field(init=False)
    is_initial: bool = False
    add_step: int = 0                # 第几次加仓（0=初始）

    def __post_init__(self):
        self.remaining_size = self.size


class MacroCircuitBreaker:
    """宏观 Regime Shift 熔断器"""

    def __init__(self, vix_threshold: float = 35.0, commodity_shock_threshold: float = 0.15):
        self.vix_threshold = vix_threshold
        self.commodity_shock_threshold = commodity_shock_threshold

    def is_triggered(self, state: MarketState) -> bool:
        if state.macro_vix >= self.vix_threshold:
            logger.warning(f"Macro Circuit Breaker: VIX {state.macro_vix} >= {self.vix_threshold}")
            return True
        if abs(state.macro_commodity_shock) >= self.commodity_shock_threshold:
            logger.warning(f"Macro Circuit Breaker: Commodity shock {state.macro_commodity_shock}")
            return True
        return False


class InitialEntryEngine:
    """初始入场引擎"""

    def __init__(self, base_risk_pct: float = 0.01):
        self.base_risk_pct = base_risk_pct

    def calculate_entry(self, account: float, state: MarketState) -> Optional[Position]:
        if state.spacetime_score <= 0:
            return None

        risk_dollars = account * self.base_risk_pct * state.spacetime_score
        structural_dist = max(state.current_price - state.d3_low, 0.0001)
        atr_dist = state.atr * 1.8 * 0.75
        effective_dist = max(structural_dist, atr_dist)

        hard_stop = state.current_price - effective_dist
        size = risk_dollars / effective_dist

        logger.info(f"Initial Entry calculated | Size: {size:.4f} | HardStop: {hard_stop:.2f}")
        return Position(
            entry_price=state.current_price,
            size=size,
            hard_stop=hard_stop,
            is_initial=True,
            add_step=0
        )


class PyramidManager:
    """正金字塔加仓引擎（含宏观熔断）"""

    def __init__(self, ladder: List[float] = None, max_total_risk_pct: float = 0.04, vix_threshold: float = 35.0):
        self.ladder = ladder or [0.40, 0.30, 0.20, 0.10]
        # FIX: 源码草稿为 1，会跳过 ladder[0]=0.40。改为 0 以完成 4 级加仓 [0.40,0.30,0.20,0.10]。
        self.current_step = 0  # 0 = 首次加仓索引（ladder[0]=0.40）
        self.max_total_risk_pct = max_total_risk_pct
        self.macro_breaker = MacroCircuitBreaker(vix_threshold=vix_threshold)

    def can_add_position(self, state: MarketState, current_total_risk_pct: float) -> bool:
        if self.current_step >= len(self.ladder):
            return False
        if self.macro_breaker.is_triggered(state):
            return False
        if not (state.current_price > state.d3_low and state.j1_confirmed):
            return False
        if state.spacetime_score < 0.80:
            return False
        if current_total_risk_pct >= self.max_total_risk_pct:
            return False
        return True

    def calculate_add_on(self, account: float, state: MarketState, base_risk_pct: float) -> Optional[Position]:
        add_factor = self.ladder[self.current_step]
        risk_dollars = (account * base_risk_pct) * add_factor * state.spacetime_score
        structural_dist = max(state.current_price - state.d3_low, 0.0001)

        size = risk_dollars / structural_dist
        hard_stop = state.d3_low  # 强制绑定初始结构硬止损

        pos = Position(
            entry_price=state.current_price,
            size=size,
            hard_stop=hard_stop,
            is_initial=False,
            add_step=self.current_step
        )
        self.current_step += 1
        logger.info(f"Pyramid Add-on #{self.current_step-1} | Size: {size:.4f} | Step Risk Factor: {add_factor}")
        return pos


class TrailingStopEngine:
    """跟踪止盈引擎（倒金字塔 + ATR + 结构保护）"""

    def __init__(self, trailing_multiplier: float = 2.0):
        self.trailing_multiplier = trailing_multiplier
        self.highest_price: float = 0.0
        self.current_trailing_stop: float = float('-inf')

    def update_trailing_stop(self, state: MarketState, swing_low: Optional[float] = None,
                              initial_hard_stop: Optional[float] = None) -> float:
        self.highest_price = max(self.highest_price, state.current_price)

        atr_stop = self.highest_price - (state.atr * self.trailing_multiplier)
        swing_stop = (swing_low - state.atr * 0.5) if swing_low else float('-inf')

        new_stop = max(atr_stop, swing_stop, self.current_trailing_stop)

        # 永远不低于初始结构硬止损（保护风险）
        if initial_hard_stop is not None:
            new_stop = max(new_stop, initial_hard_stop)

        self.current_trailing_stop = new_stop
        return new_stop

    def calculate_exit_size(self, current_price: float, total_remaining_size: float) -> float:
        if self.highest_price <= 0:
            return 0.0
        drawdown = (self.highest_price - current_price) / self.highest_price

        if drawdown > 0.08:
            return total_remaining_size * 0.30
        elif drawdown > 0.05:
            return total_remaining_size * 0.15
        return 0.0


class RiskGateway:
    """Trinity OS v2.2.1 风控总网关"""

    def __init__(self):
        self.entry_engine = InitialEntryEngine(base_risk_pct=0.01)
        self.pyramid_manager = PyramidManager()
        self.trailing_engine = TrailingStopEngine()
        self.active_positions: List[Position] = []
        self.initial_hard_stop: Optional[float] = None

    def _calculate_total_risk_pct(self, account: float) -> float:
        if not self.active_positions or account <= 0:
            return 0.0
        total_risk_dollars = sum(
            (p.entry_price - p.hard_stop) * p.remaining_size for p in self.active_positions
        )
        return total_risk_dollars / account

    def process_tick(self, account_balance: float, state: MarketState) -> Dict[str, Any]:
        log = {"action": "hold", "details": {}}

        # 1. 初始建仓
        if not self.active_positions:
            pos = self.entry_engine.calculate_entry(account_balance, state)
            if pos:
                self.active_positions.append(pos)
                self.initial_hard_stop = pos.hard_stop
                self.trailing_engine.highest_price = state.current_price
                log = {"action": "initial_entry", "position": pos}
            return log

        # 1.5 Phase 2 硬止损跳空强制全平（最高优先级，阻断后续加仓/跟踪止盈）
        # 消除回测"理想止损"幻觉：价格跳空低开/跌破 hard_stop 时，按真实跳空价全平。
        breach, gap_price, breached_pos = self._detect_hard_stop_breach(state)
        if breach:
            return self._fatal_gap_liquidation(gap_price, breached_pos)

        current_total_risk = self._calculate_total_risk_pct(account_balance)

        # 2. 金字塔加仓（含宏观熔断）
        if self.pyramid_manager.can_add_position(state, current_total_risk):
            pos = self.pyramid_manager.calculate_add_on(account_balance, state, self.entry_engine.base_risk_pct)
            if pos:
                self.active_positions.append(pos)
                log = {"action": "pyramid_add", "position": pos, "total_risk_pct": current_total_risk}

        # 3. 跟踪止盈 + 倒金字塔减仓
        swing_low = state.current_price - (state.atr * 1.2)
        new_stop = self.trailing_engine.update_trailing_stop(
            state, swing_low=swing_low, initial_hard_stop=self.initial_hard_stop
        )

        total_remaining = sum(p.remaining_size for p in self.active_positions)
        exit_size = self.trailing_engine.calculate_exit_size(state.current_price, total_remaining)

        if exit_size > 0:
            # 简单按比例从最新头寸开始减仓（实际可按 FIFO 或自定义策略）
            remaining_to_exit = exit_size
            for pos in reversed(self.active_positions):
                if remaining_to_exit <= 0:
                    break
                deduct = min(pos.remaining_size, remaining_to_exit)
                pos.remaining_size -= deduct
                remaining_to_exit -= deduct

            log = {
                "action": "trailing_exit",
                "exit_size": exit_size,
                "new_trailing_stop": new_stop,
                "total_remaining_size": sum(p.remaining_size for p in self.active_positions)
            }

        # 清理已平仓头寸
        self.active_positions = [p for p in self.active_positions if p.remaining_size > 0.0001]

        return log

    # ------------------------------------------------------------------
    # Phase 2: 硬止损跳空强制全平（Gap Liquidation）
    # ------------------------------------------------------------------
    def _detect_hard_stop_breach(self, state: MarketState) -> "tuple[bool, Optional[float], Optional[Position]]":
        """检测是否发生硬止损跳空穿透（Phase 2 修订：逐个 pos 检查 + initial_hard_stop 并集）。

        判定（任一成立即视为穿透）：
          * 当根 bar 开盘价直接低于任一持仓的 hard_stop（跳空低开）；
          * 当根 bar 当前价低于任一持仓的 hard_stop（盘中击穿）；
          * 当根 bar 开盘价/当前价低于全局 initial_hard_stop。
        返回 (是否穿透, 触发穿透的参考价, 首个被穿透的持仓)。
        """
        if not self.active_positions:
            return False, None, None
        # 逐个持仓检查（PDF 核心意图：任何单笔穿透都应触发全平）
        for pos in self.active_positions:
            if state.open is not None and state.open < pos.hard_stop:
                return True, state.open, pos
            if state.current_price < pos.hard_stop:
                return True, state.current_price, pos
        # 并集：全局 initial_hard_stop 穿透也触发
        if self.initial_hard_stop is not None:
            if state.open is not None and state.open < self.initial_hard_stop:
                return True, state.open, None
            if state.current_price < self.initial_hard_stop:
                return True, state.current_price, None
        return False, None, None

    def _fatal_gap_liquidation(self, gap_price: Optional[float], breached_pos: Optional[Position] = None) -> Dict[str, Any]:
        """硬止损跳空强制全平：清空所有持仓，返回强平动作日志。

        真实撮合价 = min(hard_stop, gap_price)（跳空越深，成交越差），
        由回测器在 _execute_action 中再叠加 ATR 滑点 + 手续费，体现真实"流血"。
        """
        breached_stop = breached_pos.hard_stop if breached_pos is not None else (self.initial_hard_stop or 0.0)
        ref = gap_price if gap_price is not None else breached_stop
        liq_price = min(breached_stop, ref) if breached_stop > 0 else ref
        closed = [
            {
                "entry_price": p.entry_price,
                "size": p.remaining_size,
                "hard_stop": p.hard_stop,
            }
            for p in self.active_positions
        ]
        total_closed_size = sum(c["size"] for c in closed)
        self.active_positions = []
        self.initial_hard_stop = None
        logger.warning(
            "FATAL_GAP_LIQUIDATION: 硬止损跳空穿透，强制全平 | liq_price=%.2f | size=%.4f",
            liq_price,
            total_closed_size,
        )
        return {
            "action": "fatal_gap_liquidation",
            "reason": "hard_stop_gap_breach",
            "breached_stop": breached_stop,
            "gap_price": ref,
            "liquidation_price": liq_price,
            "closed_positions": closed,
            "total_closed_size": total_closed_size,
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "active_positions_count": len(self.active_positions),
            "total_remaining_size": sum(p.remaining_size for p in self.active_positions),
            "current_trailing_stop": self.trailing_engine.current_trailing_stop,
            "pyramid_step": self.pyramid_manager.current_step,
            "initial_hard_stop": self.initial_hard_stop
        }


# ==================== 使用示例 ====================
if __name__ == "__main__":
    gateway = RiskGateway()
    account = 1_000_000.0

    # 场景1: 初始入场
    state1 = MarketState(current_price=100.0, d3_low=95.0, atr=2.0, spacetime_score=0.87,
                         j1_confirmed=False, macro_vix=22.0)
    result1 = gateway.process_tick(account, state1)
    print("场景1:", result1)
    print("状态:", gateway.get_status())

    # 场景2: 右侧回抽确认 + 加仓
    state2 = MarketState(current_price=103.5, d3_low=95.0, atr=2.1, spacetime_score=0.91,
                         j1_confirmed=True, macro_vix=24.0)
    result2 = gateway.process_tick(account, state2)
    print("\n场景2:", result2)
    print("状态:", gateway.get_status())

    # 场景3: 价格冲高后回落 → 触发倒金字塔止盈
    state3 = MarketState(current_price=98.0, d3_low=95.0, atr=2.8, spacetime_score=0.65,
                         j1_confirmed=True, macro_vix=28.0)
    gateway.trailing_engine.highest_price = 112.0  # 模拟曾到高点
    result3 = gateway.process_tick(account, state3)
    print("\n场景3:", result3)
    print("最终状态:", gateway.get_status())
