"""Core sector allocator - L4.5 QQQ budget splitter with EMA smoothing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from config.config_loader import constraints

logger = logging.getLogger(__name__)

TECH_SECTORS: list[str] = [
    "TECH_HW", "TECH_HYPER", "TECH_AI_APP",
    "TECH_OPTICAL", "TECH_POWER", "TECH_MEMORY", "TECH_NET",
]

_VAULT_PATH = Path(__file__).resolve().parent.parent.parent / "vault" / "sector_state.json"


class SectorAllocator:
    """L4.5 sector allocator ? EMA smoothing + IPS compliance."""

    def __init__(self, previous_allocations: Optional[Dict[str, float]] = None):
        self.previous_allocations = previous_allocations or {s: 0.0 for s in TECH_SECTORS}
        sa = constraints.sector_allocator
        self._alpha = float(sa.get("ema_smoothing_alpha", 0.3))
        self._min_score = float(sa.get("min_diffusion_score_to_allocate", 45))
        self._max_cap = float(sa.get("single_stock_max_weight", 0.40))

    def allocate_tech_budget(
        self, total_qqq_budget: float, diffusion_scores: Dict[str, float]
    ) -> Dict[str, float]:
        """Run the full pipeline: raw signal -> EMA -> IPS -> valid weights."""
        raw = self._compute_raw_weights(total_qqq_budget, diffusion_scores)
        ema = self._apply_ema(raw)
        ips = self._compile_ips_constraints(ema)
        self.previous_allocations = ips.copy()
        return ips

    def _compute_raw_weights(self, budget: float, scores: Dict[str, float]) -> Dict[str, float]:
        """Step 1: proportional allocation from valid diffusion scores."""
        valid = {k: v for k, v in scores.items() if k in TECH_SECTORS and v >= self._min_score}
        total = sum(valid.values())
        return {sec: (valid[sec] / total * budget) if total > 0 and sec in valid else 0.0 for sec in TECH_SECTORS}

    def _apply_ema(self, raw_weights: Dict[str, float]) -> Dict[str, float]:
        """Step 2: exponential moving average across sessions."""
        return {
            sec: round(self._alpha * raw_weights.get(sec, 0.0) + (1.0 - self._alpha) * self.previous_allocations.get(sec, 0.0), 6)
            for sec in TECH_SECTORS
        }

    def _compile_ips_constraints(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Step 3: cap single-sector at max_cap; redistribute excess proportionally."""
        clamped = weights.copy()
        excess = sum(max(clamped.get(sec, 0.0) - self._max_cap, 0.0) for sec in TECH_SECTORS)
        for sec in TECH_SECTORS:
            if clamped.get(sec, 0.0) > self._max_cap:
                clamped[sec] = self._max_cap
        if excess > 1e-9:
            avail = [s for s in TECH_SECTORS if 0 < clamped.get(s, 0.0) < self._max_cap]
            total_avail = sum(clamped[s] for s in avail)
            for sec in avail:
                share = clamped[sec] / total_avail if total_avail > 0 else 1.0 / len(avail)
                clamped[sec] = round(clamped[sec] + share * excess, 6)
        return clamped

    def save_state(self, path: str = "") -> None:
        p = Path(path) if path else _VAULT_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.previous_allocations, f)
        logger.info("Sector state saved to %s", p)

    def load_state(self, path: str = "") -> None:
        p = Path(path) if path else _VAULT_PATH
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k in TECH_SECTORS:
                    if k in data:
                        self.previous_allocations[k] = float(data[k])
            logger.info("Sector state loaded from %s", p)