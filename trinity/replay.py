"""Trinity OS v2.1 - TemporalBuffer (蓝图阶段二)

防止回测中的未来函数污染 (look-ahead bias)。

核心原理: 在回测时, 任何时间点 T 的分析只能看到 T 及之前的数据。
TemporalBuffer 包装完整数据序列, 通过移动指针逐步释放数据,
确保引擎在回测模式下不会"偷看"未来。
"""
from __future__ import annotations

from typing import Generic, TypeVar, List, Optional, Iterator

from trinity.ledger import OutcomeEvent

T = TypeVar("T")


class TemporalBuffer(Generic[T]):
    """时间序列缓冲区 - 防未来函数

    用法:
        buffer = TemporalBuffer(ohlcv_data)
        # 回测: 逐步推进
        while buffer.advance():
            visible = buffer.visible()  # 只能看到当前及之前的数据
            decision = engine.analyze(visible)
    """

    def __init__(self, data: List[T]):
        """
        Args:
            data: 完整的时间序列数据 (按时间正序)
        """
        self._data: List[T] = list(data)
        self._cursor: int = 0  # 当前时间指针 (0 = 只看到第 0 个元素)

    def advance(self) -> bool:
        """推进时间指针一格

        Returns:
            True 如果还有更多数据, False 如果已到末尾
        """
        if self._cursor < len(self._data) - 1:
            self._cursor += 1
            return True
        return False

    def visible(self) -> List[T]:
        """获取当前时间点可见的数据 (从开始到 cursor)"""
        return self._data[: self._cursor + 1]

    def current(self) -> Optional[T]:
        """获取当前时间点的数据"""
        if 0 <= self._cursor < len(self._data):
            return self._data[self._cursor]
        return None

    def peek(self, offset: int = 0) -> Optional[T]:
        """获取当前时间点 ± offset 的数据

        offset > 0: 未来数据 (回测中应避免使用!)
        offset < 0: 过去数据
        offset = 0: 当前数据
        """
        idx = self._cursor + offset
        if 0 <= idx < len(self._data):
            return self._data[idx]
        return None

    def reset(self) -> None:
        """重置指针到起点"""
        self._cursor = 0

    def seek(self, index: int) -> None:
        """跳转到指定索引"""
        self._cursor = max(0, min(index, len(self._data) - 1))

    @property
    def cursor(self) -> int:
        """当前指针位置"""
        return self._cursor

    @property
    def total_length(self) -> int:
        """数据总长度"""
        return len(self._data)

    @property
    def visible_length(self) -> int:
        """当前可见数据长度"""
        return self._cursor + 1

    @property
    def has_more(self) -> bool:
        """是否还有更多数据"""
        return self._cursor < len(self._data) - 1

    @property
    def data(self) -> List[T]:
        """完整市场数据序列的只读引用 (铁律 #1: 决策与清算同一序列)

        清算端 (OutcomeSimulator) 必须引用此同一序列, 以 entry_index 锚定切片,
        确保收益来自"决策当时"所对应的真实市场后果, 而非独立生成的序列。
        """
        return self._data

    def __len__(self) -> int:
        return self.visible_length

    def __iter__(self) -> Iterator[T]:
        """迭代可见数据"""
        return iter(self.visible())

    def __getitem__(self, index: int) -> T:
        """索引访问 (只允许访问可见范围)"""
        if index < 0:
            index = self.visible_length + index
        if 0 <= index < self.visible_length:
            return self._data[index]
        raise IndexError(
            f"索引 {index} 超出可见范围 [0, {self.visible_length}), "
            f"总长度 {self.total_length} (防未来函数保护)"
        )


class ReplayEngine:
    """回测引擎 (蓝图阶段二: Replay Engine v0.1)

    串联 TemporalBuffer + TrinityEngine, 逐步回放历史数据并收集决策。

    用法:
        replay = ReplayEngine(engine, ohlcv_data)
        results = replay.run()
    """

    def __init__(self, engine, data: List, symbol: str = "REPLAY"):
        """
        Args:
            engine: TrinityEngine 实例
            data: 完整 OHLCV 数据序列
            symbol: 回测标的
        """
        self.engine = engine
        self.buffer = TemporalBuffer(data)
        self.symbol = symbol
        self.results: list[dict] = []

    def run(self, min_bars: int = 30) -> List[dict]:
        """执行回测

        Args:
            min_bars: 最小可见 K 线数 (不足时跳过, 等待数据积累)

        Returns:
            决策结果列表
        """
        self.results = []
        self.buffer.reset()

        while True:
            if self.buffer.visible_length >= min_bars:
                result = self._step()
                if result:
                    self.results.append(result)

            if not self.buffer.advance():
                break

        return self.results

    def _step(self) -> Optional[dict]:
        """执行单步回测 (子类可覆盖以自定义逻辑)"""
        # 基础版: 返回当前状态信息
        current = self.buffer.current()
        if current is None:
            return None
        return {
            "cursor": self.buffer.cursor,
            "visible_length": self.buffer.visible_length,
            "symbol": self.symbol,
        }

    def summary(self) -> dict:
        """回测摘要"""
        return {
            "symbol": self.symbol,
            "total_bars": self.buffer.total_length,
            "total_steps": len(self.results),
            "has_more": self.buffer.has_more,
        }

    def run_with_outcomes(
        self,
        decide_fn,
        simulate_fn=None,
        fixed_horizon: int = 20,
        min_bars: int = 30,
        ledger=None,
    ) -> List[dict]:
        """因果对齐回放 — 铁律 #1 (延迟双指针) 的闭环实现

        同一市场序列上:
          - 决策端: 仅可见 visible() (实时窗口, 不含未来)
          - 清算端: 以 entry_index = cursor 锚定切片同一序列
                     market_data[entry_index:] 计算收益

        这是蓝图要求的"同序列收益接线": OutcomeSimulator 的输入数据源
        被强制约束为本 ReplayEngine 正在运行的原始 market_data 引用。

        Args:
            decide_fn:   callable(visible, entry_index)
                        -> {"factors": List[EvidenceFactor],
                            "decision": Decision,
                            "contexts": dict[TradingLevel, JLevelContext]}
            simulate_fn: callable(entry_index, market_data) -> OutcomeResult
                        (默认 OutcomeSimulator().simulate)
            fixed_horizon: 固定窗口周期 (K 线数)
            min_bars:    最小可见 K 线数 (数据积累阈值)
            ledger:      可选 EventSourcingTracker, 记录决策事件并绑定收益事件

        Returns:
            records: list[dict] = [
                {"event_id", "entry_index", "decision", "factors", "outcome"}, ...
            ]
        """
        from trinity.outcome import OutcomeSimulator

        if simulate_fn is None:
            simulate_fn = OutcomeSimulator().simulate

        self.results = []
        self.buffer.reset()
        records: list[dict] = []

        while True:
            if self.buffer.visible_length >= min_bars:
                visible = self.buffer.visible()
                entry_index = self.buffer.cursor

                # 决策端: 仅基于实时可见窗口 (防未来函数)
                result = decide_fn(visible, entry_index)
                factors = result.get("factors", [])
                decision = result.get("decision")
                contexts = result.get("contexts", {})

                # 记录决策事件 (entry_index 锚点写入 metadata)
                if ledger is not None:
                    event = ledger.record(
                        contexts, decision,
                        metadata={
                            "entry_index": entry_index,
                            "symbol": self.symbol,
                            "mode": "replay",
                        },
                    )
                    event_id = event.event_id
                else:
                    event_id = f"REPLAY-{entry_index:06d}"

                # 清算端: 同一序列, 从 entry_index 切片 (铁律 #1)
                outcome = simulate_fn(entry_index, self.buffer.data)

                if ledger is not None:
                    ledger.bind_outcome(
                        event_id,
                        OutcomeEvent(
                            linked_event_id=event_id,
                            entry_index=entry_index,
                            symbol=self.symbol,
                            result=outcome.to_dict(),
                        ),
                    )

                records.append({
                    "event_id": event_id,
                    "entry_index": entry_index,
                    "decision": decision,
                    "factors": factors,
                    "outcome": outcome,
                })
                self.results.append({"cursor": entry_index, "event_id": event_id})

            if not self.buffer.advance():
                break

        return records
