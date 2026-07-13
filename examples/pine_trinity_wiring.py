"""Pine 信号 × TrinityAdapter 端到端接线示例（最小可运行）。

演示如何把 TradingView 活图表上的前端 Pine 信号，经 CDP 桥解析后，
注入 Trinity OS v2.2.1 风险网关，得到带前端熔断的最终风险动作。

运行方式（需 TradingView Desktop 开启且 CDP 在 127.0.0.1:9222）：
    python examples/pine_trinity_wiring.py
关闭 TV 时示例会走 mock 兜底，仍能展示接线逻辑。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 让 examples/ 下脚本可独立运行（python examples/pine_trinity_wiring.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.tradingview import TradingViewAdapter
from core.adapters.pine_trinity_bridge import PineSignal, run_pine_trinity_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 一条“入场条件成立”的行情（spacetime>0, price>d3_low）→ 网关本会给初始入场
SAMPLE_BAR = {
    "close": 100.0,
    "d3_low": 95.0,
    "atr": 2.0,
    "spacetime_score": 0.9,
    "j1_confirmed": True,
    "open": 99.0,
    "vix": 22.0,
    "brent_shock": 0.0,
}


def main() -> None:
    adapter = TradingViewAdapter(mcp_command="node", timeout_seconds=40)
    try:
        decision = run_pine_trinity_loop(
            adapter,
            bar_data=SAMPLE_BAR,
            script_name="Global Sentinel",
            symbol="TVC:GOLD",
        )
        logger.info("Pine 信号: %s", decision.pine_signal.name)
        logger.info("网关基准动作: %s", decision.base_action.action_type.name)
        logger.info("前端是否否决: %s", decision.override)
        logger.info(
            "最终动作: %s (reason=%s, macro_shock=%.3f)",
            decision.final_action_type.name,
            decision.reason,
            decision.enriched_macro_shock,
        )
        if decision.pine_signal == PineSignal.RISK_OFF and decision.override:
            logger.warning("【前端熔断】Pine 判定风险规避，已否决网关入场 → HOLD")
    except Exception as exc:  # noqa: BLE001
        logger.critical("示例异常（已安全降级）: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()
