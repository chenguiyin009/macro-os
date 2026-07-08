"""Shared allocation helpers for Macro OS.

These helpers keep portfolio normalization and hard-cap logic consistent across
the runtime service and the core policy engine.
"""

from __future__ import annotations

from typing import Dict, Mapping, Set, Tuple

EPSILON = 1e-9


def normalize_allocation(
    alloc_dict: Mapping[str, float],
    allowed: Set[str],
    cash_asset: str = "CASH",
) -> Dict[str, float]:
    """Normalize an allocation without inflating under-allocated proposals.

    Behavior:
    - negative values are clamped to zero
    - when total < 1.0, the remainder is assigned to cash
    - when total > 1.0, the entire allocation is proportionally scaled down
    """

    if cash_asset not in allowed:
        raise ValueError(f"cash asset '{cash_asset}' must be included in allowed assets")

    clamped = {asset: max(0.0, float(alloc_dict.get(asset, 0.0))) for asset in allowed}
    total = sum(clamped.values())

    if total <= EPSILON:
        return {asset: (1.0 if asset == cash_asset else 0.0) for asset in allowed}

    if total > 1.0 + EPSILON:
        normalized = {asset: clamped[asset] / total for asset in allowed}
    else:
        normalized = dict(clamped)
        normalized[cash_asset] = normalized.get(cash_asset, 0.0) + (1.0 - total)

    rounded = {asset: round(normalized.get(asset, 0.0), 4) for asset in allowed if asset != cash_asset}
    rounded[cash_asset] = round(max(0.0, 1.0 - sum(rounded.values())), 4)
    return rounded


def cap_group_exposure(
    target_dict: Mapping[str, float],
    group_assets: Set[str],
    max_exposure: float,
    cash_asset: str = "CASH",
) -> Tuple[Dict[str, float], float]:
    """Scale down a group of assets to a maximum exposure and route overflow to cash."""

    if max_exposure < 0.0:
        raise ValueError("max_exposure must be non-negative")

    capped = {asset: max(0.0, float(weight)) for asset, weight in target_dict.items()}
    capped.setdefault(cash_asset, 0.0)
    for asset in group_assets:
        capped.setdefault(asset, 0.0)

    group_total = sum(capped.get(asset, 0.0) for asset in group_assets)
    if group_total <= max_exposure + EPSILON:
        return capped, group_total

    scale = max_exposure / group_total if group_total > EPSILON else 0.0
    overflow = 0.0
    for asset in group_assets:
        original = capped.get(asset, 0.0)
        adjusted = original * scale
        overflow += original - adjusted
        capped[asset] = adjusted

    capped[cash_asset] = capped.get(cash_asset, 0.0) + overflow
    return capped, max_exposure
