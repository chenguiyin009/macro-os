"""Trinity OS v2.1 - 编排器

串联四层架构: Gateway → Trinity Kernel → Ledger → Output

负责:
  1. 通过 Gateway 获取多级别数据
  2. 对每个级别计算指标、状态、结构
  3. 计算时空充分性
  4. 通过决策路由器输出决策
  5. 记录到事件溯源账本
"""
from __future__ import annotations

from typing import Optional

from trinity.context import (
    Decision,
    JLevelContext,
    OHLCV,
    SpacetimeScore,
    TradingLevel,
)
from trinity.decision_router import DecisionRouter
from trinity.gateway import Gateway
from trinity.indicators import compute_indicators, last_valid
from trinity.ledger import EventSourcingTracker
from trinity.spacetime_engine import SpacetimeEngine
from trinity.state_machine import StateMachine
from trinity.structure_parser import StructureParser


class Orchestrator:
    """决策编排器

    用法:
        orch = Orchestrator(config)
        decisions = orch.run(symbol="000001", dry_run=True)
    """

    def __init__(self, config: Optional[dict] = None):
        """
        Args:
            config: 配置字典 (从 macro_config.yaml 加载)
        """
        self.config = config or {}
        gw_cfg = self.config.get("gateway", {})
        ind_cfg = self.config.get("indicators", {})
        st_cfg = self.config.get("spacetime", {})

        # 初始化各组件
        self.gateway = Gateway(
            source=gw_cfg.get("default_source", "synthetic"),
            cache_dir=gw_cfg.get("cache_dir", "data"),
        )
        self.state_machine = StateMachine()
        self.structure_parser = StructureParser(
            threshold=ind_cfg.get("zigzag", {}).get("threshold", 0.03),
        )
        self.spacetime_engine = SpacetimeEngine(
            time_tolerance=st_cfg.get("time_symmetry_tolerance_weeks", 3),
            time_windows=tuple(st_cfg.get("time_windows", [13, 18, 21, 34, 55])),
            zigzag_threshold=ind_cfg.get("zigzag", {}).get("threshold", 0.03),
        )
        self.decision_router = DecisionRouter()
        self.ledger = EventSourcingTracker()

        # 指标参数
        ma_periods = tuple(ind_cfg.get("ma", {}).get("periods", [55, 233]))
        macd_params = (
            ind_cfg.get("macd", {}).get("fast", 12),
            ind_cfg.get("macd", {}).get("slow", 26),
            ind_cfg.get("macd", {}).get("signal", 9),
        )
        boll_params = (
            ind_cfg.get("bollinger", {}).get("period", 20),
            ind_cfg.get("bollinger", {}).get("num_std", 2.0),
        )
        self._ind_params = (ma_periods, macd_params, boll_params)

    def run(
        self,
        symbol: str = "DEFAULT",
        bars: int = 100,
        dry_run: bool = True,
        seed: Optional[int] = None,
    ) -> list[Decision]:
        """执行完整决策流程

        Args:
            symbol:  标的代码
            bars:    K 线数量
            dry_run: 是否 dry-run 模式 (不执行真实交易)
            seed:    随机种子 (合成数据用)

        Returns:
            决策列表
        """
        # 1. 获取多级别数据
        multi_data = self.gateway.fetch_multi_level(symbol, bars, seed)

        # 2. 构建各级别上下文
        ctx_j2 = self._build_context(
            multi_data.get("J+2", []), TradingLevel.J_PLUS_2, symbol,
        )
        ctx_j = self._build_context(
            multi_data.get("J", []), TradingLevel.J, symbol,
        )
        ctx_j1 = self._build_context(
            multi_data.get("J-1", []), TradingLevel.J_MINUS_1, symbol,
        )
        ctx_j2m = self._build_context(
            multi_data.get("J-2", []), TradingLevel.J_MINUS_2, symbol,
        )

        # 3. 计算时空充分性 (J+2 周线 + J 日线)
        spacetime = self.spacetime_engine.evaluate(
            multi_data.get("J+2", []),
            multi_data.get("J", []),
        )

        # 4. 决策路由
        decision = self.decision_router.route(
            ctx_j2, ctx_j, ctx_j1, ctx_j2m, spacetime,
        )

        # 5. 记录到账本
        contexts = {
            TradingLevel.J_PLUS_2: ctx_j2,
            TradingLevel.J: ctx_j,
            TradingLevel.J_MINUS_1: ctx_j1,
            TradingLevel.J_MINUS_2: ctx_j2m,
        }
        metadata = {"dry_run": dry_run, "symbol": symbol, "bars": bars}
        self.ledger.record(contexts, decision, metadata=metadata)

        return [decision]

    def run_batch(
        self,
        symbols: list[str],
        bars: int = 100,
        dry_run: bool = True,
    ) -> dict[str, list[Decision]]:
        """批量执行"""
        results: dict[str, list[Decision]] = {}
        for symbol in symbols:
            results[symbol] = self.run(symbol, bars, dry_run)
        return results

    def _build_context(
        self,
        ohlcv: list[OHLCV],
        level: TradingLevel,
        symbol: str,
    ) -> JLevelContext:
        """从 OHLCV 构建单级别上下文"""
        if not ohlcv:
            return JLevelContext(level=level, symbol=symbol)

        # 计算指标
        ma_periods, macd_params, boll_params = self._ind_params
        ind = compute_indicators(ohlcv, ma_periods, macd_params, boll_params)

        # 状态判定
        dif = ind.get("dif", [])
        dea = ind.get("dea", [])
        state, state_evidence = self.state_machine.current_state_with_evidence(dif, dea)

        # 结构判定
        structure = self.structure_parser.parse(ohlcv)

        # 最新价格与指标值
        last_bar = ohlcv[-1]
        ma55 = last_valid(ind.get("ma55", [])) or 0.0
        ma233 = last_valid(ind.get("ma233", [])) or 0.0
        boll_upper = last_valid(ind.get("boll_upper", [])) or 0.0
        boll_mid = last_valid(ind.get("boll_mid", [])) or 0.0
        boll_lower = last_valid(ind.get("boll_lower", [])) or 0.0
        dif_val = last_valid(dif) or 0.0
        dea_val = last_valid(dea) or 0.0
        hist_val = last_valid(ind.get("macd_hist", [])) or 0.0

        return JLevelContext(
            level=level,
            symbol=symbol,
            state=state,
            state_evidence=state_evidence,
            structure=structure,
            ma55=ma55,
            ma233=ma233,
            price=last_bar.close,
            boll_upper=boll_upper,
            boll_mid=boll_mid,
            boll_lower=boll_lower,
            dif=dif_val,
            dea=dea_val,
            macd_hist=hist_val,
        )

    def get_ledger(self) -> EventSourcingTracker:
        """获取账本实例"""
        return self.ledger

    def save_ledger(self, filepath: str) -> None:
        """保存账本"""
        self.ledger.save(filepath)
