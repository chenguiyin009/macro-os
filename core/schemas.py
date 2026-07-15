"""Macro OS core schemas and compatibility shims."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, Field, ConfigDict, model_validator


class RegimeName(str, Enum):
    AI_EXPANSION = "AI_EXPANSION"
    NARROW_LEADERSHIP = "NARROW_LEADERSHIP"
    FAST_LIQUIDITY_SHOCK = "FAST_LIQUIDITY_SHOCK"
    CASH_LIQUIDATION = "CASH_LIQUIDATION"


class RegimeType(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    CHOPPY = "CHOPPY"
    TRANSITION = "TRANSITION"
    RISK_ON = "RISK_ON"
    TIGHT_LIQUIDITY = "TIGHT_LIQUIDITY"
    LIQUIDITY_SQUEEZE = "LIQUIDITY_SQUEEZE"
    AI_EXPANSION = "AI_EXPANSION"
    NARROW_LEADERSHIP = "NARROW_LEADERSHIP"
    FAST_LIQUIDITY_SHOCK = "FAST_LIQUIDITY_SHOCK"
    CASH_LIQUIDATION = "CASH_LIQUIDATION"


class DecisionAction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"
    REDUCE = "REDUCE"
    RISK_REDUCE = "RISK_REDUCE"
    AGGRESSIVE = "AGGRESSIVE"
    DEFENSIVE = "DEFENSIVE"
    NEUTRAL = "NEUTRAL"
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    ACCUMULATE = "ACCUMULATE"
    LIQUIDATE = "LIQUIDATE"


class AuthorityLevel(str, Enum):
    SOFT_POLICY = "SOFT_POLICY"
    SAFETY_GATE = "SAFETY_GATE"
    HARD_VETO = "HARD_VETO"


class SystemState(str, Enum):
    LOW = "LOW"
    MID = "MID"
    HIGH = "HIGH"
    ACTIVE = "ACTIVE"
    DEFENSIVE = "DEFENSIVE"
    DEGRADED = "DEGRADED"


class DataSource(str, Enum):
    MCP = "MCP"
    MOCK = "MOCK"
    MANUAL = "MANUAL"


class SoftRegimeProbs(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    risk_on: float = 0.25
    tight_liquidity: float = 0.25
    liquidity_squeeze: float = 0.25
    transition: float = 0.25


class AttributionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    feature_error: float = 0.0
    regime_error: float = 0.0
    execution_cost: float = 0.0
    total_error: float = 0.0


class CounterfactualResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    decision: str = ""
    predicted_pnl: float = 0.0
    confidence: float = 0.0
    risk_score: float = 0.0


class HMMInferenceResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    probs: Dict[str, float] = Field(default_factory=dict)
    predicted_regime: str = RegimeType.TRANSITION.value
    confidence: float = 0.25
    reason: str = ""


class SafetyGateResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    state: SystemState = SystemState.ACTIVE
    reason: str = ""
    di: float = 0.0
    mdi: float = 0.0
    pnl_stability: float = 0.5


class StabilityMetrics(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    dpe: float = 0.0
    max_dpe: float = 0.0
    flip_count: int = 0
    flip_rate: float = 0.0
    unstable: bool = False


class LedgerSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    valid: bool = True
    total_events: int = 0
    unique_events: int = 0
    duplicate_ids: List[str] = Field(default_factory=list)
    schema_errors: List[Dict[str, Any]] = Field(default_factory=list)


class PineConclusionSchema(BaseModel):
    """Structured conclusion extracted from a TradingView Pine script.

    Produced by relay/pine-bridge.mjs (CDP bridge) and consumed by the
    PineAnalysisNode / decision kernel as a confirmation / divergence signal.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    source_script: str = ""
    symbol: str = ""
    tf: str = ""
    chart_title: str = ""
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signal: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    value: Optional[float] = None
    label: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class FeatureSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    dxy: Optional[float] = None
    vix: Optional[float] = None
    ovx: Optional[float] = Field(None, validation_alias=AliasChoices("ovx", "oil_vix"))
    gold: Optional[float] = Field(None, validation_alias=AliasChoices("gold", "gld"))
    tips_yield: Optional[float] = Field(None, validation_alias=AliasChoices("tips_yield", "tip"))
    # Nominal UST curve (percent points, e.g. 4.55 == 4.55%) for funding-price research quadrant.
    nominal_10y: Optional[float] = Field(
        None, validation_alias=AliasChoices("nominal_10y", "ust_10y", "us10y")
    )
    nominal_30y: Optional[float] = Field(
        None, validation_alias=AliasChoices("nominal_30y", "ust_30y", "us30y")
    )
    nominal_2y: Optional[float] = Field(
        None, validation_alias=AliasChoices("nominal_2y", "ust_2y", "us2y")
    )
    bei_10y: Optional[float] = Field(
        None, validation_alias=AliasChoices("bei_10y", "breakeven_10y", "us_bei_10y")
    )
    # Optional short-window changes in basis points (research / weekly snapshots).
    tips_yield_change_5d_bp: Optional[float] = None
    nominal_10y_change_5d_bp: Optional[float] = None
    nominal_30y_change_5d_bp: Optional[float] = None
    hy_credit_spread: Optional[float] = Field(
        None,
        validation_alias=AliasChoices("hy_credit_spread", "hyg", "jnk"),
    )
    qqq_close: Optional[float] = Field(
        None,
        validation_alias=AliasChoices("qqq_close", "qqq", "close"),
    )
    spy_close: Optional[float] = Field(None, validation_alias=AliasChoices("spy_close", "spy"))
    equity_tech_rotation: Optional[float] = None
    tips_yield_roc_60d: Optional[float] = None
    dxy_zscore_60d: Optional[float] = None
    # core_pce 为**百分比**量纲（例如 3.5 表示 3.5%），与
    # config/thresholds.yaml 的 constitution.red_lines.core_pce_max（3.5）
    # 以及 policy_engine 的 core_pce 通道保持一致。
    # 注意：core/glen_red_lines.py 历史使用小数（0.034/0.035），
    # 属独立子系统，量纲分歧待其 owner 统一，勿在本字段混用小数。
    core_pce: Optional[float] = None
    danger_score: float = 0.0
    fragility_score: float = 0.0
    risk_score: float = 0.0
    recovery_signal: bool = False
    tech_rotation_layer: Dict[str, Any] = Field(default_factory=dict)
    pine: Optional["PineConclusionSchema"] = None
    source: DataSource = DataSource.MCP
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Decision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    action: DecisionAction = DecisionAction.NO_TRADE
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    regime: RegimeType = RegimeType.TRANSITION
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)


class KernelDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    authority: AuthorityLevel = AuthorityLevel.SOFT_POLICY
    decision: Decision = Field(default_factory=Decision)
    hard_regime: str = RegimeType.RISK_ON.value
    soft_regime_label: str = RegimeType.RISK_ON.value
    risk_budget: float = Field(default=0.5, ge=0.0, le=1.0)
    defense_budget: float = Field(default=0.5, ge=0.0, le=1.0)
    veto_reason: str = ""
    reason_code: str = ""
    regime_probs: Dict[RegimeName, float] = Field(default_factory=dict)
    audit_trail: Dict[str, Any] = Field(default_factory=dict)

    @property
    def final_risk_budget(self) -> float:
        return self.risk_budget

    @property
    def final_defense_budget(self) -> float:
        return self.defense_budget


class Event(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_id: str = ""
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    source: str
    symbol: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ensure_event_id(self) -> "Event":
        if not self.event_id:
            object.__setattr__(
                self,
                "event_id",
                compute_event_id(self.ts, self.source, self.symbol, self.event_type, self.payload),
            )
        return self

    def to_jsonl(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def from_jsonl(cls, line: str) -> "Event":
        return cls.model_validate(json.loads(line))


def compute_event_id(
    ts: str,
    source: str,
    symbol: str,
    event_type: str,
    payload: Any,
) -> str:
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    raw = "|".join([ts, source, symbol, event_type, canonical_payload])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
