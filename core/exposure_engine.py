from typing import Dict, List


class ExposureEngine:
    def __init__(self):
        self.matrix = {
            "TECH_HW": {"NVDA": 0.45, "AVGO": 0.30, "AMD": 0.25},
            "TECH_HYPER": {"MSFT": 0.35, "AMZN": 0.30, "GOOGL": 0.20, "META": 0.15},
            "TECH_AI_APP": {"PLTR": 0.30, "APP": 0.30, "SNOW": 0.20, "DDOG": 0.20},
            "TECH_NET": {"ANET": 0.40, "CSCO": 0.35, "CIEN": 0.25},
            "TECH_POWER": {"VRT": 0.40, "ETN": 0.30, "PWR": 0.30},
            "QQQ": {"NVDA": 0.08, "MSFT": 0.09, "AAPL": 0.09},
        }

    def calculate_look_through(self, flat_weights: Dict[str, float]) -> Dict[str, float]:
        true = {}
        for asset, weight in flat_weights.items():
            if weight <= 0:
                continue
            comps = self.matrix.get(asset, {})
            if not comps:
                true[asset] = true.get(asset, 0.0) + weight
                continue
            for entity, ratio in comps.items():
                true[entity] = true.get(entity, 0.0) + (weight * ratio)
        return {k: round(v, 4) for k, v in true.items()}

    def check_concentration(self, true_exposure: Dict[str, float], limit: float = 0.15) -> List[Dict]:
        violations = []
        for name, exp in true_exposure.items():
            if name in {"CASH", "GLD", "THEME_DEF"}:
                continue
            if exp > limit:
                violations.append({"entity": name, "actual": exp, "limit": limit, "excess": round(exp - limit, 4)})
        return violations
