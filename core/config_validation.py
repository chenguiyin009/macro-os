from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any, Mapping

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def validate_thresholds_config(thresholds: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    scoring = thresholds.get("scoring", {})
    weights = scoring.get("weights", {})
    required_weights = ("regime_base", "trend_strength", "volatility_adjust", "liquidity_adjust")
    missing_weights = [name for name in required_weights if name not in weights]
    if missing_weights:
        errors.append(
            "thresholds.scoring.weights missing keys: " + ", ".join(missing_weights)
        )
    else:
        try:
            total = sum(float(weights[name]) for name in required_weights)
        except (TypeError, ValueError):
            errors.append("thresholds.scoring.weights must contain numeric values")
        else:
            if abs(total - 1.0) > 1e-6:
                errors.append(
                    f"thresholds.scoring.weights must sum to 1.0 (got {total:.6f})"
                )

    regime = thresholds.get("regime", {})
    risk_on = regime.get("risk_on", {})
    for key in ("tips_yield_roc_60d_max", "dxy_zscore_60d_max"):
        if key not in risk_on:
            errors.append(f"thresholds.regime.risk_on.{key} is required")
        else:
            try:
                float(risk_on[key])
            except (TypeError, ValueError):
                errors.append(
                    f"thresholds.regime.risk_on.{key} must be a numeric threshold"
                )

    constitution = thresholds.get("constitution", {})
    if not constitution:
        errors.append("thresholds.constitution is required")
    else:
        red_lines = constitution.get("red_lines", {})
        execution = constitution.get("execution", {})
        for key in ("vix_escape_hatch", "core_pce_max"):
            if key not in red_lines:
                errors.append(f"thresholds.constitution.red_lines.{key} is required")
            else:
                try:
                    value = float(red_lines[key])
                except (TypeError, ValueError):
                    errors.append(
                        f"thresholds.constitution.red_lines.{key} must be numeric"
                    )
                else:
                    if not isfinite(value) or value <= 0:
                        errors.append(
                            f"thresholds.constitution.red_lines.{key} must be > 0"
                        )
        for key in ("max_daily_turnover", "min_cash_buffer"):
            if key not in execution:
                errors.append(f"thresholds.constitution.execution.{key} is required")
            else:
                try:
                    value = float(execution[key])
                except (TypeError, ValueError):
                    errors.append(
                        f"thresholds.constitution.execution.{key} must be numeric"
                    )
                else:
                    if not 0.0 <= value <= 1.0:
                        errors.append(
                            f"thresholds.constitution.execution.{key} must be between 0 and 1"
                        )

    return errors


def validate_watchlist_config(watchlist: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    assets = watchlist.get("assets")
    if not isinstance(assets, Mapping) or not assets:
        errors.append("watchlist.assets must be a non-empty mapping")
        return errors

    required_fields = (
        "macro_sensitivity",
        "moat_score",
        "logic_stability",
        "has_active_catalyst",
        "atr_percent_20d",
        "beta_to_spy",
        "max_portfolio_weight",
    )

    for ticker, meta in assets.items():
        if not isinstance(meta, Mapping):
            errors.append(f"watchlist.assets.{ticker} must be a mapping")
            continue

        for field in required_fields:
            if field not in meta:
                errors.append(f"watchlist.assets.{ticker}.{field} is required")

        macro_sensitivity = meta.get("macro_sensitivity")
        if isinstance(macro_sensitivity, list):
            if not macro_sensitivity:
                errors.append(
                    f"watchlist.assets.{ticker}.macro_sensitivity must not be empty"
                )
            elif not all(isinstance(item, str) for item in macro_sensitivity):
                errors.append(
                    f"watchlist.assets.{ticker}.macro_sensitivity must contain strings"
                )
        else:
            errors.append(
                f"watchlist.assets.{ticker}.macro_sensitivity must be a list of strings"
            )

        for field in ("moat_score", "logic_stability"):
            value = meta.get(field)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                errors.append(f"watchlist.assets.{ticker}.{field} must be numeric")
                continue
            if not 0.0 <= numeric <= 1.0:
                errors.append(f"watchlist.assets.{ticker}.{field} must be between 0 and 1")

        if not isinstance(meta.get("has_active_catalyst"), bool):
            errors.append(
                f"watchlist.assets.{ticker}.has_active_catalyst must be a boolean"
            )

        try:
            atr_percent = float(meta.get("atr_percent_20d"))
        except (TypeError, ValueError):
            errors.append(
                f"watchlist.assets.{ticker}.atr_percent_20d must be numeric"
            )
        else:
            if not isfinite(atr_percent) or atr_percent <= 0.0:
                errors.append(
                    f"watchlist.assets.{ticker}.atr_percent_20d must be greater than 0"
                )

        try:
            beta = float(meta.get("beta_to_spy"))
        except (TypeError, ValueError):
            errors.append(f"watchlist.assets.{ticker}.beta_to_spy must be numeric")
        else:
            if not isfinite(beta):
                errors.append(f"watchlist.assets.{ticker}.beta_to_spy must be finite")

        try:
            max_weight = float(meta.get("max_portfolio_weight"))
        except (TypeError, ValueError):
            errors.append(
                f"watchlist.assets.{ticker}.max_portfolio_weight must be numeric"
            )
        else:
            if not 0.0 <= max_weight <= 1.0:
                errors.append(
                    f"watchlist.assets.{ticker}.max_portfolio_weight must be between 0 and 1"
                )

    return errors


def validate_macro_configuration(
    thresholds: Mapping[str, Any],
    watchlist: Mapping[str, Any],
) -> None:
    errors = validate_thresholds_config(thresholds)
    errors.extend(validate_watchlist_config(watchlist))
    if errors:
        raise ValueError("Invalid Macro OS configuration:\n- " + "\n- ".join(errors))


def load_and_validate_macro_configuration(
    thresholds_path: Path,
    watchlist_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    thresholds = load_yaml(thresholds_path)
    watchlist = load_yaml(watchlist_path)
    validate_macro_configuration(thresholds, watchlist)
    return thresholds, watchlist
