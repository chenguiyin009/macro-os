"""Pine 信号 × TrinityAdapter 端到端接线层 (Pine-Trinity Bridge)。

WHY THIS FILE EXISTS
--------------------
`relay/pine-bridge.mjs` 经 CDP 从 TradingView 活图表上解析出一个前端信号
`PineConclusionSchema`（signal / confidence / value / label / payload）。这是一层
**独立的、先于价格结构判断的宏观/情绪确认信号**。

Trinity OS v2.2.1 的 `RiskGateway` 是**价格结构驱动**的（D3 低点 / ATR / spacetime /
J-1 回抽），它并不直接消费前端信号。本模块把这两层缝起来，形成闭环：

    TradingView Pine 结论
        │  translate_pine_signal  →  标准化语义 PineSignal(RISK_ON/OFF/NEUTRAL)
        │  pine_to_macro_shock    →  派生 macro_commodity_shock 注入 MarketState
        ▼
    TrinityAdapter.to_market_state  →  RiskGateway.process_tick  →  RiskAction (base)
        │  combine                →  前端信号对网关决策做确认/否决叠加
        ▼
    PineTrinityDecision (final_action_type，含 Fail-Safe)

设计要点（与 Trinity v2.2.1 风格一致）：
  * 双层接入：① 宏观注入让网关自带 MacroCircuitBreaker 原生反应（抑制金字塔加仓）；
    ② 高层 overlay 在**高置信**时直接否决入场/加仓（前端熔断），绝不臆造入场。
  * Fail-Safe 贯穿：桥失败 / 信号缺失 / 任何异常 → 安全降级为 HOLD，主流程不崩。
  * 置信度门控：confidence 低于阈值或信号 UNKNOWN → 不 override，完全信任网关。
  * 纯增量、零耦合：仅依赖 core.adapters.risk_action / core.adapters.trinity_adapter
    / core.schemas，不改动既有模块。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional

import logging

from core.adapters.risk_action import RiskAction, RiskActionType
from core.adapters.trinity_adapter import TrinityAdapter
from core.schemas import PineConclusionSchema

logger = logging.getLogger(__name__)

# RISK_OFF 时注入的宏观冲击幅度——恰好达到 MacroCircuitBreaker 的
# commodity_shock_threshold=0.15，使网关自动禁止金字塔加仓。
PINE_SHOCK_MAGNITUDE = 0.15
# 默认置信度门控阈值：低于此值的前端信号不参与决策覆盖。
DEFAULT_CONFIDENCE_THRESHOLD = 0.6


class PineSignal(Enum):
    """Pine 前端信号的标准化语义层（与具体 Pine 脚本解耦）。"""
    RISK_ON = auto()    # 偏多 / 风险偏好（放行网关决策）
    RISK_OFF = auto()   # 偏空 / 风险规避（应抑制新风险敞口）
    NEUTRAL = auto()    # 中性 / 无明确倾向
    UNKNOWN = auto()    # 信号缺失或不可解析


# 语义关键词（大小写不敏感，匹配 signal / label 字段）
_RISK_OFF_KEYWORDS = {
    "risk_off", "riskoff", "off", "sell", "bearish", "alert",
    "warning", "danger", "avoid", "reduce",
}
_RISK_ON_KEYWORDS = {
    "risk_on", "riskon", "on", "buy", "bullish", "safe", "calm", "add",
}


def translate_pine_signal(
    conclusion: Optional[PineConclusionSchema],
    value_high_is_risk_off: bool = True,
    risk_score_threshold: float = 1.0,
) -> PineSignal:
    """把自由格式的 Pine 结论翻译为标准化 PineSignal。

    解析优先级：
      1. 显式语义关键词（signal / label 命中 RISK_ON / RISK_OFF 词表）→ 直接定类；
      2. 否则若 value 为数值，视为“风险分”：
           - value_high_is_risk_off=True 时，value > risk_score_threshold → RISK_OFF，
             value <= 0 → RISK_ON，其余 → NEUTRAL；
      3. 都无法解析 → UNKNOWN。

    `risk_score_threshold` 用于把具体 Pine 脚本的“风险分”映射到语义层；调用方可按
    脚本实际刻度覆盖。本函数**不消费 confidence**（门控在 combine 中处理）。
    """
    if conclusion is None:
        return PineSignal.UNKNOWN

    raw = " ".join(
        str(x or "") for x in (conclusion.signal, conclusion.label)
    ).strip().lower()
    if raw:
        tokens = set(raw.split())
        if tokens & _RISK_OFF_KEYWORDS:
            return PineSignal.RISK_OFF
        if tokens & _RISK_ON_KEYWORDS:
            return PineSignal.RISK_ON

    value = conclusion.value
    if isinstance(value, (int, float)):
        if value_high_is_risk_off:
            if value > risk_score_threshold:
                return PineSignal.RISK_OFF
            if value <= 0:
                return PineSignal.RISK_ON
        else:
            if value > risk_score_threshold:
                return PineSignal.RISK_ON
            if value <= 0:
                return PineSignal.RISK_OFF
        return PineSignal.NEUTRAL

    return PineSignal.UNKNOWN


def pine_to_macro_shock(
    signal: PineSignal,
    magnitude: float = PINE_SHOCK_MAGNITUDE,
) -> float:
    """把 PineSignal 翻译成网关可消费的 macro_commodity_shock 幅度。

    RISK_OFF → +magnitude（触发 MacroCircuitBreaker，抑制金字塔加仓）；
    RISK_ON  → 0.0（不人为制造冲击）；
    NEUTRAL / UNKNOWN → 0.0（不干预网关原生逻辑）。
    """
    if signal == PineSignal.RISK_OFF:
        return float(magnitude)
    return 0.0


def enrich_bar_with_pine(
    bar_data: Dict[str, Any],
    conclusion: Optional[PineConclusionSchema],
    magnitude: float = PINE_SHOCK_MAGNITUDE,
) -> Dict[str, Any]:
    """把 Pine 派生的宏观冲击注入行情字典，使 `should_execute_risk_action` /
    `to_market_state` 能透明接管。

    注入字段：
      * brent_shock：在原有基础上叠加 Pine 冲击（RISK_OFF 时 = +magnitude）；
      * _pine_signal / _pine_confidence：透传供上层审计/调试。
    """
    enriched = dict(bar_data)
    signal = translate_pine_signal(conclusion)
    base_shock = float(enriched.get("brent_shock", 0.0) or 0.0)
    enriched["brent_shock"] = base_shock + pine_to_macro_shock(signal, magnitude)
    enriched["_pine_signal"] = signal.name
    enriched["_pine_confidence"] = (
        conclusion.confidence if conclusion is not None else None
    )
    return enriched


def _bar_to_market_state_kwargs(bar: Dict[str, Any]) -> Dict[str, Any]:
    """把行情字典映射为 TrinityAdapter.to_market_state 的关键字参数。"""
    close = float(bar["close"])
    return dict(
        current_price=close,
        d3_low=float(bar.get("d3_low", close * 0.95)),
        atr=float(bar.get("atr", 2.0)),
        spacetime_score=float(bar.get("spacetime_score", 0.85)),
        j1_confirmed=bool(bar.get("j1_confirmed", True)),
        open=bar.get("open"),
        macro_vix=float(bar.get("vix", 22.0)),
        macro_commodity_shock=float(bar.get("brent_shock", 0.0)),
    )


@dataclass
class PineTrinityDecision:
    """前端信号 × 风控网关 的最终结算结果（强类型、可归因）。"""
    base_action: RiskAction                       # 网关原始决策
    pine_signal: PineSignal                       # 标准化前端信号
    pine_confidence: float                       # 原始置信度
    confidence_trusted: bool                     # 是否达到门控阈值
    override: bool                               # 前端信号是否否决了网关
    final_action_type: RiskActionType            # 最终动作
    enriched_macro_shock: float                  # 注入的宏观冲击幅度
    reason: str = ""                             # 决策理由（归因/审计）


def combine(
    base_action: RiskAction,
    pine_signal: PineSignal,
    confidence: Optional[float] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    enriched_macro_shock: float = 0.0,
) -> PineTrinityDecision:
    """以 Fail-Safe 方式把前端信号叠加到网关决策上。

    规则（保守、可辩护）：
      * 信号不可信（UNKNOWN / confidence 低于阈值）→ 沿用网关，不 override；
      * RISK_OFF + 高置信 且 网关动作为 入场/加仓 → 否决为 HOLD（前端熔断）；
      * RISK_OFF + 高置信 且 网关已非入场 → 透传（仅确认，不改变动作）；
      * RISK_ON  + 高置信 → 仅“放行”网关动作，**绝不臆造入场**；
      * TRAILING_EXIT / FATAL_GAP_LIQUIDATION 透传不变（风险已由网关覆盖）。
    """
    trusted = (
        pine_signal != PineSignal.UNKNOWN
        and confidence is not None
        and confidence >= confidence_threshold
    )

    if not trusted:
        return PineTrinityDecision(
            base_action=base_action,
            pine_signal=pine_signal,
            pine_confidence=confidence or 0.0,
            confidence_trusted=False,
            override=False,
            final_action_type=base_action.action_type,
            enriched_macro_shock=enriched_macro_shock,
            reason="pine_untrusted",
        )

    if pine_signal == PineSignal.RISK_OFF:
        if base_action.action_type in (RiskActionType.INITIAL_ENTRY, RiskActionType.PYRAMID_ADD):
            return PineTrinityDecision(
                base_action=base_action,
                pine_signal=pine_signal,
                pine_confidence=confidence or 0.0,
                confidence_trusted=True,
                override=True,
                final_action_type=RiskActionType.HOLD,
                enriched_macro_shock=enriched_macro_shock,
                reason="pine_risk_off_override",
            )
        return PineTrinityDecision(
            base_action=base_action,
            pine_signal=pine_signal,
            pine_confidence=confidence or 0.0,
            confidence_trusted=True,
            override=False,
            final_action_type=base_action.action_type,
            enriched_macro_shock=enriched_macro_shock,
            reason="pine_risk_off_confirmed",
        )

    if pine_signal == PineSignal.RISK_ON:
        return PineTrinityDecision(
            base_action=base_action,
            pine_signal=pine_signal,
            pine_confidence=confidence or 0.0,
            confidence_trusted=True,
            override=False,
            final_action_type=base_action.action_type,
            enriched_macro_shock=enriched_macro_shock,
            reason="pine_risk_on_permissive",
        )

    # NEUTRAL（高置信但中性）→ 不改变网关决策
    return PineTrinityDecision(
        base_action=base_action,
        pine_signal=pine_signal,
        pine_confidence=confidence or 0.0,
        confidence_trusted=True,
        override=False,
        final_action_type=base_action.action_type,
        enriched_macro_shock=enriched_macro_shock,
        reason="pine_neutral",
    )


def run_pine_trinity_loop(
    adapter: Any,  # TradingViewAdapter（仅需 fetch_pine_conclusions 方法）
    bar_data: Dict[str, Any],
    script_name: Optional[str] = None,
    symbol: Optional[str] = None,
    cdp_url: str = "http://127.0.0.1:9222",
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    account_balance: float = 1_000_000.0,
    context: Optional[Dict[str, Any]] = None,
    trinity_adapter: Optional[TrinityAdapter] = None,
    magnitude: float = PINE_SHOCK_MAGNITUDE,
) -> PineTrinityDecision:
    """端到端编排：TradingView 前端信号 → Trinity 风控网关 → 最终风险动作。

    数据流：
      1. 拉取 Pine 结论（adapter.fetch_pine_conclusions，自带 mock 兜底）；
      2. translate_pine_signal → PineSignal；
      3. enrich_bar_with_pine → 注入宏观冲击；
      4. TrinityAdapter.to_market_state + get_risk_action → 网关 RiskAction；
      5. combine → 前端信号叠加结算。
    任何异常都会被捕获并降级为安全 HOLD（Fail-Safe），绝不抛出。
    """
    try:
        conclusion = adapter.fetch_pine_conclusions(
            symbol=symbol, script_name=script_name, cdp_url=cdp_url
        )
        signal = translate_pine_signal(conclusion)
        confidence = conclusion.confidence if conclusion is not None else None

        enriched = enrich_bar_with_pine(bar_data, conclusion, magnitude=magnitude)

        ta = trinity_adapter or TrinityAdapter()
        state = ta.to_market_state(**_bar_to_market_state_kwargs(enriched))
        base_action = ta.get_risk_action(
            state, account_balance=account_balance, context=context
        )

        return combine(
            base_action=base_action,
            pine_signal=signal,
            confidence=confidence,
            confidence_threshold=confidence_threshold,
            enriched_macro_shock=enriched.get("brent_shock", 0.0),
        )
    except Exception as exc:  # noqa: BLE001 — Fail-Safe 顶层防护
        logger.error("Pine-Trinity 端到端异常，安全降级 HOLD: %s", exc, exc_info=True)
        return PineTrinityDecision(
            base_action=RiskAction(
                action_type=RiskActionType.HOLD,
                reason=f"pine_trinity_exception: {exc!s}",
                metadata={"exception": str(exc)},
            ),
            pine_signal=PineSignal.UNKNOWN,
            pine_confidence=0.0,
            confidence_trusted=False,
            override=False,
            final_action_type=RiskActionType.HOLD,
            enriched_macro_shock=0.0,
            reason="pine_trinity_exception",
        )
