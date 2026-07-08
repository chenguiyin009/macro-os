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
            "events_written": self.vault.count_events(),
            "tradingview": self.tv.health(),
            "futu_sensor": "connected" if self.futu else "offline",
            "feishu": self.feishu.health(),
            "last_successful_run": datetime.datetime.now().isoformat(),
        }
