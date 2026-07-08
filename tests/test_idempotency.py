"""Tests for idempotent event store."""

from __future__ import annotations

import tempfile
from pathlib import Path

from core.schemas import Event, compute_event_id
from adapters.vault import VaultAdapter


class TestEventIdempotency:
    def test_append_and_detect_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            vault = VaultAdapter(path)

            event = Event(
                source="MOCK", symbol="MACRO",
                event_type="TEST", payload={"value": 42},
            )

            assert vault.append(event) is True
            assert vault.append(event) is False

    def test_validate_detects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            vault = VaultAdapter(path)

            event = Event(
                source="MOCK", symbol="MACRO",
                event_type="TEST", payload={"value": 42},
            )
            vault.append(event)

            with open(path, "a") as f:
                f.write(event.to_jsonl() + "\n")

            vault.clear_cache()
            summary = vault.validate()
            assert len(summary.duplicate_ids) == 1
            assert summary.valid is False

    def test_validate_jsonl_format_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            vault = VaultAdapter(path)

            with open(path, "w") as f:
                f.write("not valid json\n")

            summary = vault.validate()
            assert len(summary.schema_errors) > 0
            assert summary.valid is False

    def test_compute_event_id_consistency(self) -> None:
        p = {"value": 42}
        eid1 = compute_event_id("2026-01-01T00:00:00Z", "SOURCE", "SYM", "TYPE", p)
        eid2 = compute_event_id("2026-01-01T00:00:00Z", "SOURCE", "SYM", "TYPE", p)
        assert eid1 == eid2
        assert len(eid1) == 64

    def test_different_payloads_different_ids(self) -> None:
        eid1 = compute_event_id("2026-01-01T00:00:00Z", "S1", "A", "T", {"v": 1})
        eid2 = compute_event_id("2026-01-01T00:00:00Z", "S1", "A", "T", {"v": 2})
        assert eid1 != eid2

    def test_empty_ledger_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            vault = VaultAdapter(path)
            summary = vault.validate()
            assert summary.valid is True
            assert summary.total_events == 0

    def test_read_all_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            vault = VaultAdapter(path)

            events = [
                Event(source="MOCK", symbol="MACRO", event_type="A", payload={"n": 1}),
                Event(source="MOCK", symbol="MACRO", event_type="B", payload={"n": 2}),
            ]
            for e in events:
                vault.append(e)

            loaded = vault.read_all()
            assert len(loaded) == 2
            assert loaded[0].event_id == events[0].event_id
            assert loaded[1].event_id == events[1].event_id
