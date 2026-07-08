"""Macro OS — Idempotent event store adapter.

Append-only JSONL writer with idempotency enforcement via event_id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from core.schemas import Event, LedgerSummary


class VaultAdapter:
    """Event store backed by a JSONL file.

    All writes are append-only. Idempotency is enforced by checking
    event_id uniqueness before each write.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._known_ids: Optional[Set[str]] = None

    def _load_known_ids(self) -> Set[str]:
        """Lazy-load all known event IDs from the ledger."""
        if self._known_ids is not None:
            return self._known_ids
        self._known_ids = set()
        if not self.path.exists():
            return self._known_ids
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    eid = data.get("event_id")
                    if eid:
                        self._known_ids.add(eid)
                except json.JSONDecodeError:
                    continue
        return self._known_ids

    def append(self, event: Event) -> bool:
        """Append an event if it does not already exist.

        Args:
            event: Event object with computed event_id.

        Returns:
            True if appended, False if duplicate (idempotency).
        """
        known = self._load_known_ids()
        if event.event_id in known:
            return False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(event.to_jsonl() + "\n")
            f.flush()

        self._known_ids.add(event.event_id)
        return True

    def read_all(self) -> List[Event]:
        """Read all events from the ledger.

        Returns:
            List of Event objects, in chronological order.
        """
        events: List[Event] = []
        if not self.path.exists():
            return events
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(Event.from_jsonl(line))
        return events

    def validate(self) -> LedgerSummary:
        """Validate ledger integrity: format, idempotency, schema.

        Returns:
            LedgerSummary with validation results.
        """
        summary = LedgerSummary()
        seen: Dict[str, int] = {}
        errors: List[Dict] = []

        if not self.path.exists():
            summary.valid = True
            return summary

        with open(self.path, "r") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                # JSON format check
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    errors.append({"line": line_no, "error": "invalid JSON"})
                    summary.valid = False
                    continue

                # Schema compliance
                try:
                    event = Event.model_validate(data)
                except Exception as e:
                    errors.append(
                        {"line": line_no, "error": f"schema violation: {e}"}
                    )
                    summary.valid = False
                    continue

                # Idempotency
                if event.event_id in seen:
                    summary.duplicate_ids.append(event.event_id)
                    summary.valid = False
                else:
                    seen[event.event_id] = line_no

                summary.total_events += 1

        summary.unique_events = len(seen)
        summary.schema_errors = errors
        return summary

    def count_events(self) -> int:
        """Return total number of events in the ledger."""
        return len(self._load_known_ids())

    def clear_cache(self) -> None:
        """Force re-read on next access."""
        self._known_ids = None
