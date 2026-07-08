from __future__ import annotations

import json

from core.schemas import KernelDecision


def test_main_dry_run_unpacks_tuple(monkeypatch, capsys) -> None:
    from runtime import main as main_module

    class FakeOrchestrator:
        def __init__(self) -> None:
            self.calls = 0

        def dry_run(self):
            self.calls += 1
            return KernelDecision(), {"feature": "mock"}

    orchestrator = FakeOrchestrator()
    monkeypatch.setattr(main_module, "build_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(main_module.sys, "argv", ["prog", "--dry-run"])

    main_module.main()

    captured = capsys.readouterr()
    assert orchestrator.calls == 1
    payload = json.loads(captured.out)
    assert payload["risk_budget"] == 0.5
    assert payload["defense_budget"] == 0.5
