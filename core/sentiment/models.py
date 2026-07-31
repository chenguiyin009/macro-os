"""Sentiment shadow snapshot models (V2.0)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CycleBlock:
    t0_event: str = "META_HARDWARE_SHOCK"
    t0_date: Optional[str] = None
    drawdowns: Dict[str, Optional[float]] = field(default_factory=dict)
    stage: str = "UNKNOWN"
    stage_runner_up: Optional[str] = None
    stage_confidence: float = 0.0
    rebound_type: str = "UNKNOWN"
    stage_switch_signals: List[str] = field(default_factory=list)
    days_since_t0: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SentimentShadowSnapshot:
    schema_version: str = "shadow-sentiment-2.0"
    market_scope: str = "CN"
    as_of: str = ""
    session: str = "MANUAL"
    quality: str = "OK"
    confidence: float = 0.0
    ttl_seconds: int = 1800
    source: str = "MOCK"
    inputs_used: List[str] = field(default_factory=list)
    missing_inputs: List[str] = field(default_factory=list)

    cycle: CycleBlock = field(default_factory=CycleBlock)

    sse_anchor_px: float = 3750.0
    sse_last: Optional[float] = None
    sse_anchor_distance_bp: Optional[float] = None
    sse_anchor_state: str = "UNKNOWN"
    anchor_hold_quality: Optional[float] = None
    market_regime_label: str = "UNKNOWN"
    cross_section_corr_tech: Optional[float] = None
    crowding_unwind_score: Optional[float] = None

    margin_top100_limit_down_n: Optional[int] = None
    margin_top100_open_board_n: Optional[int] = None
    margin_top100_new_limit_down_n: Optional[int] = None
    margin_list_as_of: Optional[str] = None
    # board-behavior substitute probe (tdx_screener): consecutive limit-up/down + open-board.
    # These are a BROAD panic/forced-sell proxy that replaces the unobtainable margin Top100 universe.
    limit_down_consecutive_n: Optional[int] = None
    limit_down_open_n: Optional[int] = None
    limit_down_new_n: Optional[int] = None
    limit_up_consecutive_n: Optional[int] = None
    limit_up_open_n: Optional[int] = None
    deleveraging_stress_index: Optional[float] = None
    deleveraging_release_rate: Optional[float] = None
    forced_selling_proxy: Optional[float] = None

    intervention_mode: str = "UNKNOWN"
    gjd_proxy_score: Optional[float] = None
    etf_premium_proxy: Optional[float] = None
    star_chinext_etf_bid: Optional[float] = None
    csi1000_liquidity_injection: Optional[float] = None
    transmission_hs300: Optional[float] = None
    transmission_chinext: Optional[float] = None
    transmission_csi1000: Optional[float] = None
    liquidity_transmission_score: Optional[float] = None
    liquidity_transmission_stage: str = "UNKNOWN"
    transmission_lag_days: Optional[float] = None

    volume_thrust: Optional[float] = None
    advance_decline_tech: Optional[float] = None
    limit_stress: Optional[float] = None
    limit_down_count: Optional[int] = None
    intraday_path: str = "UNKNOWN"
    gap_open_ret: Optional[float] = None
    gap_hold_score: Optional[float] = None

    theme_dispersion: Optional[float] = None
    rotation_speed: Optional[float] = None
    ipo_catalyst_heat: Optional[float] = None
    equal_weight_tech_vs_cap: Optional[float] = None
    beta_first_preference: Optional[float] = None

    kr_semi_ret: Optional[float] = None
    us_mega_tech_path: Optional[str] = None
    kr_a_beta: Optional[float] = None
    kr_a_divergence: Optional[str] = None
    overseas_vs_cn_compute_rel: Optional[float] = None
    global_semi_drawdowns: Dict[str, Optional[float]] = field(default_factory=dict)
    global_sync_score: Optional[float] = None
    a_vs_global_rel: Optional[str] = None

    earnings_surprise_breadth: Optional[float] = None
    earnings_gap_fade: Optional[float] = None
    blood_chip_harvest: Optional[float] = None
    geopolitics_fear_proxy: Optional[float] = None

    growth_etf_flow: Optional[float] = None
    liquidity_pulse: Optional[float] = None
    hk_repair_relative: Optional[float] = None

    sentiment_temperature: float = 50.0
    structure_quality: str = "MIXED"
    bounce_quality: str = "NONE"
    rescue_sequence_stage: str = "SELLDOWN"
    rescue_playbook_match: str = "UNKNOWN"
    sector_index_resonance: Optional[float] = None
    ars: Optional[float] = None

    corroboration_flags: List[str] = field(default_factory=list)
    narrative_bullets: List[str] = field(default_factory=list)
    watch_checklist: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class SentimentRawInput:
    """Provider-normalized raw inputs. Missing fields stay None."""

    as_of: str
    session: str = "MANUAL"
    source: str = "MOCK"

    sse_last: Optional[float] = None
    volume_thrust: Optional[float] = None
    advance_decline_tech: Optional[float] = None
    limit_stress: Optional[float] = None
    limit_down_count: Optional[int] = None
    gap_open_ret: Optional[float] = None
    gap_hold_score: Optional[float] = None
    intraday_path: Optional[str] = None

    margin_top100_limit_down_n: Optional[int] = None
    margin_top100_open_board_n: Optional[int] = None
    margin_top100_new_limit_down_n: Optional[int] = None
    margin_list_as_of: Optional[str] = None
    # board-behavior substitute probe (tdx_screener) — see Snapshot mirror above.
    limit_down_consecutive_n: Optional[int] = None
    limit_down_open_n: Optional[int] = None
    limit_down_new_n: Optional[int] = None
    limit_up_consecutive_n: Optional[int] = None
    limit_up_open_n: Optional[int] = None
    forced_selling_proxy: Optional[float] = None

    gjd_proxy_score: Optional[float] = None
    star_chinext_etf_bid: Optional[float] = None
    csi1000_liquidity_injection: Optional[float] = None
    etf_premium_proxy: Optional[float] = None
    intervention_hint: Optional[str] = None

    transmission_hs300: Optional[float] = None
    transmission_chinext: Optional[float] = None
    transmission_csi1000: Optional[float] = None
    transmission_lag_days: Optional[float] = None

    cross_section_corr_tech: Optional[float] = None
    theme_dispersion: Optional[float] = None
    rotation_speed: Optional[float] = None
    sector_index_resonance: Optional[float] = None
    volume_for_bounce: Optional[float] = None

    kr_semi_ret: Optional[float] = None
    us_mega_tech_path: Optional[str] = None
    kr_a_divergence: Optional[str] = None
    drawdown_soxx: Optional[float] = None
    drawdown_kr_semi: Optional[float] = None
    drawdown_a_semi: Optional[float] = None
    t0_date: Optional[str] = None

    growth_etf_flow: Optional[float] = None
    liquidity_pulse: Optional[float] = None
    hk_repair_relative: Optional[float] = None
    ipo_catalyst_heat: Optional[float] = None
    no_mainline_hint: Optional[bool] = None
