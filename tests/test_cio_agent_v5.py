from __future__ import annotations

from core.agents.cio_agent import CIOAgent, CioCopilot
from core.schemas import Decision, DecisionAction, FeatureSchema, KernelDecision, RegimeName, RegimeType


def test_generate_builds_v5_markdown_action_plan() -> None:
    agent = CIOAgent()
    report = agent.generate(
        regime_probs={
            RegimeName.AI_EXPANSION: 0.10,
            RegimeName.NARROW_LEADERSHIP: 0.55,
            RegimeName.FAST_LIQUIDITY_SHOCK: 0.20,
            RegimeName.CASH_LIQUIDATION: 0.15,
        },
        allocation={"QQQ": 0.6, "CASH": 0.4},
        diff_report={"QQQ": 0.05, "CASH": -0.05},
        shadow_report="shadow context",
        features_summary=FeatureSchema(vix=18.5, dxy=104.2, ovx=32.1),
    )

    assert report.startswith("# 🏛️ Macro OS v5.0 | 每日执行计划 (Daily Action Plan)")
    assert "当前主导环境: **NARROW_LEADERSHIP** (55.0%)" in report
    assert "- **QQQ**: 60.0%" in report
    assert "- **CASH**: 40.0%" in report
    assert "- 🟢 **买入 QQQ**: 增加 5.00% 目标仓位" in report
    assert "- 🔴 **卖出 CASH**: 削减 5.00% 目标仓位" in report
    assert "shadow context" in report


def test_generate_daily_plan_alias_remains_available() -> None:
    agent = CioCopilot()
    report = agent.generate_daily_plan(
        regime_probs={RegimeName.AI_EXPANSION: 1.0},
        allocation={"QQQ": 1.0},
        diff_report=None,
        shadow_report="shadow context",
        features_summary=FeatureSchema(),
    )

    assert "Macro OS v5.0" in report


def test_generate_includes_macro_narrative_when_enabled() -> None:
    agent = CIOAgent(llm_enabled=True)
    report = agent.generate(
        regime_probs={RegimeName.AI_EXPANSION: 1.0},
        allocation={"QQQ": 1.0},
        diff_report=None,
        shadow_report="shadow context",
        features_summary=FeatureSchema(vix=18.5),
        macro_narrative="Narrative slot is active",
    )

    assert "CIO 宏观叙事" in report
    assert "Narrative slot is active" in report


def test_generate_daily_plan_legacy_wrapper_handles_kernel_decision() -> None:
    agent = CioCopilot(llm_enabled=True)
    decision = KernelDecision(
        decision=Decision(action=DecisionAction.NEUTRAL, reason="kernel reason", regime=RegimeType.RISK_ON),
        hard_regime=RegimeType.RISK_ON.value,
        soft_regime_label=RegimeType.RISK_ON.value,
        risk_budget=0.6,
        defense_budget=0.4,
        reason_code="GLOBAL_RAMP_ACTIVE",
        veto_reason="legacy veto reason",
        regime_probs={RegimeName.NARROW_LEADERSHIP: 1.0},
    )

    report = agent.generate_daily_plan(decision, {"QQQ": 0.05, "CASH": -0.05})

    assert "CIO 宏观叙事" in report
    assert "kernel reason" in report
    assert "GLOBAL_RAMP_ACTIVE" in report
