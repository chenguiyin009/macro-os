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

from core.schemas import DataSource, FeatureSchema, PineConclusionSchema

logger = logging.getLogger(__name__)

DEFAULT_PINE_BRIDGE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "relay" / "pine-bridge.mjs"
)
DEFAULT_PINE_TIMEOUT_SECONDS = 30

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

        result = self._fetch_composite_fallback()
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



    def _fetch_composite_fallback(self) -> Optional[FeatureSchema]:
        """yfinance + FRED + last-good cache (skips recursive TV fetch)."""
        try:
            from adapters.macro_composite import fetch_merged_macro_snapshot
        except Exception as exc:  # pragma: no cover
            self._last_error = f"composite import failed: {exc}"
            logger.warning(self._last_error)
            return None

        yf_enabled = os.getenv("MACRO_OS_YFINANCE_ENABLED", "1").strip() not in {"0", "false", "False"}
        fred_enabled = os.getenv("MACRO_OS_FRED_ENABLED", "1").strip() not in {"0", "false", "False"}
        cache_enabled = os.getenv("MACRO_OS_MACRO_CACHE_ENABLED", "1").strip() not in {"0", "false", "False"}

        result = fetch_merged_macro_snapshot(
            include_tv=False,
            include_yfinance=yf_enabled,
            include_fred=fred_enabled,
            use_cache=cache_enabled,
        )
        if result is None:
            self._last_error = "composite fallback empty (yfinance/fred/cache)"
            logger.warning(self._last_error)
            return None
        self._last_success_time = time.time()
        self._last_error = None
        logger.info(
            "TradingView adapter using composite fallback (yfinance=%s fred=%s cache=%s)",
            yf_enabled,
            fred_enabled,
            cache_enabled,
        )
        return result

    def _fetch_fred(self) -> Optional[FeatureSchema]:
        """Optional FRED live fallback for funding-price features."""
        enabled = os.getenv("MACRO_OS_FRED_ENABLED", "1").strip() not in {"0", "false", "False"}
        if not enabled:
            return None
        try:
            from adapters.fred import FredMacroAdapter
        except Exception as exc:  # pragma: no cover
            self._last_error = f"fred import failed: {exc}"
            logger.warning("FRED adapter import failed: %s", exc)
            return None
        adapter = FredMacroAdapter(timeout_seconds=min(8.0, float(self.timeout_seconds or 8)))
        result = adapter.fetch()
        if result is None:
            self._last_error = adapter.last_error or "fred fetch failed"
            logger.warning("FRED live fetch failed: %s", self._last_error)
            return None
        self._last_success_time = time.time()
        self._last_error = adapter.last_error
        logger.info("TradingView adapter using FRED live macro snapshot")
        return result

    def _mock_snapshot(self) -> FeatureSchema:
        """Return a research-aligned mock snapshot (week of 2026-07-06 funding-price Q1).

        Levels track docs/research/2026-07-10-funding-price-weekly.md so local
        dry-runs narrate duration stress test rather than a 0.6% TIPS fantasy world.
        """
        return FeatureSchema(
            dxy=101.12,
            vix=18.2,
            hy_credit_spread=320,
            tips_yield=2.32,
            tips_yield_change_5d_bp=14.0,
            nominal_10y=4.55,
            nominal_10y_change_5d_bp=17.0,
            nominal_30y=5.06,
            nominal_30y_change_5d_bp=19.0,
            nominal_2y=4.19,
            bei_10y=2.26,
            gold=374.45,
            equity_tech_rotation=0.15,
            danger_score=35.0,
            risk_score=0.55,
            recovery_signal=False,
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

    # ── Pine conclusion bridge (relay/pine-bridge.mjs over CDP) ──

    def fetch_pine_conclusions(
        self,
        symbol: Optional[str] = None,
        script_name: Optional[str] = None,
        cdp_url: str = "http://127.0.0.1:9222",
    ) -> Optional[PineConclusionSchema]:
        """Fetch a Pine script conclusion from TradingView via the CDP bridge.

        Fallback chain: live bridge subprocess -> mock conclusion.
        """
        result = self._run_pine_bridge(symbol=symbol, script_name=script_name, cdp_url=cdp_url)
        if result is not None:
            return result
        return self._mock_pine_conclusion(symbol=symbol, script_name=script_name)

    def _run_pine_bridge(
        self,
        symbol: Optional[str],
        script_name: Optional[str],
        cdp_url: str,
    ) -> Optional[PineConclusionSchema]:
        bridge = DEFAULT_PINE_BRIDGE_SCRIPT
        if not bridge.exists():
            self._last_error = f"pine bridge missing: {bridge}"
            logger.warning("TradingView pine bridge missing: %s", bridge)
            return None

        # Bridge always emits one JSON line on stdout; --json is accepted (no-op)
        # and kept for parity with the bridge's documented CLI.
        cmd = [self.mcp_command, str(bridge), "--json", "--cdp-url", cdp_url]
        if symbol:
            cmd += ["--symbol", symbol]
        if script_name:
            cmd += ["--script", script_name]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                # Bridge emits UTF-8 (may contain non-ASCII study names); on
                # Windows the default locale is GBK, which raises UnicodeDecodeError
                # and leaves proc.stderr as None. Force UTF-8 with lossy fallback.
                encoding="utf-8",
                errors="replace",
                timeout=DEFAULT_PINE_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as exc:
            self._last_error = f"pine bridge subprocess failed: {exc}"
            logger.warning("TradingView pine bridge subprocess failed: %s", exc)
            return None

        if proc.returncode != 0:
            self._last_error = f"pine bridge exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
            logger.warning("TradingView pine bridge error: %s", self._last_error)
            return None

        return self._parse_pine_output(proc.stdout)

    def _parse_pine_output(self, raw_text: str) -> Optional[PineConclusionSchema]:
        """Parse the bridge's single-line JSON conclusion."""
        # The bridge prints exactly one JSON line; tolerate extra whitespace.
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                conclusion = PineConclusionSchema(**data)
                self._last_success_time = time.time()
                self._last_error = None
                return conclusion
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                self._last_error = f"pine output parse failed: {exc}"
                logger.warning("TradingView pine output parse failed: %s", exc)
        return None

    def _mock_pine_conclusion(
        self,
        symbol: Optional[str],
        script_name: Optional[str],
    ) -> PineConclusionSchema:
        """Return a clearly-labeled mock Pine conclusion for local development."""
        return PineConclusionSchema(
            source_script=script_name or "MOCK",
            symbol=symbol or "TVC:GOLD",
            tf="1D",
            chart_title="mock",
            signal=None,
            confidence=None,
            value=None,
            label=None,
            payload={"study_name": "MOCK", "study_kind": "", "values": [], "plots": []},
        )

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



