"""Trinity OS v2.1 - 数据接入层 (Gateway)

统一封装数据源, 转化为 List[OHLCV] 标准契约。
支持: synthetic (合成数据, 用于 dry-run) | tv_mcp | futu | tdx
"""
from __future__ import annotations

import math
import random
from typing import Optional

from trinity.context import OHLCV


class Gateway:
    """数据接入层

    用法:
        gw = Gateway(source="synthetic")
        ohlcv = gw.fetch(symbol="TEST", bars=100)
    """

    def __init__(self, source: str = "synthetic", cache_dir: str = "data"):
        """
        Args:
            source:    数据源 (synthetic | tv_mcp | futu | tdx)
            cache_dir: 缓存目录
        """
        self.source = source
        self.cache_dir = cache_dir

    def fetch(
        self,
        symbol: str = "DEFAULT",
        bars: int = 100,
        timeframe: str = "1D",
        seed: Optional[int] = None,
    ) -> list[OHLCV]:
        """获取 OHLCV 数据

        Args:
            symbol:    标的代码
            bars:      K 线数量
            timeframe: 时间周期 (1W/1D/60/15)
            seed:      随机种子 (合成数据用, 保证可复现)
        """
        if self.source == "synthetic":
            return self._fetch_synthetic(symbol, bars, timeframe, seed)
        elif self.source in ("tv_mcp", "futu", "tdx"):
            # 真实数据源: 预留接口, 当前降级为合成数据
            # 实际部署时对接 TV MCP / Futu API / 通达信
            return self._fetch_synthetic(symbol, bars, timeframe, seed)
        else:
            raise ValueError(f"未知数据源: {self.source}")

    def _fetch_synthetic(
        self,
        symbol: str,
        bars: int,
        timeframe: str,
        seed: Optional[int],
    ) -> list[OHLCV]:
        """合成数据生成器

        生成带有趋势 + 波动 + 周期特征的模拟 K 线,
        用于 dry-run 和测试。通过 seed 保证可复现。
        """
        rng = random.Random(seed if seed is not None else hash(symbol) % 2**32)
        result: list[OHLCV] = []

        # 基础参数
        base_price = 50.0 + rng.uniform(0, 50)
        # 趋势: 随机选择上涨/下跌/震荡
        trend_type = rng.choice(["bull", "bear", "range"])
        trend_strength = rng.uniform(0.1, 0.5)
        # 周期: 模拟波浪
        cycle_length = rng.randint(15, 30)
        cycle_amplitude = rng.uniform(2, 8)
        # 波动率
        volatility = rng.uniform(0.5, 2.0)

        for i in range(bars):
            # 趋势分量
            if trend_type == "bull":
                trend = i * trend_strength
            elif trend_type == "bear":
                trend = -i * trend_strength
            else:
                trend = 0.0

            # 周期分量 (正弦波)
            cycle = math.sin(2 * math.pi * i / cycle_length) * cycle_amplitude

            # 随机噪声
            noise = rng.gauss(0, volatility)

            close = base_price + trend + cycle + noise
            # 确保 close > 0
            close = max(close, 1.0)

            high = close + abs(rng.gauss(0, volatility * 0.5))
            low = close - abs(rng.gauss(0, volatility * 0.5))
            open_price = close + rng.gauss(0, volatility * 0.3)
            volume = rng.uniform(500, 5000)

            result.append(OHLCV(
                timestamp=float(i),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
            ))

        return result

    def fetch_multi_level(
        self,
        symbol: str = "DEFAULT",
        bars: int = 100,
        seed: Optional[int] = None,
    ) -> dict[str, list[OHLCV]]:
        """获取多级别数据 (J+2/J/J-1/J-2)

        合成数据模式下, 用不同参数模拟各级别。
        真实模式下, 应从不同时间周期获取。
        """
        # 各级别使用不同 seed 派生, 保证可复现且各级别独立
        base_seed = seed if seed is not None else hash(symbol) % 2**32
        return {
            "J+2": self._fetch_synthetic(symbol, bars, "1W", base_seed + 1),
            "J":   self._fetch_synthetic(symbol, bars, "1D", base_seed + 2),
            "J-1": self._fetch_synthetic(symbol, bars * 4, "60", base_seed + 3),
            "J-2": self._fetch_synthetic(symbol, bars * 16, "15", base_seed + 4),
        }
