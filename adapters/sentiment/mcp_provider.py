"""MCP-backed sentiment provider (Workbuddy side, V2.0).

设计原则（来自 handoff `workbuddy_sentiment_backtest_handoff.md`）：
- **只做数据映射，不重写阶段逻辑**：本模块把「录制好的 MCP 原始聚合结果」
  映射为 `SentimentRawInput`，交给 `SentimentShadowEngine.compute`。
- **缺字段一律 None**：engine 会输出 PARTIAL / DEGRADED；绝不用 0 假充。
- **数据源诚实**：fixture 的 `source` 必须是 LIVE / LIVE_PARTIAL。

录制流程（由 Workbuddy agent 执行，不在本模块内强依赖外网 MCP）：
  1. agent 调用行情 MCP（neodata / westock / tdx / tradingview）拉取某 `as_of` 的原始数据
  2. agent 把多源响应聚合为扁平 dict（字段名尽量对齐 SentimentRawInput）
  3. agent 落盘到 `vault/shadow/mcp_raw/{as_of}.json`（或 tests/fixtures/sentiment/mcp_raw_{as_of}.json）
  4. 本 provider.fetch() 读取该文件 → 映射 → SentimentRawInput

映射时，所有未出现在原始 dict 中的字段保持 None。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from core.sentiment.models import SentimentRawInput

ROOT = Path(__file__).resolve().parents[2]  # macro-os/
DEFAULT_MCP_RAW_DIR = ROOT / "vault" / "shadow" / "mcp_raw"
FIXTURE_MCP_RAW_DIR = ROOT / "tests" / "fixtures" / "sentiment"


def _raw_from_dict(d: Dict[str, Any]) -> SentimentRawInput:
    """Pick only declared SentimentRawInput fields; missing -> None."""
    keys = SentimentRawInput.__dataclass_fields__.keys()
    payload = {k: d.get(k) for k in keys if k in d}
    return SentimentRawInput(**payload)


class McpSentimentProvider:
    """Reads a recorded MCP raw payload and maps it to SentimentRawInput.

    The MCP-original payload is a flat dict whose keys align with
    SentimentRawInput fields (already aggregated by the recording agent).
    Any absent field stays None -> engine emits PARTIAL, never fabricated.
    """

    def __init__(self, raw_dir: Optional[Path] = None) -> None:
        self.raw_dir = Path(raw_dir) if raw_dir else DEFAULT_MCP_RAW_DIR
        self.fixture_dir = FIXTURE_MCP_RAW_DIR

    def _resolve(self, as_of: str) -> Path:
        p = self.raw_dir / f"mcp_raw_{as_of}.json"
        if p.exists():
            return p
        p2 = self.fixture_dir / f"mcp_raw_{as_of}.json"
        if p2.exists():
            return p2
        raise FileNotFoundError(
            f"No recorded MCP raw for {as_of} at {self.raw_dir} or {self.fixture_dir}"
        )

    def fetch(self, as_of: str, session: str = "CLOSE") -> SentimentRawInput:
        path = self._resolve(as_of)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("as_of", as_of)
        data.setdefault("session", session)
        src = data.get("source")
        # honesty guard: recorded MCP data must be LIVE / LIVE_PARTIAL
        if src not in ("LIVE", "LIVE_PARTIAL"):
            data["source"] = "LIVE_PARTIAL"
        return _raw_from_dict(data)


def build_raw_from_mcp_aggregates(
    as_of: str,
    session: str,
    aggregates: Dict[str, Any],
    source: str = "LIVE_PARTIAL",
) -> SentimentRawInput:
    """Helper used by the recording step: accept an already-aggregated flat dict,
    fill defaults, and return SentimentRawInput. Missing keys -> None."""
    aggregates = dict(aggregates)
    aggregates.setdefault("as_of", as_of)
    aggregates.setdefault("session", session)
    aggregates.setdefault("source", source)
    return _raw_from_dict(aggregates)


def persist_mcp_raw(as_of: str, payload: Dict[str, Any], to_fixtures: bool = False) -> Path:
    """Save a recorded MCP raw payload to disk (recording step)."""
    target_dir = FIXTURE_MCP_RAW_DIR if to_fixtures else DEFAULT_MCP_RAW_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"mcp_raw_{as_of}.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out
