from __future__ import annotations

import json

import pytest

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


def test_main_dry_run_writes_output_file(monkeypatch, tmp_path) -> None:
    """--output 路径（CI/CD 友好）必须把决策 JSON 落盘，且内容可解析。"""
    from runtime import main as main_module

    class FakeOrchestrator:
        def dry_run(self):
            return KernelDecision(), {"feature": "mock"}

    monkeypatch.setattr(main_module, "build_orchestrator", lambda: FakeOrchestrator())
    out_file = tmp_path / "decision.json"
    monkeypatch.setattr(
        main_module.sys, "argv", ["prog", "--dry-run", "--output", str(out_file)]
    )

    main_module.main()

    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["risk_budget"] == 0.5
    assert payload["defense_budget"] == 0.5


def test_main_dry_run_exits_when_decision_none(monkeypatch, capsys) -> None:
    """dry_run 返回 (None, ...) 时，main 必须干净退出（exit 1）而非抛异常。"""
    from runtime import main as main_module

    class FakeOrchestrator:
        def dry_run(self):
            return None, {}

    monkeypatch.setattr(main_module, "build_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(main_module.sys, "argv", ["prog", "--dry-run"])

    with pytest.raises(SystemExit) as exc_info:
        main_module.main()
    assert exc_info.value.code == 1


def test_main_exits_on_orchestrator_build_failure(monkeypatch, capsys) -> None:
    """配置校验失败（build_orchestrator 抛 ValueError）必须 fail-fast 退出 1。"""
    from runtime import main as main_module

    def _boom():
        raise ValueError("invalid config")

    monkeypatch.setattr(main_module, "build_orchestrator", _boom)
    monkeypatch.setattr(main_module.sys, "argv", ["prog", "--dry-run"])

    with pytest.raises(SystemExit) as exc_info:
        main_module.main()
    assert exc_info.value.code == 1


def test_orchestrator_dry_run_mocks_when_fetch_none(monkeypatch) -> None:
    """tv.fetch() 返回 None 时，dry_run 必须回退到 MOCK 数据并仍产出决策（韧性）。"""
    import tempfile
    from pathlib import Path

    from runtime.orchestrator import Orchestrator
    from adapters.tradingview import TradingViewAdapter
    from adapters.vault import VaultAdapter
    from adapters.feishu import FeishuAdapter

    tv = TradingViewAdapter(mcp_command="x", mcp_script_path="y", timeout_seconds=1)
    monkeypatch.setattr(tv, "fetch", lambda: None)
    vault = VaultAdapter(Path(tempfile.mkdtemp()) / "events.jsonl")
    orch = Orchestrator(tradingview=tv, vault=vault, feishu=FeishuAdapter())

    decision, features = orch.dry_run()
    assert decision is not None
    assert isinstance(features, dict)
