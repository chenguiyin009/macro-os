#!/usr/bin/env python3
"""Trinity OS v2.1 — Alpha 归因报告生成器

一键审计脚本：从 Event Ledger 加载历史决策 → OutcomeSimulator 计算收益
→ PerformanceAggregator 归因诊断 → 输出格式化报告。

闭环路径:
  1. 数据层 (Event Ledger):  记录"决策当时"的 Evidence (原因)
  2. 模拟层 (OutcomeSimulator): 计算"决策之后"的 Result (结果)
  3. 认知层 (PerformanceAggregator): 统计关联性, 回答"原因"如何导致"结果"

用法:
    python scripts/generate_alpha_report.py
    python scripts/generate_alpha_report.py --ledger data/ledger.json
    python scripts/generate_alpha_report.py --ledger data/ledger.json --symbol 000001 --bars 200 --seed 42
    python scripts/generate_alpha_report.py --ledger data/ledger.json --market-data data/market.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 将项目根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trinity.aggregator import (
    DiagnosticReport,
    FactorAlpha,
    InteractionEffect,
    PerformanceAggregator,
)
from trinity.context import OHLCV, MacroState, TradingLevel
from trinity.core.engine import EvidenceFactor, State, StateMachineEngine
from trinity.gateway import Gateway
from trinity.ledger import EventSourcingTracker
from trinity.outcome import OutcomeResult, OutcomeSimulator
from trinity.replay import ReplayEngine
from trinity.orchestrator import Orchestrator


# ================================================================
# 1. Ledger 加载
# ================================================================

def load_ledger(filepath: str) -> List[dict]:
    """加载 Ledger 文件, 返回事件列表

    支持两种格式:
      - EventSourcingTracker 格式 (trinity/ledger.py)
      - AntiAmnesiaTracker 格式 (trinity/core/engine.py)
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("events", data.get("memories", []))
    if not events:
        print(f"警告: Ledger 中无事件 ({filepath})", file=sys.stderr)
    return events


# ================================================================
# 2. 市场数据获取
# ================================================================

def load_market_data(filepath: str) -> List[OHLCV]:
    """从 JSON 文件加载市场数据"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    ohlcv = []
    for bar in data:
        ohlcv.append(OHLCV(
            timestamp=bar.get("timestamp", 0),
            open=bar["open"], high=bar["high"],
            low=bar["low"], close=bar["close"],
            volume=bar.get("volume", 0),
        ))
    return ohlcv


def generate_market_data(symbol: str, bars: int, seed: int) -> List[OHLCV]:
    """使用 Gateway 合成市场数据 (可复现)"""
    gw = Gateway(source="synthetic")
    return gw.fetch(symbol=symbol, bars=bars, seed=seed)


# ================================================================
# 3. 因子重构
# ================================================================

def reconstruct_factors(event: dict) -> List[EvidenceFactor]:
    """从 Ledger 事件中重构结构化证据因子

    Ledger 存储的是上下文快照和文本证据, 需要逆向提取因子值。
    """
    factors: list[EvidenceFactor] = []

    # 从 contexts 或 context_snapshot 中提取
    ctx_data = event.get("contexts") or event.get("context_snapshot") or {}

    # 处理 EventSourcingTracker 格式 (contexts 是 {level: dict})
    if isinstance(ctx_data, dict) and "state_j2" not in ctx_data:
        # 取 J+2 级别
        j2_ctx = ctx_data.get("J+2", {})
        j_ctx = ctx_data.get("J", {})
        j1_ctx = ctx_data.get("J-1", {})

        state_val = j2_ctx.get("state", "")
        structure_val = j_ctx.get("structure", "UNKNOWN")
        price = j2_ctx.get("price", 0)
        ma55 = j2_ctx.get("ma55", 0)
        boll_mid = j1_ctx.get("boll_mid", 0)
        dif = j1_ctx.get("dif", 0)
        dea = j1_ctx.get("dea", 0)
        macd_hist = j1_ctx.get("macd_hist", 0)
    else:
        # 处理 AntiAmnesiaTracker 格式 (扁平结构)
        state_val = ctx_data.get("state_j2", "强")
        structure_val = ctx_data.get("structure", "D")
        price = ctx_data.get("price", 0)
        ma55 = ctx_data.get("j_ma55", 0)
        boll_mid = ctx_data.get("j_minus_1_bollinger_mid", 0)
        dif = 0
        dea = 0
        macd_hist = 0

    # 状态因子
    state_confidence = {
        "极强": 0.9, "强": 0.75, "中偏强": 0.55,
        "中偏弱": 0.45, "弱": 0.3, "极弱": 0.2, "混沌": 0.3,
    }
    factors.append(EvidenceFactor(
        module="J+2", factor="STATE",
        value=state_confidence.get(state_val, 0.3), weight=0.3,
    ))

    # 结构因子
    struct_score = 0.85 if structure_val in ("A", "B") else 0.5
    factors.append(EvidenceFactor(
        module="J", factor="STRUCTURE",
        value=struct_score, weight=0.25,
    ))

    # MA55 回抽因子
    pullback = 1.0 if (boll_mid > 0 and price > 0 and abs(price - boll_mid) / boll_mid < 0.05) else 0.0
    factors.append(EvidenceFactor(
        module="J-1", factor="MA55_PULLBACK",
        value=pullback, weight=0.2,
    ))

    # 背离因子
    has_divergence = macd_hist < 0 or (dif > 0 and dif < dea)
    factors.append(EvidenceFactor(
        module="J-1", factor="DIVERGENCE",
        value=0.0 if has_divergence else 1.0, weight=0.15,
    ))

    # 时空因子
    decision = event.get("decision", {})
    st_score = decision.get("spacetime_overall", 0)
    if st_score == 0:
        st = event.get("spacetime_score")
        if st and isinstance(st, dict):
            st_score = st.get("total_score", 0)
    factors.append(EvidenceFactor(
        module="SPACETIME", factor="TOTAL_SCORE",
        value=st_score, weight=0.1,
    ))

    return factors


# ================================================================
# 4. 报告生成
# ================================================================

def generate_report(
    ledger_filepath: str,
    market_data: Optional[List[OHLCV]] = None,
    symbol: str = "DEFAULT",
    bars: int = 200,
    seed: int = 42,
    fixed_horizon: int = 20,
) -> DiagnosticReport:
    """生成 Alpha 归因报告 (基于已存账本)

    说明: 本函数加载的是"已决策"的账本 (决策上下文为快照), 因此收益
    来自一个与本序列解耦的演示性市场数据 — 这是**机制演示**, 用于验证
    归因管道 (因子 Alpha / 交互效应 / 权重建议) 是否工作, 而非策略预测。

    若需**因果有效**的闭环 (收益来自决策当时的真实市场后果),
    请使用 generate_report_from_replay() (铁律 #1 延迟双指针)。

    Args:
        ledger_filepath: Ledger JSON 文件路径
        market_data:     市场数据 (None 则自动生成)
        symbol:          标的 (生成数据用)
        bars:            K 线数 (生成数据用)
        seed:            随机种子 (生成数据用)
        fixed_horizon:   固定窗口周期

    Returns:
        DiagnosticReport 诊断报告
    """
    # 1. 加载 Ledger
    events = load_ledger(ledger_filepath)
    if not events:
        return DiagnosticReport(
            top_factors=[], top_interactions=[],
            recommendations=["Ledger 为空, 无可分析数据"],
            sample_size=0, avg_return=0, win_rate=0, symbol=symbol,
        )

    # 2. 获取市场数据
    if market_data is None:
        market_data = generate_market_data(symbol, bars, seed)

    if len(market_data) < fixed_horizon + 10:
        return DiagnosticReport(
            top_factors=[], top_interactions=[],
            recommendations=[f"市场数据不足 ({len(market_data)} < {fixed_horizon + 10})"],
            sample_size=0, avg_return=0, win_rate=0, symbol=symbol,
        )

    # 3. 对每个事件模拟收益
    simulator = OutcomeSimulator()
    aggregator = PerformanceAggregator()

    n_events = len(events)
    for i, event in enumerate(events):
        # 将事件索引映射到市场数据索引 (均匀分布)
        entry_index = int(i * (len(market_data) - fixed_horizon - 1) / max(n_events, 1))
        entry_index = max(0, min(entry_index, len(market_data) - fixed_horizon - 1))

        # 计算收益
        outcome = simulator.simulate(
            entry_index=entry_index,
            market_data=market_data,
            fixed_horizon=fixed_horizon,
        )

        # 重构因子并注入聚合器
        factors = reconstruct_factors(event)
        aggregator.add_decision(factors, outcome)

    # 4. 归因诊断
    return aggregator.diagnose()


# ================================================================
# 4b. 因果对齐报告 (铁律 #1 延迟双指针: 同序列决策+清算)
# ================================================================

def _replay_factors_from_levels(
    ctx_j2, ctx_j, ctx_j1, decision,
) -> List[EvidenceFactor]:
    """从真实级别上下文重构 5 个证据因子

    与 reconstruct_factors 同构, 但直接读取枚举的 .value
    (实时上下文为对象, 账本为 JSON 字符串)。
    """
    state_val = ctx_j2.state.value if ctx_j2.state else ""
    structure_val = (
        ctx_j.structure.structure_type.value if ctx_j.structure else "UNKNOWN"
    )
    price = ctx_j2.price
    ma55 = ctx_j2.ma55
    boll_mid = ctx_j1.boll_mid
    dif = ctx_j1.dif
    dea = ctx_j1.dea
    macd_hist = ctx_j1.macd_hist

    state_confidence = {
        "极强": 0.9, "强": 0.75, "中偏强": 0.55,
        "中偏弱": 0.45, "弱": 0.3, "极弱": 0.2, "混沌": 0.3,
    }
    factors: list[EvidenceFactor] = [
        EvidenceFactor(
            module="J+2", factor="STATE",
            value=state_confidence.get(state_val, 0.3), weight=0.3,
        ),
    ]

    struct_score = 0.85 if structure_val in ("A", "B") else 0.5
    factors.append(EvidenceFactor(
        module="J", factor="STRUCTURE", value=struct_score, weight=0.25,
    ))

    pullback = (
        1.0 if (boll_mid > 0 and price > 0
                and abs(price - boll_mid) / boll_mid < 0.05)
        else 0.0
    )
    factors.append(EvidenceFactor(
        module="J-1", factor="MA55_PULLBACK", value=pullback, weight=0.2,
    ))

    has_divergence = macd_hist < 0 or (dif > 0 and dif < dea)
    factors.append(EvidenceFactor(
        module="J-1", factor="DIVERGENCE",
        value=0.0 if has_divergence else 1.0, weight=0.15,
    ))

    st_score = decision.spacetime.overall if decision.spacetime else 0.0
    factors.append(EvidenceFactor(
        module="SPACETIME", factor="TOTAL_SCORE", value=st_score, weight=0.1,
    ))
    return factors


def _replay_decide(visible, entry_index, orch, symbol):
    """实时决策回调: 仅基于 visible() 窗口 (防未来函数)

    将单一回放序列按级别拆解为 J+2/J/J-1/J-2 上下文, 复用生产级
    Orchestrator._build_context, 保证与实盘决策同源。
    """
    ctx_j = orch._build_context(visible, TradingLevel.J, symbol)
    ctx_j2 = orch._build_context(visible[::5], TradingLevel.J_PLUS_2, symbol)
    ctx_j1 = orch._build_context(visible[-60:], TradingLevel.J_MINUS_1, symbol)
    ctx_j2m = orch._build_context(visible[-30:], TradingLevel.J_MINUS_2, symbol)

    spacetime = orch.spacetime_engine.evaluate(visible[::5], visible)
    decision = orch.decision_router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m, spacetime)

    factors = _replay_factors_from_levels(ctx_j2, ctx_j, ctx_j1, decision)
    contexts = {
        TradingLevel.J_PLUS_2: ctx_j2,
        TradingLevel.J: ctx_j,
        TradingLevel.J_MINUS_1: ctx_j1,
        TradingLevel.J_MINUS_2: ctx_j2m,
    }
    return {"factors": factors, "decision": decision, "contexts": contexts}


def generate_report_from_replay(
    symbol: str = "REPLAY",
    bars: int = 200,
    seed: int = 42,
    fixed_horizon: int = 20,
    min_bars: int = 30,
    ledger: Optional[EventSourcingTracker] = None,
) -> DiagnosticReport:
    """生成因果对齐的 Alpha 归因报告 (铁律 #1 闭环)

    与 generate_report (基于已存账本的"机制演示") 不同, 本函数:

      1. 生成【唯一】市场序列 market_data (Gateway, 可复现)
      2. 用 ReplayEngine 在该序列上逐步回放:
         - 决策端: 仅可见 visible() (实时窗口, 不含未来)
         - 清算端: entry_index = cursor 锚定, 切片【同一序列】
                   market_data[entry_index:] 计算收益
      3. 每个决策事件通过 OutcomeEvent.linked_event_id 绑定真实收益

    因此收益来自"决策当时"所对应的真实市场后果, 而非独立生成序列 —
    这是蓝图要求的"同序列收益接线"。

    Args:
        symbol:        回测标的
        bars:          K 线数
        seed:          随机种子
        fixed_horizon: 固定窗口周期
        min_bars:      最小可见 K 线数
        ledger:        可选账本, 用于持久化 (决策事件 + 收益事件)

    Returns:
        DiagnosticReport 因果对齐诊断报告
    """
    market_data = generate_market_data(symbol, bars, seed)
    if len(market_data) < fixed_horizon + 10:
        return DiagnosticReport(
            top_factors=[], top_interactions=[],
            recommendations=[f"市场数据不足 ({len(market_data)} < {fixed_horizon + 10})"],
            sample_size=0, avg_return=0, win_rate=0, symbol=symbol,
        )

    orch = Orchestrator()

    def decide_fn(visible, entry_index):
        return _replay_decide(visible, entry_index, orch, symbol)

    replay = ReplayEngine(engine=None, data=market_data, symbol=symbol)
    records = replay.run_with_outcomes(
        decide_fn,
        simulate_fn=OutcomeSimulator().simulate,
        fixed_horizon=fixed_horizon,
        min_bars=min_bars,
        ledger=ledger,
    )

    aggregator = PerformanceAggregator()
    for rec in records:
        aggregator.add_decision(rec["factors"], rec["outcome"])

    return aggregator.diagnose(symbol=symbol)


# ================================================================
# 5. 报告打印
# ================================================================

def print_report(report: DiagnosticReport, symbol: str = "DEFAULT") -> None:
    """格式化打印 Alpha 归因报告"""
    print("=" * 70)
    print(f"  Trinity OS v2.1 | Alpha 归因分析报告 | Symbol: {symbol}")
    print("=" * 70)

    # 概览
    print(f"\n{'─'*70}")
    print(f"  📊 概览")
    print(f"{'─'*70}")
    print(f"  样本数:       {report.sample_size}")
    print(f"  平均收益:     {report.avg_return:+.4%}")
    print(f"  胜率:         {report.win_rate:.2%}")

    # 因子 Alpha 排名
    print(f"\n{'─'*70}")
    print(f"  🏆 因子 Alpha 排名 (按 |Alpha| 降序)")
    print(f"{'─'*70}")
    if report.top_factors:
        print(f"  {'因子':<20} {'Alpha':>10} {'显著性':>8}")
        print(f"  {'─'*40}")
        for fname, alpha in report.top_factors:
            print(f"  {fname:<20} {alpha:>+10.4f} {'':>8}")
    else:
        print("  (无因子数据)")

    # 交互效应
    print(f"\n{'─'*70}")
    print(f"  🔬 交互效应检测 (2x2 Factorial)")
    print(f"{'─'*70}")
    if report.top_interactions:
        for ie in report.top_interactions:
            print(f"\n  {ie.factor_a} × {ie.factor_b}")
            print(f"    交互效应值:   {ie.interaction:+.4f}")
            print(f"    双高平均收益: {ie.both_high_avg:+.4%}")
            print(f"    A高B低:       {ie.a_high_b_low_avg:+.4%}")
            print(f"    A低B高:       {ie.a_low_b_high_avg:+.4%}")
            print(f"    双低平均收益: {ie.both_low_avg:+.4%}")
            print(f"    解读: {ie.interpretation}")
    else:
        print("  (无显著交互效应)")

    # 权重调整建议
    print(f"\n{'─'*70}")
    print(f"  💡 权重优化建议")
    print(f"{'─'*70}")
    for i, rec in enumerate(report.recommendations, 1):
        print(f"  {i}. {rec}")

    print(f"\n{'='*70}")
    print("  报告结束。Trinity OS v2.1 — 不只是交易, 更是认知。")
    print(f"{'='*70}")


# ================================================================
# 6. CLI 入口
# ================================================================

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Trinity OS v2.1 Alpha 归因报告生成器",
    )
    parser.add_argument(
        "--ledger", default="data/ledger.json",
        help="Ledger 文件路径 (默认 data/ledger.json)",
    )
    parser.add_argument("--symbol", default="DEFAULT", help="标的代码 (生成数据用)")
    parser.add_argument("--bars", type=int, default=200, help="K 线数 (生成数据用)")
    parser.add_argument("--seed", type=int, default=42, help="随机种子 (生成数据用)")
    parser.add_argument("--fixed-horizon", type=int, default=20, help="固定窗口周期")
    parser.add_argument(
        "--market-data", default=None,
        help="市场数据 JSON 文件 (不指定则用合成数据)",
    )
    parser.add_argument(
        "--replay", action="store_true",
        help="因果对齐模式: 在同序列上回放决策并清算收益 (铁律 #1 延迟双指针)",
    )
    parser.add_argument(
        "--save-ledger", default=None,
        help="回放模式下, 将(决策+收益)账本保存到指定路径 (不覆盖默认 ledger.json)",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式 (便于程序读取)")

    args = parser.parse_args(argv)

    # 检查 Ledger 文件
    if not os.path.exists(args.ledger):
        print(f"错误: Ledger 文件不存在: {args.ledger}", file=sys.stderr)
        return 1

    # 因果对齐模式 (铁律 #1): 同序列决策 + 清算
    if args.replay:
        ledger = EventSourcingTracker() if args.save_ledger else None
        try:
            report = generate_report_from_replay(
                symbol=args.symbol,
                bars=args.bars,
                seed=args.seed,
                fixed_horizon=args.fixed_horizon,
                ledger=ledger,
            )
        except Exception as e:
            print(f"错误: 回放报告生成失败: {e}", file=sys.stderr)
            return 1
        if args.save_ledger and ledger is not None:
            ledger.save(args.save_ledger)
            print(
                f"[replay] 账本已保存: {args.save_ledger} "
                f"(决策事件 {len(ledger)} / 收益事件 {len(ledger.outcomes())})"
            )
    else:
        # 加载市场数据
        market_data = None
        if args.market_data:
            if not os.path.exists(args.market_data):
                print(f"错误: 市场数据文件不存在: {args.market_data}", file=sys.stderr)
                return 1
            market_data = load_market_data(args.market_data)

        # 生成报告
        try:
            report = generate_report(
                ledger_filepath=args.ledger,
                market_data=market_data,
                symbol=args.symbol,
                bars=args.bars,
                seed=args.seed,
                fixed_horizon=args.fixed_horizon,
            )
        except Exception as e:
            print(f"错误: 报告生成失败: {e}", file=sys.stderr)
            return 1

    # 输出
    if args.json:
        output = {
            "symbol": args.symbol,
            "sample_size": report.sample_size,
            "avg_return": report.avg_return,
            "win_rate": report.win_rate,
            "top_factors": report.top_factors,
            "top_interactions": [
                {
                    "factor_a": ie.factor_a,
                    "factor_b": ie.factor_b,
                    "interaction": ie.interaction,
                    "interpretation": ie.interpretation,
                }
                for ie in report.top_interactions
            ],
            "recommendations": report.recommendations,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_report(report, symbol=args.symbol)

    return 0


if __name__ == "__main__":
    sys.exit(main())
