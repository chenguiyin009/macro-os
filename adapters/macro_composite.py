"""Multi-source macro snapshot merge + last-good local cache.

Priority (first non-null field wins unless prefer_high_quality overrides):
  explicit sources order provided by caller.

Quality preference for research-critical fields:
  tips_yield, bei_10y, hy_credit_spread (true OAS/bp) prefer FRED-like over ETF proxies.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.schemas import DataSource, FeatureSchema

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "macro_last_good.json"

# Fields where a "true" research series should not be overwritten by ETF proxies.
HIGH_QUALITY_FIELDS = {
    "tips_yield",
    "bei_10y",
    "hy_credit_spread",
    "tips_yield_change_5d_bp",
}


def _model_fields() -> List[str]:
    return [name for name in FeatureSchema.model_fields.keys() if name not in {"pine", "tech_rotation_layer"}]


def feature_to_dict(fs: FeatureSchema) -> Dict[str, Any]:
    data = fs.model_dump()
    # drop bulky/default noise
    return {k: v for k, v in data.items() if v is not None}


def merge_feature_snapshots(
    snapshots: Sequence[tuple[str, FeatureSchema]],
    *,
    prefer_high_quality_from: Optional[Sequence[str]] = None,
) -> Optional[FeatureSchema]:
    """Merge multiple FeatureSchema snapshots.

    snapshots: list of (source_name, schema), ordered from highest general priority to lowest.
    prefer_high_quality_from: source names whose high-quality fields should win even if later.
    """
    if not snapshots:
        return None

    prefer = set(prefer_high_quality_from or [])
    merged: Dict[str, Any] = {}
    provenance: Dict[str, str] = {}

    # First pass: fill in order
    for src_name, fs in snapshots:
        d = feature_to_dict(fs)
        for k, v in d.items():
            if k in {"source", "fetched_at"}:
                continue
            if k not in merged or merged[k] is None:
                merged[k] = v
                provenance[k] = src_name

    # Second pass: allow preferred sources to overwrite high-quality fields
    if prefer:
        for src_name, fs in snapshots:
            if src_name not in prefer:
                continue
            d = feature_to_dict(fs)
            for k, v in d.items():
                if k in HIGH_QUALITY_FIELDS and v is not None:
                    merged[k] = v
                    provenance[k] = src_name

    if not merged:
        return None

    merged["source"] = DataSource.MCP
    merged["fetched_at"] = datetime.now(timezone.utc)
    # stash provenance in equity_tech_rotation? No — keep clean. Caller logs provenance.
    try:
        fs = FeatureSchema(**merged)
    except Exception as exc:
        logger.warning("merge_feature_snapshots validation failed: %s", exc)
        return None
    # attach provenance dynamically for debugging (not a schema field)
    object.__setattr__(fs, "_field_provenance", provenance) if False else None
    fs.__dict__["_field_provenance"] = provenance  # type: ignore[attr-defined]
    return fs


def save_last_good(fs: FeatureSchema, path: Path = DEFAULT_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "features": feature_to_dict(fs),
    }
    # serialize datetime
    feats = payload["features"]
    if hasattr(feats.get("fetched_at"), "isoformat"):
        feats["fetched_at"] = feats["fetched_at"].isoformat()
    if hasattr(feats.get("source"), "value"):
        feats["source"] = feats["source"].value
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_last_good(path: Path = DEFAULT_CACHE_PATH) -> Optional[FeatureSchema]:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        feats = raw.get("features") or {}
        return FeatureSchema.model_validate(feats)
    except Exception as exc:
        logger.warning("load_last_good failed: %s", exc)
        return None


def fetch_merged_macro_snapshot(
    *,
    include_tv: bool = True,
    include_yfinance: bool = True,
    include_fred: bool = True,
    use_cache: bool = True,
    cache_path: Path = DEFAULT_CACHE_PATH,
    tv_adapter: Any = None,
    yf_adapter: Any = None,
    fred_adapter: Any = None,
) -> Optional[FeatureSchema]:
    """Fetch and merge macro snapshots from available sources.

    Order for general fields: TV, yfinance, FRED, cache.
    High-quality research fields prefer FRED when present.
    """
    snapshots: List[tuple[str, FeatureSchema]] = []

    if include_tv and tv_adapter is not None:
        try:
            # Avoid recursion: caller should pass a TV adapter method that only does MCP/relay
            tv_fs = tv_adapter()
            if tv_fs is not None:
                snapshots.append(("tv", tv_fs))
        except Exception as exc:
            logger.warning("TV source failed: %s", exc)

    if include_yfinance:
        try:
            if yf_adapter is None:
                from adapters.yfinance_macro import YFinanceMacroAdapter
                yf_adapter = YFinanceMacroAdapter()
            yf_fs = yf_adapter.fetch() if hasattr(yf_adapter, "fetch") else yf_adapter()
            if yf_fs is not None:
                snapshots.append(("yfinance", yf_fs))
        except Exception as exc:
            logger.warning("yfinance source failed: %s", exc)

    if include_fred:
        try:
            if fred_adapter is None:
                from adapters.fred import EXTENDED_SERIES, FredMacroAdapter
                fred_adapter = FredMacroAdapter(series=EXTENDED_SERIES, timeout_seconds=8)
            fred_fs = fred_adapter.fetch() if hasattr(fred_adapter, "fetch") else fred_adapter()
            if fred_fs is not None:
                snapshots.append(("fred", fred_fs))
        except Exception as exc:
            logger.warning("FRED source failed: %s", exc)

    if use_cache:
        cached = load_last_good(cache_path)
        if cached is not None:
            snapshots.append(("cache", cached))

    if not snapshots:
        return None

    merged = merge_feature_snapshots(snapshots, prefer_high_quality_from=["fred"])
    if merged is not None and use_cache:
        try:
            save_last_good(merged, cache_path)
        except Exception as exc:
            logger.warning("save_last_good failed: %s", exc)
    return merged
