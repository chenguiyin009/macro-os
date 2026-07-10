"""Trinity OS v2.1 — 因果对齐守卫 (铁律 #1 延迟双指针)

守护"同序列收益接线"不被破坏:

  1. 收益 (固定窗口) 仅依赖 market_data[entry_index:], 与入场前数据无关
  2. 决策端 (visible) 不含未来; entry_index == cursor
  3. 决策与清算使用【同一】市场序列引用
  4. OutcomeEvent.linked_event_id 与 DecisionEvent.event_id 严格绑定
  5. 蓝图契约别名层 (trinity.core.contracts) 正确
  6. PerformanceAggregator.diagnose(symbol=...) 向后兼容
  7. StructureParser 输出携带 tier 形态层级元数据
"""
from __future__ import annotations

from trinity.aggregator import PerformanceAggregator
from trinity.context import (
    ActionType,
    Decision,
    JLevelContext,
    OHLCV,
    StructureType,
    TradingLevel,
)
from trinity.core.contracts import (
    EvidenceFactor,
    OutcomeEvent as ContractOutcomeEvent,
    OutcomeResult as ContractOutcomeResult,
    TrinityEvent,
)
from trinity.core.engine import EvidenceFactor as EngineEvidenceFactor
from trinity.gateway import Gateway
from trinity.ledger import DecisionEvent, EventSourcingTracker, OutcomeEvent
from trinity.outcome import OutcomeResult, OutcomeSimulator
from trinity.replay import ReplayEngine
from trinity.structure_parser import Pivot, StructureParser


# ================================================================
# 工具
# ================================================================

def _make_market(n: int = 60, seed_base: float = 100.0):
    """构造确定性单调递增 OHLCV 序列, 便于精确断言收益"""
    return [
        OHLCV(
            timestamp=i, open=c, high=c + 1.0, low=c - 1.0, close=c, volume=1000,
        )
        for i, c in enumerate([seed_base + i for i in range(n)])
    ]


def _dummy_decision(symbol: str = "TEST"):
    return Decision(action=ActionType.HOLD, confidence=0.3, symbol=symbol)


def _dummy_ctx(symbol: str = "TEST"):
    return JLevelContext(level=TradingLevel.J, symbol=symbol)


# ================================================================
# 1. 收益仅依赖 entry_index 之后 (固定窗口因果干净)
# ================================================================

def test_outcome_depends_only_on_entry_onward():
    sim = OutcomeSimulator()
    market = _make_market(60)
    entry = 10
    base = sim.simulate(entry, market, fixed_horizon=20)

    # 固定窗口收益 = future[-1].close / entry.close - 1, 仅依赖 [entry+1 : entry+1+horizon]
    future = market[entry + 1: entry + 1 + 20]
    manual = future[-1].close / market[entry].close - 1
    assert abs(base.fixed_return - manual) < 1e-9

    # 修改入场【之前】的数据: 固定窗口收益应完全不变
    market_pre = [
        OHLCV(timestamp=i, open=c, high=c + 1, low=c - 1,
              close=(c * 2 if i < entry else c), volume=1000)
        for i, c in enumerate([100.0 + i for i in range(60)])
    ]
    changed_pre = sim.simulate(entry, market_pre, fixed_horizon=20)
    assert changed_pre.fixed_return == base.fixed_return

    # 修改入场【之后、窗口内】的数据 (窗口最后一棒): 固定窗口收益应改变
    market_post = [
        OHLCV(timestamp=i, open=c, high=c + 1, low=c - 1,
              close=(c + 5 if i == entry + 20 else c), volume=1000)
        for i, c in enumerate([100.0 + i for i in range(60)])
    ]
    changed_post = sim.simulate(entry, market_post, fixed_horizon=20)
    assert changed_post.fixed_return != base.fixed_return


# ================================================================
# 2. 决策端无未来函数 + entry_index == cursor
# ================================================================

def test_replay_no_lookahead_entry_equals_cursor():
    gw = Gateway(source="synthetic")
    market = gw.fetch(symbol="TEST", bars=120, seed=7)
    replay = ReplayEngine(engine=None, data=market, symbol="TEST")

    seen = []

    def decide_fn(visible, entry_index):
        # 铁律 #1: 可见窗口长度 == 当前指针 + 1 (不含未来)
        assert len(visible) == entry_index + 1
        seen.append(entry_index)
        return {
            "factors": [EvidenceFactor(module="J", factor="STATE",
                                       value=0.5, weight=0.3)],
            "decision": _dummy_decision(),
            "contexts": {TradingLevel.J: _dummy_ctx()},
        }

    records = replay.run_with_outcomes(
        decide_fn, simulate_fn=OutcomeSimulator().simulate,
        fixed_horizon=20, min_bars=30,
    )
    assert records
    # 每个记录的 entry_index 等于其决策时刻的 cursor
    for rec in records:
        assert rec["entry_index"] in seen


# ================================================================
# 3. 决策与清算使用【同一】序列引用
# ================================================================

def test_replay_same_series_for_decision_and_outcome():
    gw = Gateway(source="synthetic")
    market = gw.fetch(symbol="TEST", bars=120, seed=7)
    replay = ReplayEngine(engine=None, data=market, symbol="TEST")

    sim_calls = []

    def decide_fn(visible, entry_index):
        return {
            "factors": [EvidenceFactor(module="J", factor="STATE",
                                       value=0.5, weight=0.3)],
            "decision": _dummy_decision(),
            "contexts": {TradingLevel.J: _dummy_ctx()},
        }

    def simulate_fn(entry_index, market_data_arg):
        sim_calls.append((entry_index, market_data_arg))
        return OutcomeSimulator().simulate(entry_index, market_data_arg)

    records = replay.run_with_outcomes(
        decide_fn, simulate_fn=simulate_fn,
        fixed_horizon=20, min_bars=30,
    )

    # 清算端拿到的就是回放的原始市场序列 (浅拷贝共享同一批 OHLCV 对象)
    assert sim_calls
    for entry_index, md in sim_calls:
        # TemporalBuffer 存的是 list(data) 浅拷贝: 列表对象不同, 但元素引用相同
        assert md == market
        assert md[0] is market[0]

    # 记录中的收益 == 用该 entry_index 在同序列上重算的收益 (因果可复现)
    sim = OutcomeSimulator()
    for rec in records:
        recomputed = sim.simulate(rec["entry_index"], market)
        assert recomputed.fixed_return == rec["outcome"].fixed_return
        assert recomputed.dynamic_return == rec["outcome"].dynamic_return


# ================================================================
# 4. OutcomeEvent.linked_event_id 严格绑定 DecisionEvent.event_id
# ================================================================

def test_outcome_event_linked_event_id():
    gw = Gateway(source="synthetic")
    market = gw.fetch(symbol="TEST", bars=120, seed=7)
    replay = ReplayEngine(engine=None, data=market, symbol="TEST")
    ledger = EventSourcingTracker()

    def decide_fn(visible, entry_index):
        return {
            "factors": [EvidenceFactor(module="J", factor="STATE",
                                       value=0.5, weight=0.3)],
            "decision": _dummy_decision(),
            "contexts": {TradingLevel.J: _dummy_ctx()},
        }

    records = replay.run_with_outcomes(
        decide_fn, simulate_fn=OutcomeSimulator().simulate,
        fixed_horizon=20, min_bars=30, ledger=ledger,
    )

    # 每个决策事件都有对应的收益事件, 且 linked_event_id 一致
    assert len(ledger.outcomes()) == len(records) == len(ledger)
    for rec in records:
        oc = ledger.outcome_for(rec["event_id"])
        assert oc is not None
        assert oc.linked_event_id == rec["event_id"]
        assert oc.entry_index == rec["entry_index"]
        # 收益事件可被持久化/反序列化且不丢溯源
        # (OutcomeResult.to_dict 四舍五入至 6 位, 用近似比较)
        assert abs(oc.result["fixed_return"] - rec["outcome"].fixed_return) < 1e-5
        assert abs(oc.result["dynamic_return"] - rec["outcome"].dynamic_return) < 1e-5


# ================================================================
# 5. 蓝图契约别名层
# ================================================================

def test_contracts_alias_layer():
    assert TrinityEvent is DecisionEvent
    assert EvidenceFactor is EngineEvidenceFactor
    assert OutcomeResult is ContractOutcomeResult
    assert OutcomeEvent is ContractOutcomeEvent


# ================================================================
# 6. diagnose(symbol=...) 向后兼容
# ================================================================

def test_diagnose_accepts_symbol_kwarg():
    agg = PerformanceAggregator()
    # 样本不足时走显著性铁律分支, 仍应返回 symbol
    rep = agg.diagnose(symbol="SYM_X")
    assert rep.symbol == "SYM_X"

    # 充足样本时 symbol 同样透传 (用 replay 风格注入样本)
    gw = Gateway(source="synthetic")
    market = gw.fetch(symbol="X", bars=200, seed=3)
    sim = OutcomeSimulator()
    for i in range(15):
        ei = 10 + i * 10
        if ei + 20 >= len(market):
            break
        oc = sim.simulate(ei, market, fixed_horizon=20)
        factors = [EvidenceFactor(module="J", factor="STATE",
                                  value=0.5 + 0.01 * i, weight=0.3)]
        agg.add_decision(factors, oc)
    rep2 = agg.diagnose(symbol="SYM_Y")
    assert rep2.symbol == "SYM_Y"
    assert rep2.sample_size >= 10


# ================================================================
# 7. StructureParser 输出携带 tier 形态层级
# ================================================================

def test_structure_tier_present():
    sp = StructureParser(threshold=0.03)

    # D 结构 (3 段) -> T3
    d3 = [Pivot(0, 100, is_high=True), Pivot(5, 90, is_high=False),
          Pivot(10, 95, is_high=True), Pivot(15, 85, is_high=False)]
    ev3 = sp.classify(d3)
    assert ev3.structure_type == StructureType.D
    assert ev3.tier == "T3"

    # D 结构 (4 段) -> T2
    d4 = d3 + [Pivot(20, 92, is_high=True)]
    ev4 = sp.classify(d4)
    assert ev4.structure_type == StructureType.D
    assert ev4.tier == "T2"

    # 段数 < 3 -> UNKNOWN -> tier 空
    unk = [Pivot(0, 100, is_high=True), Pivot(5, 90, is_high=False)]
    evu = sp.classify(unk)
    assert evu.structure_type == StructureType.UNKNOWN
    assert evu.tier == ""
