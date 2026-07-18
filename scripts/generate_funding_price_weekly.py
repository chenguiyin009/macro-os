"""Generate funding-price weekly research artifacts from a FeatureSchema snapshot.

Writes:
  - data/research/funding_price_week_<start>.json
  - docs/research/<end-or-start>-funding-price-weekly-auto.md

Does not call decision_kernel; research-only pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.features import build_features
from core.research.funding_price_quadrant import (
    assessment_from_week_snapshot,
    classify_funding_price_quadrant,
)
from core.schemas import FeatureSchema

logger = logging.getLogger("macro-os.weekly")


def _week_bounds(as_of: datetime) -> tuple[str, str]:
    # ISO week: Monday start
    d = as_of.date()
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=4)  # Fri
    return start.isoformat(), end.isoformat()


def _attach_source_label(features: FeatureSchema, label: str) -> FeatureSchema:
    features.__dict__["_source_label"] = label
    return features


def _fill_derived_fields(features: FeatureSchema) -> FeatureSchema:
    merged = features.model_dump()
    if merged.get("bei_10y") is None:
        tips = merged.get("tips_yield")
        nominal_10y = merged.get("nominal_10y")
        if tips is not None and nominal_10y is not None:
            merged["bei_10y"] = round(float(nominal_10y) - float(tips), 4)
    result = FeatureSchema.model_validate(merged)
    if "_source_label" in features.__dict__:
        result.__dict__["_source_label"] = features.__dict__["_source_label"]
    return result


def _merge_missing_fields(primary: FeatureSchema, fallback: FeatureSchema, *, label: str) -> FeatureSchema:
    merged = primary.model_dump()
    fallback_data = fallback.model_dump()
    for key, value in fallback_data.items():
        if key in {"source", "fetched_at"}:
            continue
        if merged.get(key) is None and value is not None:
            merged[key] = value
    result = FeatureSchema.model_validate(merged)
    result = _fill_derived_fields(result)
    return _attach_source_label(result, label)


def snapshot_from_features(
    features: Dict[str, Any],
    *,
    week_start: str,
    week_end: str,
    title: str = "资金价格与风险资产周报（自动生成）",
) -> Dict[str, Any]:
    assessment = classify_funding_price_quadrant(features)
    levels = {
        "tips_10y": features.get("tips_yield"),
        "nominal_10y": features.get("nominal_10y"),
        "nominal_30y": features.get("nominal_30y"),
        "nominal_2y": features.get("nominal_2y"),
        "bei_10y": features.get("bei_10y"),
        "dxy": features.get("dxy"),
        "gold": features.get("gold"),
        "vix": features.get("vix"),
        "hy_credit_spread_bp": features.get("hy_credit_spread"),
    }
    chg = {
        "tips_10y_bp": features.get("tips_yield_change_5d_bp"),
        "nominal_10y_bp": features.get("nominal_10y_change_5d_bp"),
        "nominal_30y_bp": features.get("nominal_30y_change_5d_bp"),
    }
    snap = {
        "week_start": week_start,
        "week_end": week_end,
        "title": title,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "funding_price_quadrant": assessment.quadrant.value,
        "quadrant_label_zh": assessment.label_zh,
        "status_summary": assessment.notes or assessment.label_zh,
        "core_conclusion": assessment.notes,
        "transmission_layer": assessment.transmission_layer,
        "drivers": {
            "dominant": assessment.dominant_drivers,
            "non_dominant": [],
        },
        "levels": levels,
        "changes_5d_bp_or_pct": chg,
        "credit": {
            "stable": assessment.credit_stable,
            "hy_stress_confirmed": bool(
                features.get("hy_credit_spread") is not None
                and float(features.get("hy_credit_spread") or 0) >= 400
            ),
        },
        "usd": {
            "breakout": assessment.usd_breakout,
            "funding_squeeze_confirmed": False,
        },
        "macro_os_mapping": {
            "research_quadrant": assessment.quadrant.value,
            "hard_regime_hint": assessment.hard_regime_hint,
            "not_hard_regime": "LIQUIDITY_SQUEEZE",
            "reason": assessment.notes,
        },
        "source": features.get("_source"),
        "assessment": assessment.to_payload(),
    }
    # Re-run through assessment_from_week_snapshot for consistency
    refined = assessment_from_week_snapshot(snap)
    snap["assessment"] = refined.to_payload()
    snap["funding_price_quadrant"] = refined.quadrant.value
    snap["quadrant_label_zh"] = refined.label_zh
    snap["macro_os_mapping"]["hard_regime_hint"] = refined.hard_regime_hint
    return snap


def render_markdown(snapshot: Dict[str, Any]) -> str:
    a = snapshot.get("assessment") or {}
    levels = snapshot.get("levels") or {}
    chg = snapshot.get("changes_5d_bp_or_pct") or {}
    q = snapshot.get("funding_price_quadrant")
    zh = snapshot.get("quadrant_label_zh")
    lines = [
        f"# {snapshot.get('title', '资金价格周报（自动生成）')}",
        "",
        f"- **周期**：{snapshot.get('week_start')} — {snapshot.get('week_end')}",
        f"- **生成时间**：{snapshot.get('generated_at')}",
        f"- **研究象限**：{q}（{zh}）",
        f"- **hard_regime_hint**：`{(snapshot.get('macro_os_mapping') or {}).get('hard_regime_hint')}`",
        f"- **数据源**：{snapshot.get('source')}",
        "",
        "## 核心判断",
        "",
        str(snapshot.get("core_conclusion") or a.get("notes") or ""),
        "",
        "## 资金价格读数",
        "",
        "| 变量 | 水平 | 5D变化(bp) |",
        "|------|------|------------|",
        f"| 10Y TIPS | {levels.get('tips_10y')} | {chg.get('tips_10y_bp')} |",
        f"| 10Y 名义 | {levels.get('nominal_10y')} | {chg.get('nominal_10y_bp')} |",
        f"| 30Y 名义 | {levels.get('nominal_30y')} | {chg.get('nominal_30y_bp')} |",
        f"| 2Y 名义 | {levels.get('nominal_2y')} |  |",
        f"| 10Y BEI | {levels.get('bei_10y')} |  |",
        f"| DXY(代理) | {levels.get('dxy')} |  |",
        f"| HY OAS(bp) | {levels.get('hy_credit_spread_bp')} |  |",
        f"| VIX | {levels.get('vix')} |  |",
        "",
        "## 研究层细节",
        "",
        f"- 真实利率方向：`{a.get('real_rate_direction')}`",
        f"- 名义利率方向：`{a.get('nominal_rate_direction')}`",
        f"- 传导层：`{a.get('transmission_layer')}`",
        f"- 信用稳定：`{a.get('credit_stable')}` / 美元突破：`{a.get('usd_breakout')}`",
        f"- 主导驱动：{', '.join(a.get('dominant_drivers') or [])}",
        "",
        "## 说明",
        "",
        "本文件由 `scripts/generate_funding_price_weekly.py` 自动生成，属于研究层 SSOT 草稿。",
        "不直接改写 `decide()` 预算；Q1 默认 hint 为 TIGHT_LIQUIDITY，而非 LIQUIDITY_SQUEEZE。",
        "",
    ]
    return "\n".join(lines)


def write_weekly_artifacts(
    snapshot: Dict[str, Any],
    *,
    project_root: Path,
) -> tuple[Path, Path]:
    week_start = snapshot["week_start"]
    week_end = snapshot["week_end"]
    data_path = project_root / "data" / "research" / f"funding_price_week_{week_start}.json"
    doc_path = project_root / "docs" / "research" / f"{week_end}-funding-price-weekly-auto.md"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    doc_path.write_text(render_markdown(snapshot), encoding="utf-8")
    return data_path, doc_path


def load_features_from_source(source: str, project_root: Path) -> FeatureSchema:
    source = source.lower()
    if source == "fred":
        from adapters.fred import EXTENDED_SERIES, FredMacroAdapter
        from adapters.yfinance_macro import YFinanceMacroAdapter

        fred_fs = FredMacroAdapter(series=EXTENDED_SERIES, timeout_seconds=15).fetch()
        yf_fs = YFinanceMacroAdapter().fetch()
        if fred_fs is None and yf_fs is None:
            raise RuntimeError("FRED/yfinance fetch failed")
        if fred_fs is None:
            return _fill_derived_fields(_attach_source_label(yf_fs, "yfinance"))  # type: ignore[arg-type]
        if yf_fs is None:
            return _fill_derived_fields(_attach_source_label(fred_fs, "fred"))
        return _merge_missing_fields(fred_fs, yf_fs, label="fred+yfinance")
    if source in {"yfinance", "yf"}:
        from adapters.fred import EXTENDED_SERIES, FredMacroAdapter
        from adapters.yfinance_macro import YFinanceMacroAdapter

        yf_fs = YFinanceMacroAdapter().fetch()
        fred_fs = FredMacroAdapter(series=EXTENDED_SERIES, timeout_seconds=15).fetch()
        if yf_fs is None and fred_fs is None:
            raise RuntimeError("yfinance/FRED fetch failed")
        if yf_fs is None:
            return _attach_source_label(fred_fs, "fred")  # type: ignore[arg-type]
        if fred_fs is None:
            return _fill_derived_fields(_attach_source_label(yf_fs, "yfinance"))
        return _merge_missing_fields(yf_fs, fred_fs, label="yfinance+fred")
    if source in {"composite", "auto"}:
        from adapters.macro_composite import fetch_merged_macro_snapshot

        fs = fetch_merged_macro_snapshot(include_tv=False)
        if fs is None:
            raise RuntimeError("composite fetch failed")
        return _fill_derived_fields(_attach_source_label(fs, "composite"))
    if source == "tv":
        from adapters.tradingview import TradingViewAdapter

        fs = TradingViewAdapter().fetch()
        if fs is None:
            raise RuntimeError("TV fetch failed")
        return fs
    if source == "mock":
        from adapters.tradingview import TradingViewAdapter

        return TradingViewAdapter()._mock_snapshot()
    if source.endswith(".json"):
        path = Path(source)
        if not path.is_absolute():
            path = project_root / path
        data = json.loads(path.read_text(encoding="utf-8"))
        # allow either FeatureSchema fields or weekly snapshot
        if "levels" in data:
            levels = data["levels"]
            chg = data.get("changes_5d_bp_or_pct") or {}
            return FeatureSchema(
                tips_yield=levels.get("tips_10y"),
                nominal_10y=levels.get("nominal_10y"),
                nominal_30y=levels.get("nominal_30y"),
                nominal_2y=levels.get("nominal_2y"),
                bei_10y=levels.get("bei_10y"),
                dxy=levels.get("dxy"),
                gold=levels.get("gold"),
                vix=levels.get("vix"),
                hy_credit_spread=levels.get("hy_credit_spread_bp"),
                tips_yield_change_5d_bp=chg.get("tips_10y_bp"),
                nominal_10y_change_5d_bp=chg.get("nominal_10y_bp"),
                nominal_30y_change_5d_bp=chg.get("nominal_30y_bp"),
            )
        return _fill_derived_fields(FeatureSchema.model_validate(data))
    raise ValueError(f"unknown source: {source}")


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate funding-price weekly research artifacts")
    parser.add_argument(
        "--source",
        default="fred",
        help="fred | tv | mock | path/to.json",
    )
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today UTC)")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc)
    else:
        as_of = datetime.now(timezone.utc)
    week_start, week_end = _week_bounds(as_of)

    fs = load_features_from_source(args.source, root)
    features = build_features(fs)
    features["_source"] = getattr(fs, "_source_label", getattr(getattr(fs, "source", None), "value", str(getattr(fs, "source", ""))))
    snap = snapshot_from_features(features, week_start=week_start, week_end=week_end)
    data_path, doc_path = write_weekly_artifacts(snap, project_root=root)
    logger.info("wrote %s", data_path)
    logger.info("wrote %s", doc_path)
    print(json.dumps({"data": str(data_path), "doc": str(doc_path), "quadrant": snap["funding_price_quadrant"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
