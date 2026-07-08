"""Macro OS - TradingView MCP client adapter.

Minimal wrapper around the MCP subprocess bridge.
Falls back through: live subprocess -> relay log -> mock.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.schemas import DataSource, FeatureSchema

logger = logging.getLogger(__name__)

DEFAULT_RELAY_LOG_PATH = (
    Path(__file__).resolve().parents[2] / "relay" / "logs" / "tv-desktop-monitor.out.log"
)
DEFAULT_RELAY_MAX_AGE_SECONDS = 300
DEFAULT_RELAY_SCAN_BYTES = 64 * 1024
MACRO_SIGNATURE_KEYS = {"vix", "dxy", "danger_score", "qqq_close", "close"}

class TradingViewAdapter:
    """Adapter for fetching macro data from TradingView MCP."""

    def __init__(
        self,
        mcp_command: str = "node",
        mcp_script_path: str = "",
        timeout_seconds: int = 8,
        relay_log_path: str | Path | None = None,
        relay_max_age_seconds: int = DEFAULT_RELAY_MAX_AGE_SECONDS,
        relay_scan_bytes: int = DEFAULT_RELAY_SCAN_BYTES,
    ) -> None:
        self.mcp_command = mcp_command
        self.mcp_script_path = Path(mcp_script_path) if mcp_script_path else None
        self.timeout_seconds = timeout_seconds
        relay_log_candidate = relay_log_path or os.getenv("MACRO_OS_RELAY_LOG_PATH") or DEFAULT_RELAY_LOG_PATH
        self.relay_log_path = Path(relay_log_candidate)
        self.relay_max_age_seconds = relay_max_age_seconds
        self.relay_scan_bytes = max(1024, int(relay_scan_bytes))
        self._last_success_time: Optional[float] = None
        self._last_error: Optional[str] = None

    def fetch(self) -> Optional[FeatureSchema]:
        """Attempt live MCP fetch with fallback chain.

        Returns:
            FeatureSchema if successful, None if all sources fail.
        """
        if self.mcp_script_path and self.mcp_script_path.exists():
            result = self._run_mcp_subprocess()
            if result is not None:
                return result

        result = self._read_relay_log()
        if result is not None:
            return result

        return self._mock_snapshot()

    def _run_mcp_subprocess(self) -> Optional[FeatureSchema]:
        """Run MCP script as subprocess and parse output."""
        try:
            proc = subprocess.run(
                [self.mcp_command, str(self.mcp_script_path)],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw = proc.stdout.strip()
                result = self._parse_mcp_output(raw)
                if result is not None:
                    self._last_success_time = time.time()
                    self._last_error = None
                    return result
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as exc:
            self._last_error = f"mcp subprocess failed: {exc}"
            logger.warning("TradingView MCP subprocess failed: %s", exc)
        return None

    def _read_relay_log(self) -> Optional[FeatureSchema]:
        """Read the latest snapshot from the relay stdout log."""
        path = self.relay_log_path
        if not path.exists():
            self._last_error = f"relay log missing: {path}"
            logger.warning("TradingView relay log missing: %s", path)
            return None

        try:
            file_size = path.stat().st_size
        except OSError as exc:
            self._last_error = f"relay log stat failed: {exc}"
            logger.warning("TradingView relay log stat failed: %s", exc)
            return None

        if file_size <= 0:
            self._last_error = f"relay log empty: {path}"
            logger.warning("TradingView relay log empty: %s", path)
            return None

        try:
            age_seconds = time.time() - path.stat().st_mtime
        except OSError as exc:
            self._last_error = f"relay log stat failed: {exc}"
            logger.warning("TradingView relay log stat failed: %s", exc)
            return None

        if age_seconds > self.relay_max_age_seconds:
            self._last_error = f"relay log stale: {age_seconds:.0f}s old"
            logger.warning("TradingView relay log stale: %.1fs old (%s)", age_seconds, path)
            return None

        window = min(file_size, self.relay_scan_bytes)
        payload: Optional[Dict[str, Any]] = None

        while True:
            try:
                raw_text = self._read_tail_text(path, window)
            except OSError as exc:
                self._last_error = f"relay log read failed: {exc}"
                logger.warning("TradingView relay log read failed: %s", exc)
                return None

            payload = self._extract_last_macro_json(raw_text)
            if payload is not None or window >= file_size:
                break
            window = min(file_size, window * 2)

        if payload is None:
            self._last_error = "relay log tail did not contain a macro snapshot"
            logger.warning("TradingView relay log tail did not contain a macro snapshot: %s", path)
            return None

        try:
            snapshot = FeatureSchema(**payload)
        except (TypeError, ValueError) as exc:
            self._last_error = f"relay payload validation failed: {exc}"
            logger.warning("TradingView relay payload validation failed: %s", exc)
            return None

        self._last_success_time = time.time()
        self._last_error = None
        return snapshot

    def _read_tail_text(self, path: Path, window_size: int) -> str:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            read_size = min(file_size, max(1, window_size))
            handle.seek(file_size - read_size)
            return handle.read(read_size).decode("utf-8", errors="replace")

    def _extract_last_macro_json(self, text: str) -> Optional[Dict[str, Any]]:
        decoder = json.JSONDecoder()
        search_end = len(text)

        while search_end > 0:
            idx = text.rfind("{", 0, search_end)
            if idx == -1:
                break

            try:
                obj, _ = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                search_end = idx
                continue

            if isinstance(obj, dict) and any(key in obj for key in MACRO_SIGNATURE_KEYS):
                return obj

            logger.debug("Skipped non-macro JSON block in relay log tail.")
            search_end = idx

        return None

    def _mock_snapshot(self) -> FeatureSchema:
        """Return a realistic mock snapshot for local development."""
        return FeatureSchema(
            dxy=104.5,
            vix=18.2,
            hy_credit_spread=320,
            tips_yield=0.6,
            gold=2350.0,
            equity_tech_rotation=0.15,
            source=DataSource.MOCK,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

    def _parse_mcp_output(self, raw_text: str) -> Optional[FeatureSchema]:
        """Parse raw MCP text output into structured features.

        Note: In production this delegates to the LLM parser.
        The mock implementation handles the common JSON-wrapped format.
        """
        try:
            data = json.loads(raw_text)
            return FeatureSchema(**data)
        except (json.JSONDecodeError, ValueError) as exc:
            self._last_error = f"mcp output parse failed: {exc}"
            logger.warning("TradingView MCP output parse failed: %s", exc)
        return None

    def fetch_raw(self) -> Optional[str]:
        """Fetch raw MCP output without parsing (for LLM ingestion)."""
        if self.mcp_script_path and self.mcp_script_path.exists():
            try:
                proc = subprocess.run(
                    [self.mcp_command, str(self.mcp_script_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip()
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass

        relay_snapshot = self._read_relay_log()
        if relay_snapshot is not None:
            return relay_snapshot.model_dump_json()

        return self._mock_snapshot().model_dump_json()

    def health(self) -> Dict[str, Any]:
        """Return adapter health status."""
        exists = self.relay_log_path.exists()
        age_seconds: Optional[float] = None
        if exists:
            try:
                age_seconds = time.time() - self.relay_log_path.stat().st_mtime
            except OSError:
                age_seconds = None

        return {
            "mcp_script_exists": bool(self.mcp_script_path and self.mcp_script_path.exists()),
            "timeout_seconds": self.timeout_seconds,
            "relay_log_path": str(self.relay_log_path),
            "relay_log_exists": exists,
            "relay_log_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
            "relay_log_is_stale": (age_seconds > self.relay_max_age_seconds) if age_seconds is not None else None,
            "last_success_time": self._last_success_time,
            "last_error": self._last_error,
        }


