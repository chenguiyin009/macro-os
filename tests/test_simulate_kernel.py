"""Tests for the decision kernel CLI simulator."""
from __future__ import annotations


class TestSimulateKernel:
    def test_simulate_kernel_outputs_expected_json(self) -> None:
        import json
        from scripts.simulate_kernel import main

        test_args = [
            "--phase", "EARLY",
            "--regime", "RISK_ON",
            "--soft-regime-label", "RISK_ON",
            "--risk-score", "0.8",
            "--confidence", "0.8",
            "--proposed-risk", "0.8",
            "--recovery", "false",
            "--days-in-recovery", "0",
            "--previous-risk-budget", "0.0",
        ]
        result_json = main(test_args)
        payload = json.loads(result_json)
        assert payload["execution_outcome"]["final_risk_budget"] == 0.10
        assert payload["execution_outcome"]["reason_code"] == "GLOBAL_RAMP_ACTIVE"
