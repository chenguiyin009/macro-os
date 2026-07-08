from __future__ import annotations
from typing import Dict, Optional


class EarningsVerifier:
    def __init__(self, manual_whitelist: Optional[Dict[str, bool]] = None):
        self.whitelist: Dict[str, bool] = manual_whitelist or {
            "NORTH_EQUIP": True,
            "CPO_ELS": True,
            "LEADER_BROKER": True,
            "MDI_CHEM": True,
            "SH_GOLD": True,
            "PUM_CONCEPT": False,
        }

    def verify(self, ticker: str) -> bool:
        return self.whitelist.get(ticker, False)
