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
        """Run pipeline on a loop at the configured interval."""
        self._running = True
        logger.info(
            "Scheduler: starting loop with interval=%ds", self.interval_seconds
        )
        while self._running:
            self.run_once()
            time.sleep(self.interval_seconds)

    def stop(self) -> None:
        """Signal the scheduler loop to stop."""
        self._running = False
        logger.info("Scheduler: stop signal received")

    def health(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "interval_seconds": self.interval_seconds,
        }
