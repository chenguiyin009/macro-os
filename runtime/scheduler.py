"""Macro OS — Scheduler for periodic pipeline execution.

Runs the macro decision pipeline on a configurable interval.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from runtime.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class Scheduler:
    """Simple interval-based scheduler for the macro pipeline."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        interval_minutes: int = 15,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.interval_seconds = interval_minutes * 60
        self.config = config or {}
        self._running = False

    def run_once(self) -> None:
        """Execute a single pipeline cycle."""
        logger.info("Scheduler: starting pipeline cycle")
        decision = self.orchestrator.run_pipeline()
        if decision is None:
            logger.warning("Scheduler: pipeline returned no decision")
        else:
            logger.info(
                "Scheduler: cycle complete — action=%s conf=%.3f",
                decision.action.value,
                decision.confidence,
            )

    def run_loop(self) -> None:
        """Run pipeline on a loop at the configured interval.

        Per-cycle resilience: a single failed cycle (e.g. a transient network
        timeout or API error inside ``Orchestrator.run_pipeline``) must NEVER
        take down the 7x24 daemon. The cycle body is isolated so that any
        exception is caught, logged, and alerted here — the loop then sleeps and
        proceeds to the next cycle instead of propagating the error upward.
        """
        self._running = True
        logger.info(
            "Scheduler: starting loop with interval=%ds", self.interval_seconds
        )
        while self._running:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the daemon
                logger.error(
                    "Scheduler: cycle crashed, auto-recovering: %s", exc, exc_info=True
                )
                self._notify_crash(exc)
            time.sleep(self.interval_seconds)

    def _notify_crash(self, exc: Exception) -> None:
        """Best-effort ``[CRITICAL_ALERT]`` dispatch on a crashed cycle.

        Must never raise: a failed alert transport (e.g. Feishu webhook down)
        must not break the auto-recovery path, so the alert call itself is
        guarded.
        """
        feishu = getattr(self.orchestrator, "feishu", None)
        if feishu is None or not hasattr(feishu, "send_alert"):
            return
        try:
            feishu.send_alert(
                f"[CRITICAL_ALERT] 单轮调度崩溃，守护进程尝试自动恢复: {exc}"
            )
        except Exception:  # alert transport failed — do not crash recovery
            logger.warning("Scheduler: failed to dispatch crash alert", exc_info=True)

    def stop(self) -> None:
        """Signal the scheduler loop to stop."""
        self._running = False
        logger.info("Scheduler: stop signal received")

    def health(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "interval_seconds": self.interval_seconds,
        }
