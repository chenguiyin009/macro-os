"""config/config_loader.py ? Load hard_constraints.yaml with type safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


class Constraints(BaseModel):
    """Typed wrapper around hard_constraints.yaml."""
    version: str = "5.0"
    portfolio_limits: Dict[str, Any] = Field(default_factory=dict)
    circuit_breakers: Dict[str, Any] = Field(default_factory=dict)
    reconciliation: Dict[str, Any] = Field(default_factory=dict)
    constitution: Dict[str, Any] = Field(default_factory=dict)
    sector_allocator: Dict[str, Any] = Field(default_factory=dict)
    supported_assets: Dict[str, Any] = Field(default_factory=dict)


_cache: Optional[Constraints] = None


def load_hard_constraints(path: Optional[str] = None) -> Constraints:
    """Load YAML and return a typed Constraints object."""
    global _cache
    if path is None:
        path = str(Path(__file__).resolve().parent / "hard_constraints.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    result = Constraints(**raw)
    _cache = result
    return result


# Singleton loaded at import time
constraints: Constraints = load_hard_constraints()
