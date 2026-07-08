import os
from enum import Enum
from typing import List, Literal, Dict, Optional
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, model_validator
import logging

logger = logging.getLogger(__name__)

# ================ Configuration & Paths =================
# 统一绝对路径管理
BASE_DIR = Path(__file__).resolve().parent.parent.parent
VAULT_DIR = BASE_DIR / "vault"
CONFIG_DIR = BASE_DIR / "config"
VAULT_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

DECISION_JOURNAL_PATH = VAULT_DIR / "DECISION_JOURNAL.jsonl"
DB_PATH = BASE_DIR / "data" / "macro_os_ledger.db"

# ================ Version Governance ====================
# 统一版本体系：使用 Semantic Versioning
SYSTEM_VERSIONS = {
    "event_schema": "5.1",
    "allocation_engine": "2.0",
    "prompt_version": "1.3"
}
SUPPORTED_SCHEMA_VERSIONS = ["5.0", "5.1"]

# ================ Hard Constraints ======================
# 建议：未来将这些常量迁移至 config/hard_constraints.yaml 实现单点事实(SSOT)
AssetLiteral = Literal["QQQ", "CASH", "GLD", "THEME_DEF", "TLT", "HYG", "SPY"]
ALLOWED_ASSETS = set(AssetLiteral.__args__)

# 注意：此网关层仅处理【宏观大类资产】。
# 科技子板块 (TECH_HW, TECH_AI_APP等) 由下游 FractureAwareSizer 根据 V2.4 架构处理。
EQUITY_ASSETS = {"QQQ", "SPY"}
DEFENSIVE_ASSETS = {"CASH", "GLD", "THEME_DEF", "TLT"}
CASH_ASSET = "CASH"

MAX_EQUITY_EXPOSURE = 0.80
MIN_CASH_BUFFER = 0.05
MAX_CONSECUTIVE_VIOLATIONS = 5

# ================ Pydantic Models =======================

class RegimeName(str, Enum):
    AI_EXPANSION = "AI_EXPANSION"
    NARROW_LEADERSHIP = "NARROW_LEADERSHIP"
    FAST_LIQUIDITY_SHOCK = "FAST_LIQUIDITY_SHOCK"
    CASH_LIQUIDATION = "CASH_LIQUIDATION"

# 建立 Enum 到风控动作的严格映射，消除断层
REGIME_ACTION = {
    RegimeName.CASH_LIQUIDATION: ("RISK_REDUCE", 0.0),
    RegimeName.FAST_LIQUIDITY_SHOCK: ("RISK_REDUCE", 0.0),
    RegimeName.NARROW_LEADERSHIP: ("REDUCE", 0.50),
    RegimeName.AI_EXPANSION: ("NEUTRAL", 1.0),
}

class SentinelPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # schema_ 格式期望如 "macro_os.v5.1"
    schema_: str = Field(..., alias="schema")
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
    time: str = Field(...) # 强制要求 time 字段

    @model_validator(mode='after')
    def validate_schema_version(self):
        # 提取版本号，例如从 "macro_os.v5.1" 提取 "5.1"
        try:
            version = self.schema_.split(".v")[-1]
            if version not in SUPPORTED_SCHEMA_VERSIONS:
                raise ValueError(f"Unsupported schema version: {version}. Expected one of {SUPPORTED_SCHEMA_VERSIONS}")
        except Exception as e:
            raise ValueError(f"Invalid schema format: {self.schema_}. Error: {e}")
        return self

class AllocationItem(BaseModel):
    # 强制白名单校验
    asset: AssetLiteral 
    target_weight: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)

class LLMProposal(BaseModel):
    regime_identified: RegimeName
    allocations: List[AllocationItem]

# ================ Core Validation Logic =================

def validate_proposal(proposal: LLMProposal) -> bool:
    """
    检查 LLM 提议是否满足系统的物理约束底线 (Hard Constraints)。
    返回 True 表示放行，False 表示触发熔断拦截。
    """
    allocations = proposal.allocations
    if not allocations:
        logger.error("Validation Failed: Empty allocations.")
        return False
        
    total_weight = sum(item.target_weight for item in allocations)
    total_equity = sum(item.target_weight for item in allocations if item.asset in EQUITY_ASSETS)
    total_cash = sum(item.target_weight for item in allocations if item.asset == CASH_ASSET)

    # 1. 权重归一化校验 (极小容忍度防浮点误差)
    if not (0.999 <= total_weight <= 1.001):
        logger.error(f"Validation Failed: Total weight is {total_weight:.4f}, must equal 1.0")
        return False
        
    # 2. 权益类敞口上限断路器 (防多头狂热)
    if total_equity > MAX_EQUITY_EXPOSURE:
        logger.error(f"Validation Failed: Equity exposure {total_equity:.4f} exceeds limit {MAX_EQUITY_EXPOSURE}")
        return False
        
    # 3. 现金安全垫底线 (防流动性枯竭)
    if total_cash < MIN_CASH_BUFFER:
        logger.error(f"Validation Failed: Cash buffer {total_cash:.4f} is below minimum {MIN_CASH_BUFFER}")
        return False
        
    # 4. 宏观逻辑一致性校验 (Macro-Consistency Check)
    # 确保在极端环境下，LLM 没有给出违背常识的激进提议
    expected_action, max_allowed_risk = REGIME_ACTION.get(proposal.regime_identified, ("NEUTRAL", 1.0))
    if total_equity > max_allowed_risk:
        logger.error(f"Validation Failed: Regime {proposal.regime_identified} limits equity to {max_allowed_risk:.2f}, but got {total_equity:.2f}")
        return False

    return True

# --- 测试 / 演示 ---
if __name__ == "__main__":
    # 模拟一个合法的 LLM 提议 (AI_EXPANSION)
    valid_proposal = LLMProposal(
        regime_identified=RegimeName.AI_EXPANSION,
        allocations=[
            AllocationItem(asset="QQQ", target_weight=0.70, confidence=0.8),
            AllocationItem(asset="TLT", target_weight=0.20, confidence=0.6),
            AllocationItem(asset="CASH", target_weight=0.10, confidence=1.0)
        ]
    )
    print(f"Valid Proposal check: {validate_proposal(valid_proposal)}") # 预期: True

    # 模拟一个违规的 LLM 提议 (在 CASH_LIQUIDATION 环境下满仓 QQQ)
    invalid_proposal = LLMProposal(
        regime_identified=RegimeName.CASH_LIQUIDATION,
        allocations=[
            AllocationItem(asset="QQQ", target_weight=0.90, confidence=0.9), # 违背最大敞口与环境限制
            AllocationItem(asset="CASH", target_weight=0.10, confidence=1.0)
        ]
    )
    print(f"Invalid Proposal check: {validate_proposal(invalid_proposal)}") # 预期: False (触发多条报错日志)