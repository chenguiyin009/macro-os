"""Macro OS v4.3 - Decision stability engine.

Computes DPE, flip rate with lag tolerance, duration-weighted transitions.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List

from core.schemas import StabilityMetrics


def compute_dpe(decisions: List[str], window: int = 20) -> float:
    recent = decisions[-window:] if len(decisions) > window else decisions
    if len(recent) < 2:
        return 0.0
    counts = Counter(recent)
    total = len(recent)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def count_flips(decisions: List[str], window: int = 20, lag_tolerance: int = 2) -> int:
    recent = decisions[-window:] if len(decisions) > window else decisions
    if len(recent) < 2:
        return 0
    flips = 0
    run_start = 0
    current = recent[0]
    for i in range(1, len(recent)):
        if recent[i] != current:
            if (i - run_start) >= lag_tolerance:
                flips += 1
            run_start = i
            current = recent[i]
    return flips


def compute_duration_weighted_entropy(decisions: List[str], window: int = 20, min_duration: int = 3) -> float:
    recent = decisions[-window:] if len(decisions) > window else decisions
    if len(recent) < 2:
        return 0.0
    weighted: Dict[str, float] = {}
    i = 0
    while i < len(recent):
        j = i
        while j < len(recent) and recent[j] == recent[i]:
            j += 1
        duration = j - i
        weight = min(1.0, duration / min_duration)
        weighted[recent[i]] = weighted.get(recent[i], 0.0) + weight
        i = j
    total = sum(weighted.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for w in weighted.values():
        p = w / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def detect_instability(
    decisions: List[str],
    dpe_threshold: float = 1.2,
    flip_count_threshold: int = 4,
    flip_rate_threshold: float = 0.3,
    window: int = 20,
    lag_tolerance: int = 2,
    min_duration: int = 3,
) -> StabilityMetrics:
    dur_dpe = compute_duration_weighted_entropy(decisions, window, min_duration)
    flip_count = count_flips(decisions, window, lag_tolerance)
    recent = decisions[-window:] if len(decisions) > window else decisions
    flip_rate = flip_count / max(len(recent) - 1, 1)
    unstable = (dur_dpe > dpe_threshold and flip_count > flip_count_threshold) or flip_rate > flip_rate_threshold
    return StabilityMetrics(
        dpe=round(dur_dpe, 4), max_dpe=round(dpe_threshold, 4),
        flip_count=flip_count, flip_rate=round(flip_rate, 4), unstable=unstable,
    )
