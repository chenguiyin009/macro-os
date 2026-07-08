"""Macro OS v5.0 - CIO Agent (Daily Report Generator).

Synthesizes L3 decisions, L4 reconciliation diffs, and optional narrative context
into a comprehensive Markdown action plan.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.schemas import FeatureSchema, RegimeName

logger = logging.getLogger(__name__)


class CIOAgent:
    """Generates the daily investment committee action plan."""

    def __init__(self, llm_enabled: bool = False) -> None:
        self.llm_enabled = llm_enabled

    def generate(
        self,
        regime_probs: Dict[RegimeName, float],
        allocation: Dict[str, float],
        diff_report: Optional[Dict[str, float]],
        shadow_report: str,
        features_summary: FeatureSchema,
        macro_narrative: str = "",
    ) -> str:
        """Build the final Markdown report for Feishu/Telegram notifications."""
        if regime_probs:
            dominant_regime = max(regime_probs, key=regime_probs.get)
            prob_val = regime_probs[dominant_regime]
        else:
            dominant_regime = RegimeName.AI_EXPANSION
            prob_val = 0.0

        vix_str = f"{features_summary.vix:.2f}" if features_summary.vix is not None else "N/A"
        dxy_str = f"{features_summary.dxy:.2f}" if features_summary.dxy is not None else "N/A"
        ovx_val = getattr(features_summary, "ovx", None)
        ovx_str = f"{ovx_val:.2f}" if ovx_val is not None else "N/A"

        action_plan = "> ✅ **无需调仓** (目标偏离度处于噪音过滤死区，规避摩擦成本)"
        if diff_report:
            trades = []
            for sym, val in sorted(diff_report.items(), key=lambda item: abs(item[1]), reverse=True):
                if val >= 0.001:
                    trades.append(f"- 🟢 **买入 {sym}**: 增加 {abs(val):.2%} 目标仓位")
                elif val <= -0.001:
                    trades.append(f"- 🔴 **卖出 {sym}**: 削减 {abs(val):.2%} 目标仓位")

            if trades:
                action_plan = "\n".join(trades)

        sorted_allocation = sorted(
            ((k, v) for k, v in allocation.items() if abs(v) >= 0.001),
            key=lambda item: item[1],
            reverse=True,
        )
        allocation_str = "\n".join([f"- **{k}**: {v:.1%}" for k, v in sorted_allocation]) or "- 无显著仓位"

        narrative_block = ""
        if self.llm_enabled and macro_narrative.strip():
            narrative_block = f"\n## 🧠 CIO 宏观叙事\n> {macro_narrative.strip()}\n"

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        report = f"""# 🏛️ Macro OS v5.0 | 每日执行计划 (Daily Action Plan)
*Generated at: {date_str}*{narrative_block}

## 📊 宏观概率矩阵 (Probabilistic Matrix)
当前主导环境: **{dominant_regime.value}** ({prob_val:.1%})
* **VIX** (恐慌): {vix_str} | **DXY** (美元): {dxy_str} | **OVX** (原油波动): {ovx_str}

| 宏观状态 | 概率判定 |
|:---|---:|
| AI_EXPANSION | {regime_probs.get(RegimeName.AI_EXPANSION, 0):.1%} |
| NARROW_LEADERSHIP | {regime_probs.get(RegimeName.NARROW_LEADERSHIP, 0):.1%} |
| FAST_LIQUIDITY_SHOCK | {regime_probs.get(RegimeName.FAST_LIQUIDITY_SHOCK, 0):.1%} |
| CASH_LIQUIDATION | {regime_probs.get(RegimeName.CASH_LIQUIDATION, 0):.1%} |

## 🎯 宪法与微观目标配置 (Target Allocation)
{allocation_str}

## ⚡ 交易指令 (Execution Deltas)
{action_plan}

---
{shadow_report}
"""
        logger.info("CIO Agent: Institutional Markdown report generated successfully.")
        return report

    def generate_daily_plan(self, *args: Any, **kwargs: Any) -> str:
        """Backward-compatible wrapper for legacy and v5 report call sites."""
        if kwargs and "regime_probs" in kwargs:
            return self.generate(
                kwargs["regime_probs"],
                kwargs["allocation"],
                kwargs.get("diff_report"),
                kwargs.get("shadow_report", ""),
                kwargs.get("features_summary") or FeatureSchema(),
                macro_narrative=kwargs.get("macro_narrative", ""),
            )

        if len(args) == 2 and not kwargs:
            approved_decision, diff_report = args
            regime_probs = getattr(approved_decision, "regime_probs", {}) or {}
            allocation = {
                "QQQ": getattr(approved_decision, "risk_budget", 0.0),
                "CASH": getattr(approved_decision, "defense_budget", 0.0),
            }

            macro_narrative = ""
            if self.llm_enabled:
                parts = []
                decision_reason = getattr(getattr(approved_decision, "decision", None), "reason", "")
                if decision_reason:
                    parts.append(decision_reason)
                veto_reason = getattr(approved_decision, "veto_reason", "")
                if veto_reason:
                    parts.append(veto_reason)
                reason_code = getattr(approved_decision, "reason_code", "")
                if reason_code:
                    parts.append(reason_code)
                macro_narrative = " | ".join(parts)

            return self.generate(
                regime_probs,
                allocation,
                diff_report,
                "",
                FeatureSchema(),
                macro_narrative=macro_narrative,
            )

        if len(args) >= 5:
            return self.generate(*args, **kwargs)

        raise TypeError("generate_daily_plan expects either 2 legacy args or the full v5 report payload")


CioCopilot = CIOAgent
