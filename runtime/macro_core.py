
import asyncio, time, json, yaml, hashlib, os, copy
from dataclasses import dataclass
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
import aiosqlite
import sys
import httpx

from core.allocation_utils import cap_group_exposure, normalize_allocation

# ================ Version Governance ====================
SYSTEM_VERSIONS = {"event_schema": "v5.1", "allocation_engine": "v2.0", "prompt_version": "v1.3_no_pm"}
DECISION_JOURNAL_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "vault", "DECISION_JOURNAL.jsonl"))
DB_PATH = "macro_os_ledger.db"
MAX_RETRIES = 3
EMERGENCY_COOLDOWN_SEC = 900
SUPPORTED_SCHEMA_VERSIONS = ["1.0", "1.1"]

# ================ Hard Constraints ======================
ALLOWED_ASSETS = {"QQQ", "CASH", "GLD", "THEME_DEF", "TLT", "HYG", "SPY"}
FINANCIAL_ASSETS = {"QQQ", "GLD", "TLT", "HYG", "SPY"}
CASH_ASSET = "CASH"
DEFENSIVE_ASSETS = {"CASH", "GLD", "THEME_DEF"}
MAX_EQUITY_EXPOSURE = 0.80
MIN_CASH_BUFFER = 0.05
MAX_DAILY_TURNOVER = 0.25
MAX_CONSECUTIVE_VIOLATIONS = 5

# ================ Circuit Breaker State =================
EQUITY_ASSETS = {"QQQ", "SPY"}
SYSTEM_STATE_TABLE = "system_state"
SYSTEM_STATE_KEY_BREAKER = "consecutive_violations"


@dataclass
class CircuitBreakerState:
    consecutive_violations: int = 0

# ================ Pydantic Models =======================
class RegimeName(str, Enum):
    AI_EXPANSION = "AI_EXPANSION"
    NARROW_LEADERSHIP = "NARROW_LEADERSHIP"
    FAST_LIQUIDITY_SHOCK = "FAST_LIQUIDITY_SHOCK"
    CASH_LIQUIDATION = "CASH_LIQUIDATION"


class SentinelPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_: str = Field("macro_os.v5", alias="schema")
    script: str = "Global Sentinel"
    composite_regime: str = "NEUTRAL"
    danger_regime: str = "NEUTRAL"
    gold_regime: str = "Neutral"
    composite_score: float = 0.0
    m1_score: int = 0
    m2_score: int = 0
    m3_score: int = 0
    danger_score: int = 0
    vix: Optional[float] = None
    dxy: Optional[float] = None
    gld: Optional[float] = None
    qqq: Optional[float] = None
    hyg: Optional[float] = None
    jnk: Optional[float] = None
    tip: Optional[float] = None
    spy: Optional[float] = None
    close: Optional[float] = None
    time: str = ""


# ================ Action Mapping ========================
REGIME_ACTION = {
    "CRISIS": ("RISK_REDUCE", 0.0),
    "FAST_SHOCK": ("RISK_REDUCE", 0.0),
    "ELEVATED": ("REDUCE", 0.50),
    "WATCH": ("NEUTRAL", 0.75),
    "NEUTRAL": ("NEUTRAL", 1.0),
}




class AllocationItem(BaseModel):
    asset: str = Field(..., description="Must be in ALLOWED_ASSETS")
    target_weight: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0, description="Conviction level")
    rebalance_days: int = Field(ge=1, le=10, description="Suggested execution window")

class LLMProposal(BaseModel):
    regime_identified: RegimeName
    macro_narrative: str
    allocations: List[AllocationItem]

# ================ Validation ============================
def validate_proposal(allocations: List[dict]) -> bool:
    for item in allocations:
        if item.get("asset") not in ALLOWED_ASSETS:
            return False
        w = item.get("target_weight", 0)
        if not (0 <= w <= 1):
            return False
    return True

# ================ Allocation Engine (Turnover Constitution) ===
def allocation_engine(proposal: LLMProposal, current_state: dict, tv_payload: dict, consecutive_violations: int = 0) -> tuple[dict, int]:
    danger_score = tv_payload.get("nq_macro_layer", {}).get("danger_score", 0.0)
    fragility = tv_payload.get("fragility_layer", {}).get("fragility_score", 0)
    current_weights = normalize_allocation(current_state.get("weights", {"CASH": 1.0}), ALLOWED_ASSETS, CASH_ASSET)

    target_dict = {asset: 0.0 for asset in ALLOWED_ASSETS}
    for item in proposal.allocations:
        if item.asset in ALLOWED_ASSETS:
            target_dict[item.asset] = target_dict.get(item.asset, 0.0) + item.target_weight
    target_dict = normalize_allocation(target_dict, ALLOWED_ASSETS, CASH_ASSET)

    # Hard Caps
    actual_max_equity = 0.15 if danger_score >= 75 else MAX_EQUITY_EXPOSURE
    target_dict, _ = cap_group_exposure(target_dict, EQUITY_ASSETS, actual_max_equity, CASH_ASSET)
    if target_dict.get("CASH", 0) < MIN_CASH_BUFFER:
        shortfall = MIN_CASH_BUFFER - target_dict["CASH"]
        target_dict["CASH"] = MIN_CASH_BUFFER
        target_dict["QQQ"] = max(0.0, target_dict.get("QQQ", 0) - shortfall)
    if fragility >= 8:
        target_dict["THEME_DEF"] = max(target_dict.get("THEME_DEF", 0), 0.30)

    target_dict = normalize_allocation(target_dict, ALLOWED_ASSETS, CASH_ASSET)

    # Constitution Violation Check
    if danger_score >= 75 and target_dict.get("QQQ", 0) > 0.10:
        consecutive_violations += 1
    elif fragility >= 8 and target_dict.get("CASH", 0) < 0.40:
        consecutive_violations += 1
    else:
        consecutive_violations = 0

    if consecutive_violations >= MAX_CONSECUTIVE_VIOLATIONS:
        target_dict = {asset: (1.0 if asset == CASH_ASSET else 0.0) for asset in ALLOWED_ASSETS}
        target_dict = normalize_allocation(target_dict, ALLOWED_ASSETS, CASH_ASSET)

    # Turnover Constitution
    turnover = sum(abs(target_dict.get(a, 0) - current_weights.get(a, 0)) for a in ALLOWED_ASSETS) / 2.0
    approved = {}
    if turnover > MAX_DAILY_TURNOVER:
        sf = MAX_DAILY_TURNOVER / turnover
        for a in ALLOWED_ASSETS:
            d = target_dict.get(a, 0) - current_weights.get(a, 0)
            approved[a] = current_weights.get(a, 0) + d * sf
        audit = f"Turnover clamped (target {turnover:.2f} > {MAX_DAILY_TURNOVER}) scale={sf:.2f}"
    else:
        approved = copy.deepcopy(target_dict)
        audit = f"Turnover OK ({turnover:.2f} <= {MAX_DAILY_TURNOVER})"

    approved = normalize_allocation(approved, ALLOWED_ASSETS, CASH_ASSET)

    return {
        "macro_state_summary": {"timestamp": tv_payload.get("timestamp"), "diagnosis": proposal.macro_narrative,
                                "regime": proposal.regime_identified.value, "constitution_audit": audit},
        "target_allocation": approved,
        "risk_control": {"hard_cap_active": bool(danger_score >= 75), "max_total_exposure": actual_max_equity,
                         "actual_turnover": min(turnover, MAX_DAILY_TURNOVER)}
    }, consecutive_violations

# ================ DB & Worker ===========================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, priority TEXT, status TEXT, reason TEXT, received_at REAL, started_at REAL, finished_at REAL, retry_count INTEGER DEFAULT 0, payload JSON, schema_ver TEXT DEFAULT '1.0')")
        await db.execute("CREATE TABLE IF NOT EXISTS decision_trace (event_id TEXT PRIMARY KEY, timestamp REAL, llm_proposal JSON, policy_result JSON, final_yaml TEXT, FOREIGN KEY(event_id) REFERENCES events(event_id))")
        await db.execute(f"CREATE TABLE IF NOT EXISTS {SYSTEM_STATE_TABLE} (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_events_received ON events(received_at)")
        await db.commit()


async def _get_system_state_int(db, key: str, default: int = 0) -> int:
    cursor = await db.execute(f"SELECT value FROM {SYSTEM_STATE_TABLE} WHERE key=?", (key,))
    row = await cursor.fetchone()
    if row is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


async def _set_system_state_int(db, key: str, value: int) -> None:
    await db.execute(
        f"INSERT INTO {SYSTEM_STATE_TABLE} (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, int(value)),
    )


def _write_watchlist_atomically(yaml_content: str, event_id: str) -> None:
    tmp = f"watchlist_{event_id}.yaml"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(yaml_content)
    os.replace(tmp, "current_watchlist.yaml")


def _load_decision_journal_event_ids() -> set[str]:
    event_ids: set[str] = set()
    if not os.path.exists(DECISION_JOURNAL_PATH):
        return event_ids
    with open(DECISION_JOURNAL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = entry.get("event_id")
            if event_id:
                event_ids.add(event_id)
    return event_ids

async def macro_worker():
    breaker_state = CircuitBreakerState()
    async with aiosqlite.connect(DB_PATH) as db:
        breaker_state.consecutive_violations = await _get_system_state_int(db, SYSTEM_STATE_KEY_BREAKER, 0)
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("SELECT * FROM events WHERE status='QUEUED' OR (status='FAILED' AND retry_count<?) ORDER BY priority='EMERGENCY' DESC, received_at ASC LIMIT 1", (MAX_RETRIES,))
                task = await cursor.fetchone()
                if not task:
                    await asyncio.sleep(1); continue
                eid = task["event_id"]
                payload = json.loads(task["payload"])
                retry = task["retry_count"]
                await db.execute("UPDATE events SET status='PROCESSING', started_at=? WHERE event_id=?", (time.time(), eid))
                await db.commit()
                try:
                    current_state = {"weights": {"CASH": 0.60, "QQQ": 0.30, "HYG": 0.10, "GLD": 0.0, "THEME_DEF": 0.0}}
                    mock = {"regime_identified": "FAST_LIQUIDITY_SHOCK", "macro_narrative": "Liquidity??",
                            "allocations": [{"asset": "QQQ", "target_weight": 0.80, "confidence": 0.7, "rebalance_days": 3}]}
                    proposal = LLMProposal(**mock)
                    approved, breaker_state.consecutive_violations = allocation_engine(
                        proposal,
                        current_state,
                        payload,
                        breaker_state.consecutive_violations,
                    )
                    await _set_system_state_int(db, SYSTEM_STATE_KEY_BREAKER, breaker_state.consecutive_violations)
                    await db.commit()
                    yaml_content = yaml.dump(approved, allow_unicode=True, sort_keys=False)
                    await asyncio.to_thread(_write_watchlist_atomically, yaml_content, eid)
                    await db.execute("INSERT INTO decision_trace (event_id,timestamp,llm_proposal,policy_result,final_yaml) VALUES (?,?,?,?,?)",
                                     (eid, time.time(), json.dumps(mock), json.dumps(approved), yaml_content))
                    await db.execute("UPDATE events SET status='SUCCESS', finished_at=? WHERE event_id=?", (time.time(), eid))
                    await db.commit()
                except Exception as e:
                    new_retry = retry + 1
                    ns = "FAILED_PERMANENTLY" if new_retry >= MAX_RETRIES else "FAILED"
                    await db.execute("UPDATE events SET status=?, retry_count=?, reason=? WHERE event_id=?", (ns, new_retry, str(e), eid))
                    await db.commit()
        except Exception:
            await asyncio.sleep(2)


async def get_adaptive_cooldown(db, new_danger: float) -> int:
    cursor = await db.execute("SELECT payload FROM events WHERE priority='EMERGENCY' ORDER BY received_at DESC LIMIT 1")
    last = await cursor.fetchone()
    if not last:
        return 0
    last_payload = json.loads(last[0])
    last_danger = last_payload.get("nq_macro_layer", {}).get("danger_score", 0)
    return 0 if new_danger > last_danger else EMERGENCY_COOLDOWN_SEC


@asynccontextmanager
async def lifespan(app):
    await init_db()
    t = asyncio.create_task(macro_worker())
    yield
    t.cancel()




# ================ Sentinel Helpers ======================
DECISION_JOURNAL_LOCK = asyncio.Lock()

def _write_decision_journal(entry: dict) -> None:
    os.makedirs(os.path.dirname(DECISION_JOURNAL_PATH), exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with open(DECISION_JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def _compute_sentinel_event_id(payload: dict) -> str:
    content = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]

def _derive_action(composite_score: float, composite_regime: str, danger_score: int) -> dict:
    action, budget = REGIME_ACTION.get(composite_regime, ("NEUTRAL", 1.0))
    if composite_regime == "WATCH" and danger_score >= 3:
        action, budget = "REDUCE", 0.50
    if composite_regime == "NEUTRAL" and composite_score > 0.4:
        budget = 0.85
    return {"action": action, "budget": budget}

def _build_sentinel_card(payload: dict, derived: dict) -> dict:
    regime = payload.get("composite_regime", "NEUTRAL")
    color_map = {"CRISIS": "red", "FAST_SHOCK": "red", "ELEVATED": "orange", "WATCH": "yellow", "NEUTRAL": "green"}
    color = color_map.get(regime, "blue")
    action = derived.get("action", "NEUTRAL")
    budget = derived.get("budget", 1.0)

    def kv(key, val):
        return {"tag": "div", "text": {"tag": "lark_md", "content": f"**{key}:** {val}"}}

    def f(v, fmt=".1f"):
        return f"{v:{fmt}}" if v is not None else "?"

    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**Regime:** {regime}  |  **Action:** {action}  |  **Budget:** {budget*100:.0f}%"}},
        {"tag": "hr"},
        kv("Composite Score", f"{payload.get('composite_score', 0):.2f}/1.00"),
        kv("Danger Score", f"{payload.get('danger_score', 0)}/7"),
        {"tag": "hr"},
        kv("Modules", f"M1={payload.get('m1_score','?')}  M2={payload.get('m2_score','?')}  M3={payload.get('m3_score','?')}"),
        kv("Market", f"VIX={f(payload.get('vix'))}  DXY={f(payload.get('dxy'))}  QQQ={f(payload.get('qqq'))}"),
        kv("", f"HYG={f(payload.get('hyg'))}  GLD={f(payload.get('gld'))}  TIP={f(payload.get('tip'))}"),
        {"tag": "hr"},
        kv("Danger Regime", payload.get("danger_regime", "?")),
        kv("Gold Regime", payload.get("gold_regime", "?")),
        kv("Script", f"{payload.get('script','?')}  |  {payload.get('time','?')[:19]}"),
    ]

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"Global Sentinel \u2014 {regime}"},
                "template": color,
            },
            "elements": elements,
        },
    }


app = FastAPI(lifespan=lifespan)
memory_lock = asyncio.Lock()
@app.post("/webhook/global-sentinel")
async def receive_global_sentinel(request: Request):
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "reason": "invalid_json"})

    try:
        sentinel = SentinelPayload(**raw)
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "reason": f"validation_failed: {e}"})

    payload = sentinel.model_dump(by_alias=True)
    event_id = _compute_sentinel_event_id(payload)

    async with DECISION_JOURNAL_LOCK:
        existing_event_ids = await asyncio.to_thread(_load_decision_journal_event_ids)
        if event_id in existing_event_ids:
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": "duplicate_event"})

        derived = _derive_action(payload.get("composite_score", 0), payload.get("composite_regime", "NEUTRAL"), payload.get("danger_score", 0))
        entry = {"event_id": event_id, "ts": payload.get("time", ""), **payload, **derived}
        await asyncio.to_thread(_write_decision_journal, entry)

    feishu_url = os.environ.get("MACRO_OS_FEISHU_WEBHOOK_URL", "")
    if feishu_url:
        card = _build_sentinel_card(payload, derived)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(feishu_url, json=card)
                feishu_status = "sent" if resp.status_code == 200 else f"failed({resp.status_code})"
        except Exception as e:
            feishu_status = f"error({e})"
    else:
        feishu_status = "no_webhook"

    return JSONResponse(status_code=200, content={
        "status": "ok", "event_id": event_id,
        "action": derived.get("action"), "budget": derived.get("budget"),
        "feishu": feishu_status,
    })


@app.post("/webhook/tv_macro")
async def receive_webhook(request: Request):
    payload = await request.json()
    schema_ver = payload.get("schema_ver", "1.0")
    if schema_ver not in SUPPORTED_SCHEMA_VERSIONS:
        return JSONResponse(status_code=400, content={"status": "rejected", "reason": "unsupported_schema_version"})

    event_id = payload.get("snapshot_id")
    priority = payload.get("priority", "SCHEDULED")
    now = time.time()
    danger = payload.get("nq_macro_layer", {}).get("danger_score", 0)

    async with memory_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT status FROM events WHERE event_id=?", (event_id,))
            if await cur.fetchone():
                return JSONResponse(status_code=200, content={"status": "ignored", "reason": "already_in_ledger"})
            if priority == "SCHEDULED":
                cooldown = await get_adaptive_cooldown(db, danger)
                if cooldown > 0:
                    await db.execute("INSERT INTO events (event_id,priority,status,reason,received_at,schema_ver) VALUES (?,?,'DISCARDED','COOLDOWN_ACTIVE',?,?)",
                                     (event_id, priority, now, schema_ver))
                    await db.commit()
                    return JSONResponse(status_code=200, content={"status": "discarded", "reason": "cooldown_active"})
            await db.execute("INSERT INTO events (event_id,priority,status,received_at,payload,schema_ver) VALUES (?,?,'QUEUED',?,?,?)",
                             (event_id, priority, now, json.dumps(payload), schema_ver))
            await db.commit()
    return JSONResponse(status_code=200, content={"status": "queued", "event_id": event_id})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
