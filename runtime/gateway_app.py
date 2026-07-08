"""Macro OS v5.0 - Gateway Data Collection & Sentinel Service.

Receives TradingView webhooks, records to ledger, sends Feishu alerts.
Strictly limited to: data ingestion, ledger recording, FEISHU_ALERTING.
NO trading decisions, NO YAML writes, NO LLM calls.
"""

from __future__ import annotations

import json, logging, time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Path setup for macro-os imports
import sys as _sys
_root = Path(__file__).resolve().parent.parent
if str(_root) not in _sys.path:
    _sys.path.insert(0, str(_root))

from core.gateway.gateway_core import (
    RegimeName,
    REGIME_ACTION,
    SUPPORTED_SCHEMA_VERSIONS,
    DB_PATH,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# === Configuration ===

LEGACY_REGIME_MAP: dict[str, RegimeName] = {
    "CRISIS": RegimeName.CASH_LIQUIDATION,
    "FAST_SHOCK": RegimeName.FAST_LIQUIDITY_SHOCK,
    "ELEVATED": RegimeName.NARROW_LEADERSHIP,
    "WATCH": RegimeName.AI_EXPANSION,
    "NEUTRAL": RegimeName.AI_EXPANSION,
}

REGIME_COLOR_MAP: dict[RegimeName, str] = {
    RegimeName.CASH_LIQUIDATION: "red",
    RegimeName.FAST_LIQUIDITY_SHOCK: "red",
    RegimeName.NARROW_LEADERSHIP: "orange",
    RegimeName.AI_EXPANSION: "green",
}

# === Database ===

async def init_db() -> None:
    """Create events table if not exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                priority TEXT DEFAULT 'SCHEDULED',
                status TEXT DEFAULT 'QUEUED',
                reason TEXT,
                received_at REAL,
                payload TEXT,
                schema_ver TEXT DEFAULT '1.0'
            )
        """)
        await db.commit()

async def get_adaptive_cooldown(db, danger_score: int) -> float:
    """Return adaptive cooldown based on danger level."""
    if danger_score >= 5:
        return 30.0
    elif danger_score >= 3:
        return 15.0
    return 5.0

# === Sentinel Logic ===

def _derive_action(composite_score: float, composite_regime: str, danger_score: int) -> dict:
    """Map legacy regime string to action/budget using normalized enum."""
    nr = LEGACY_REGIME_MAP.get(composite_regime, composite_regime)
    action, budget = REGIME_ACTION.get(nr, ("NEUTRAL", 1.0))
    if nr == RegimeName.AI_EXPANSION and danger_score >= 3:
        action, budget = "REDUCE", 0.50
    if nr == RegimeName.AI_EXPANSION and composite_score > 0.4:
        budget = 0.85
    return {"action": action, "budget": budget}

def _build_sentinel_card(payload: dict, derived: dict) -> dict:
    """Build Feishu interactive card for sentinel alerts."""
    raw = payload.get("composite_regime", "NEUTRAL")
    nr = LEGACY_REGIME_MAP.get(raw, raw)
    color = REGIME_COLOR_MAP.get(nr, "blue")
    elements = [{"tag": "markdown", "content": f"**Regime:** {raw}"}]
    if derived:
        elements.append({"tag": "markdown", "content": f"**Action:** {derived.get('action', 'N/A')} | **Budget:** {derived.get('budget', 'N/A')}"})
    return {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "Macro OS Sentinel Alert"}, "template": color},
            "elements": elements,
        },
    }

# === FastAPI App ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Gateway initialized: Data Collection & Sentinel Mode ONLY.")
    yield
    logger.info("Gateway shutting down.")

app = FastAPI(title="Macro OS Gateway", version="5.0.0", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "operational", "mode": "data_collection_v5", "events_db": str(DB_PATH)}

@app.post("/webhook/tv_macro")
async def receive_webhook(request: Request):
    """Ingest TradingView webhook payload into the event ledger."""
    payload = await request.json()
    schema_ver = payload.get("schema_ver", "1.0")
    if schema_ver not in SUPPORTED_SCHEMA_VERSIONS:
        return JSONResponse(status_code=400, content={"status": "rejected", "reason": "unsupported_schema_version"})

    event_id = payload.get("snapshot_id")
    priority = payload.get("priority", "SCHEDULED")
    now = time.time()
    danger = payload.get("nq_macro_layer", {}).get("danger_score", 0)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status FROM events WHERE event_id=?", (event_id,))
        if await cur.fetchone():
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": "already_in_ledger"})

        if priority == "SCHEDULED":
            cooldown = await get_adaptive_cooldown(db, danger)
            if cooldown > 0:
                await db.execute(
                    "INSERT INTO events (event_id,priority,status,reason,received_at,schema_ver) VALUES (?,?,'DISCARDED','COOLDOWN_ACTIVE',?,?)",
                    (event_id, priority, now, schema_ver),
                )
                await db.commit()
                return JSONResponse(status_code=200, content={"status": "discarded", "reason": "cooldown_active"})

        await db.execute(
            "INSERT INTO events (event_id,priority,status,received_at,payload,schema_ver) VALUES (?,?,'QUEUED',?,?,?)",
            (event_id, priority, now, json.dumps(payload), schema_ver),
        )
        await db.commit()

    return JSONResponse(status_code=200, content={"status": "queued", "event_id": event_id})