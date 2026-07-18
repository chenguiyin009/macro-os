"""Cold-start session defaults per Decision Authority Map §6 (review Q3)."""
from __future__ import annotations

from unittest.mock import MagicMock

from runtime.orchestrator import Orchestrator


def test_orchestrator_cold_start_previous_risk_budget_is_zero() -> None:
    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=MagicMock(),
        feishu=MagicMock(),
        futu=MagicMock(),
        config={"ENABLE_RISK_GATEWAY": False, "HYDRATE_SESSION_FROM_VAULT": False},
    )
    assert orch.state["previous_risk_budget"] == 0.0
    assert orch.state["days_in_recovery"] == 0

