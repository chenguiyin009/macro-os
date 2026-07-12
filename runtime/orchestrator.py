"""Macro OS - Pipeline orchestrator (v5.0 Research Edition).

Coordinates the macro decision pipeline without containing business logic.
Pipeline v5.0:
  [Data] TV MCP + Futu Sensor -> [L1] Regime -> [L2] Divergence -> [L3] Constitution Kernel
  -> [L4] Reconciliation -> [Agent] CIO Copilot -> [Store] Vault -> [Notify] Feishu
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

from adapters.feishu import FeishuAdapter
from adapters.futu import FutuSensor
from adapters.tradingview import TradingViewAdapter
from adapters.vault import VaultAdapter
from core.agents.cio_agent import CioCopilot
from core.decision_kernel import decide as kernel_decide
from core.divergence.divergence_engine import DivergencePhaseEngine
from core.divergence.divergence_phase import map_phase
from core.features import build_features
from core.macro.macro_mapper import compute_macro_state
from core.portfolio.reconciliation import compute_actionable_diff
from core.schemas import DataSource, Event, FeatureSchema, KernelDecision
from core.sector.sector_allocator import SectorAllocator
from core.shadow_engine import ShadowEngine

# Phase 3: 风控集成层（可通过 ENABLE_RISK_GATEWAY 配置开关控制）
from core.integration.risk_integration import (
    initialize_risk_integration,
    should_execute_risk_action,
)
from core.adapters.risk_action import RiskAction, RiskActionType

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates the Macro OS v5.0 Research pipeline."""

    def __init__(
        self,
        tradingview: TradingViewAdapter,
        vault: VaultAdapter,
        feishu: FeishuAdapter,
        futu: Optional[FutuSensor] = None,
        futu_sensor: Optional[FutuSensor] = None,
        cio_agent: Optional[CioCopilot] = None,
        sector_allocator: Optional[SectorAllocator] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.tv = tradingview
        self.vault = vault
        self.feishu = feishu
        self.futu = futu or futu_sensor or FutuSensor()
        self.futu_sensor = self.futu
        self.cio_agent = cio_agent or CioCopilot()
        self.config = config or {}
        self.enable_risk_gateway = bool(
            self.config.get("ENABLE_RISK_GATEWAY", True)
        )

        if self.enable_risk_gateway:
            initialize_risk_integration()
            logger.info("[Orchestrator] 风控网关已启用 (v2.2.1 Phase 3)")

        self.state = {
            "previous_risk_budget": 0.50,
            "days_in_recovery": 0,
        }
        self.sector_allocator = sector_allocator or SectorAllocator()
        self.sector_allocator.load_state()
        self.shadow_engine = ShadowEngine()

    def _serialize_payload(self, obj: Any) -> Any:
        """Safely serialize Pydantic models or nested containers."""
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if isinstance(obj, dict):
            return {k: self._serialize_payload(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize_payload(i) for i in obj]
        return obj

    def run_pipeline(self) -> Optional[KernelDecision]:
        """Execute one full v5.0 macro research cycle."""
        logger.info("Starting Macro OS v5.0 pipeline...")

        try:
            raw_macro = self.tv.fetch()
            actual_positions = self.futu.fetch_positions()

            if raw_macro is None:
                logger.error("Pipeline aborted: MCP macro data failed")
                return None

            features = build_features(raw_macro)

            macro_state = compute_macro_state(features)
            regime = str(macro_state.quadrant)
            div_engine = DivergencePhaseEngine(use_pine_data=False)
            div_state = div_engine.compute_state(features, vix=features.get("vix", 20.0))
            divergence_phase = map_phase(div_state.score)
            recovery_active = features.get("recovery_signal", False)

            if recovery_active and divergence_phase in ["LATE", "MID"]:
                self.state["days_in_recovery"] += 1
            else:
                self.state["days_in_recovery"] = 0

            proposed_risk = 0.80

            approved_decision = kernel_decide(
                features=features,
                hard_regime=regime,
                soft_regime_label="RISK_ON",
                risk_score=features.get("risk_score", 0.5),
                confidence=0.8,
                config=self.config,
                divergence_phase=divergence_phase,
                recovery_active=recovery_active,
                proposed_risk=proposed_risk,
                days_in_recovery=self.state["days_in_recovery"],
                previous_risk_budget=self.state["previous_risk_budget"],
            )

            self.state["previous_risk_budget"] = approved_decision.risk_budget

            # ===================== Phase 3 风控拦截层 =====================
            if self.enable_risk_gateway:
                current_bar = self._extract_bar_dict(features)
                risk_action = should_execute_risk_action(current_bar)

                # 1. 最高优先级：跳空强平 → 阻断后续所有逻辑
                if risk_action.action_type == RiskActionType.FATAL_GAP_LIQUIDATION:
                    logger.warning(
                        "[FATAL] 跳空击穿硬止损，执行强制平仓 | price=%s",
                        risk_action.gap_price,
                    )
                    self._force_liquidate_all(risk_action)
                    self.feishu.send_alert(
                        f"[FATAL_GAP_LIQUIDATION] 跳空强平触发 | "
                        f"gap_price={risk_action.gap_price} | "
                        f"breached_stop={risk_action.breached_stop}"
                    )
                    return None

                # 2. 跟踪止盈：执行部分减仓后继续（不阻断报告生成）
                if risk_action.action_type == RiskActionType.TRAILING_EXIT:
                    logger.info(
                        "[TrailingExit] 触发跟踪止盈减仓 | size=%s",
                        risk_action.size,
                    )
                    self._execute_partial_exit(risk_action.size)

                # 3. 入场/加仓：与 kernel_decide 做 AND 逻辑
                #    kernel_decide 已产出 approved_decision，风控准入即放行；
                #    若风控拒绝（HOLD），此处记录但不阻断报告流。
                if risk_action.action_type == RiskActionType.HOLD:
                    logger.info(
                        "[RiskGateway] 风控拒绝入场 | reason=%s",
                        risk_action.reason,
                    )
            # ===================== 风控拦截层结束 =====================

            approved_allocation = {
                "QQQ": approved_decision.risk_budget,
                "CASH": approved_decision.defense_budget,
            }
            diff_report = compute_actionable_diff(approved_allocation, actual_positions.to_dict())

            market_data = self._serialize_payload(raw_macro)
            self.shadow_engine.update_daily(market_data, approved_allocation)
            shadow_report = self.shadow_engine.generate_counterfactual_report()

            report_md = self.cio_agent.generate_daily_plan(
                regime_probs=approved_decision.regime_probs or {},
                allocation=approved_allocation,
                diff_report=diff_report,
                shadow_report=shadow_report,
                features_summary=raw_macro,
            )

            event = Event(
                source="MACRO_OS_V5",
                symbol="MACRO",
                event_type="RESEARCH_REPORT",
                payload=self._serialize_payload(
                    {
                        "regime": regime,
                        "divergence_phase": divergence_phase,
                        "kernel_decision": approved_decision,
                        "diff_summary": diff_report,
                        "features": features,
                    }
                ),
            )
            appended = self.vault.append(event)
            if not appended:
                logger.warning("Duplicate event detected, skipping vault append: %s", event.event_id)

            self.feishu.send_message(
                title=f"Macro OS v5.0 Daily Report | {regime} | Auth: {approved_decision.authority.value}",
                text=report_md,
            )

            logger.info("Pipeline execution complete, report sent.")
            return approved_decision

        except Exception as e:
            logger.exception("Pipeline failed with a fatal error: %s", e)
            return None

    def _extract_bar_dict(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """从 features 中提取当前 bar 的关键信息，供风控钩子消费。"""
        return {
            "close": features.get("close"),
            "open": features.get("open"),
            "high": features.get("high"),
            "low": features.get("low"),
            "atr": features.get("atr"),
            "vix": features.get("vix"),
            "brent_shock": features.get("brent_shock", 0.0),
            "spacetime_score": features.get("spacetime_score", 0.85),
            "j1_confirmed": features.get("j1_confirmed", True),
            "symbol": features.get("symbol", "UNKNOWN"),
            "index": features.get("bar_index"),
        }

    def _force_liquidate_all(self, risk_action: RiskAction) -> None:
        """强制平仓所有持仓（跳空强平触发时调用）。

        TODO: 接入实盘下单接口（Futu / CTP 等）执行市价全平。
        """
        logger.critical(
            "[Orchestrator] 执行跳空强平，目标价格: %s | breached_stop: %s",
            risk_action.gap_price,
            risk_action.breached_stop,
        )

    def _execute_partial_exit(self, size: float) -> None:
        """执行部分减仓（跟踪止盈触发时调用）。

        TODO: 接入实盘下单接口执行指定数量的减仓。
        """
        logger.info("[Orchestrator] 执行部分减仓，数量: %s", size)

    def dry_run(self) -> tuple[Optional[KernelDecision], dict]:
        """Execute pipeline without writing events or sending notifications."""
        logger.info("Executing DRY RUN...")
        raw_macro = self.tv.fetch()
        if raw_macro is None:
            raw_macro = FeatureSchema(source=DataSource.MOCK, fetched_at=datetime.datetime.now())

        features = build_features(raw_macro)
        macro_state = compute_macro_state(features)
        regime = str(macro_state.quadrant)
        div_engine = DivergencePhaseEngine(use_pine_data=False)
        div_state = div_engine.compute_state(features, vix=20.0)
        divergence_phase = map_phase(div_state.score)

        approved_decision = kernel_decide(
            features=features,
            hard_regime=regime,
            soft_regime_label="RISK_ON",
            risk_score=0.5,
            confidence=0.8,
            config=self.config,
            divergence_phase=divergence_phase,
            recovery_active=False,
            proposed_risk=0.8,
            days_in_recovery=0,
            previous_risk_budget=0.5,
        )

        return approved_decision, self._serialize_payload(features)

    def health(self) -> Dict[str, Any]:
        """Return orchestrator + adapter health status."""
        return {
            "pipeline": "operational_v5",
            "risk_gateway_enabled": self.enable_risk_gateway,
            "events_written": self.vault.count_events(),
            "tradingview": self.tv.health(),
            "futu_sensor": "connected" if self.futu else "offline",
            "feishu": self.feishu.health(),
            "last_successful_run": datetime.datetime.now().isoformat(),
        }
