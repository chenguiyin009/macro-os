from __future__ import annotations
from typing import Any, Dict
from dataclasses import dataclass


@dataclass
class RedLineStatus:
    is_hyper_hawkish: bool = False
    is_recession_pricing: bool = False
    severity_multiplier: float = 1.0


class GlenRedLinesEvaluator:
    @staticmethod
    def evaluate(features: Dict[str, Any]) -> RedLineStatus:
        core_pce = features.get("core_pce", 0.034)
        wage_overheat = features.get("wage_inflation_overheat", False)
        yield_10y_drop = features.get("yield_10y_drop_20bp", False)
        curve_re_inversion = features.get("curve_re_inversion", False)

        hawkish = core_pce >= 0.035 or wage_overheat
        recession = yield_10y_drop or curve_re_inversion

        multiplier = 1.0
        if recession:
            multiplier = 0.2
        elif hawkish:
            multiplier = 0.5

        return RedLineStatus(
            is_hyper_hawkish=hawkish,
            is_recession_pricing=recession,
            severity_multiplier=multiplier,
        )
