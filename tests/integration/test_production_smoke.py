"""Production-path (non-dry-run) smoke test.

WHY THIS FILE EXISTS
--------------------
The previous engineering spec described a fictional production path:
``runtime.main.run_pipeline(dry_run=...)`` patching ``runtime.main.VaultGateway``
and ``runtime.main.DingTalkNotifier``. None of those exist. Verified against
source:

  * The real non-dry-run entry is ``main()`` (argparse) -> ``Orchestrator.run_pipeline()``
    (no ``dry_run`` arg; lives in ``runtime/orchestrator.py``).
  * The real side-effect classes are ``adapters.vault.VaultAdapter`` and
    ``adapters.feishu.FeishuAdapter`` — NOT ``VaultGateway`` / ``DingTalkNotifier``.
  * The real side-effect calls are ``self.vault.append(event)`` and
    ``self.feishu.send_message(title=..., text=...)`` (orchestrator lines 149 / 153).

This suite exercises the REAL ``Orchestrator.run_pipeline`` wiring and asserts
that, on the production (non-dry-run) branch, the event is written to the vault
and the notification is dispatched. Upstream heavy compute (feature build,
regime inference, kernel decision, divergence engine, shadow/cio agents) is
mocked so the test isolates the *wiring* — the exact dry-run blind spot we
want to close — without performing real network/disk I/O.

No source code is modified. Pure additive.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import runtime.orchestrator as orch_mod
from runtime.orchestrator import Orchestrator


class TestProductionSmoke:
    """Guards that the production (non-dry-run) path is actually wired to
    vault persistence + notification dispatch."""

    @patch("runtime.orchestrator.compute_actionable_diff")
    @patch("runtime.orchestrator.map_phase")
    @patch("runtime.orchestrator.kernel_decide")
    @patch("runtime.orchestrator.compute_macro_state")
    @patch("runtime.orchestrator.build_features")
    @patch("runtime.orchestrator.ShadowEngine")
    def test_run_pipeline_wires_vault_and_feishu(
        self, mock_shadow_cls, mock_build, mock_state, mock_kernel,
        mock_map_phase, mock_diff,
    ):
        # --- isolate upstream heavy compute (keep wiring under test) ---
        mock_build.return_value = {
            "vix": 20.0, "recovery_signal": False, "risk_score": 0.5,
        }
        mock_state.return_value = SimpleNamespace(quadrant="RISK_ON", score=0.5)
        mock_map_phase.return_value = "MID"
        mock_diff.return_value = {}

        decision = MagicMock()
        decision.risk_budget = 0.8
        decision.defense_budget = 0.2
        decision.authority = MagicMock(value="L1")
        decision.regime_probs = {}
        decision.model_dump.return_value = {
            "risk_budget": 0.8, "defense_budget": 0.2,
            "authority": {"value": "L1"}, "regime_probs": {},
        }
        mock_kernel.return_value = decision

        # --- build orchestrator with mocked adapters (no real IO) ---
        orch = Orchestrator(
            tradingview=MagicMock(),
            vault=MagicMock(),
            feishu=MagicMock(),
            futu=MagicMock(),          # avoid real FutuSensor() instantiation
            cio_agent=MagicMock(),     # avoid real CioCopilot() instantiation
            sector_allocator=MagicMock(),
            config={},
        )
        # ShadowEngine is patched at class level -> instance is a MagicMock
        orch.shadow_engine.generate_counterfactual_report.return_value = {}
        orch.cio_agent.generate_daily_plan.return_value = "report-md"

        # Drive the real upstream data taps with canned data
        orch.tv.fetch.return_value = {"symbol": "MACRO", "regime": "RISK_ON"}
        orch.futu.fetch_positions.return_value = MagicMock(to_dict=lambda: {})

        # --- exercise the REAL production path ---
        try:
            result = orch.run_pipeline()
        except Exception as exc:  # pragma: no cover - defensive
            import pytest
            pytest.fail(f"生产路径接线崩溃，抛出未捕获异常: {exc}")

        # --- iron-law assertions: real side effects fired ---
        assert orch.tv.fetch.called, "tv.fetch 未被调用 — 上游数据源未接线"
        assert orch.futu.fetch_positions.called, "futu.fetch_positions 未被调用"
        assert orch.vault.append.called, "vault.append 未被调用 — 生产路径未接线到事件落库"
        assert orch.feishu.send_message.called, "feishu.send_message 未被调用 — 生产路径未接线到通知分发"
        assert result is not None, "run_pipeline 返回 None — 生产路径异常提前返回"
