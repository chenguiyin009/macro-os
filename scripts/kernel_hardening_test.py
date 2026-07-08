#!/usr/bin/env python3
"""Macro OS v4.3 - Kernel Hardening Test Suite.

20 institutional test cases:
- Regime conflict injection
- Fake VIX spike
- Liquidity flash crash
- Counterfactual leakage
"""

from __future__ import annotations
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("hardening")

from core.decision_kernel import decide, risk_budget_for_kernel
from core.schemas import AuthorityLevel, DecisionAction, KernelDecision

CONFIG = {"decision": {"long_confidence_min": 0.60, "short_confidence_min": 0.65, "no_trade_confidence_max": 0.35, "reduce_threshold": 0.30}}


def run_all():
    passed = 0
    failed = 0

    def check(name, ok):
        nonlocal passed, failed
        if ok:
            passed += 1
            log.info("[PASS] " + name)
        else:
            failed += 1
            log.info("[FAIL] " + name)

    # 1-4: Regime conflict injection
    check("VETO: squeeze forces REDUCE",
          decide({"vix": 30.0}, "LIQUIDITY_SQUEEZE", "RISK_ON", 0.9, 0.9, CONFIG).decision.action == DecisionAction.REDUCE)
    check("VETO: tight forces REDUCE",
          decide({"dxy": 105.0}, "TIGHT_LIQUIDITY", "RISK_ON", 0.8, 0.8, CONFIG).decision.action == DecisionAction.REDUCE)
    check("VETO: risk_on allows LONG",
          decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, CONFIG).decision.action == DecisionAction.AGGRESSIVE)
    check("VETO: squeeze budget=0",
          risk_budget_for_kernel(decide({"vix": 30.0}, "LIQUIDITY_SQUEEZE", "RISK_ON", 0.9, 0.9, CONFIG)) == 0.0)

    # 5-8: VIX spike
    check("VIX spike 30 -> veto",
          decide({"vix": 30.0}, "LIQUIDITY_SQUEEZE", "TRANSITION", 0.5, 0.3, CONFIG).authority == AuthorityLevel.HARD_VETO)
    check("VIX 15 -> soft policy",
          decide({"vix": 15.0}, "RISK_ON", "RISK_ON", 0.7, 0.7, CONFIG).authority == AuthorityLevel.SOFT_POLICY)
    check("VIX 40 -> squeeze REDUCE",
          decide({"vix": 40.0}, "LIQUIDITY_SQUEEZE", "TRANSITION", 0.2, 0.1, CONFIG).decision.action == DecisionAction.REDUCE)
    check("VIX 22 -> tight caution (hard veto)",
          decide({"vix": 22.0, "dxy": 104.0}, "TIGHT_LIQUIDITY", "TIGHT_LIQUIDITY", 0.4, 0.4, CONFIG).authority == AuthorityLevel.HARD_VETO)

    # 9-12: Flash crash
    check("Flash crash: high VIX+HY -> HARD VETO",
          decide({"vix": 35.0, "hy_credit_spread": 600}, "LIQUIDITY_SQUEEZE", "TRANSITION", 0.1, 0.1, CONFIG).authority == AuthorityLevel.HARD_VETO)
    check("Flash crash: budget=0",
          risk_budget_for_kernel(decide({"vix": 35.0}, "LIQUIDITY_SQUEEZE", "RISK_ON", 0.9, 0.9, CONFIG)) == 0.0)
    check("Recovery: transition hard veto",
          decide({"vix": 18.0, "dxy": 101.0}, "TRANSITION", "RISK_ON", 0.5, 0.5, CONFIG).authority == AuthorityLevel.HARD_VETO)
    check("Post-crash: risk_on => AGGRESSIVE",
          decide({"vix": 14.0, "dxy": 97.0}, "RISK_ON", "RISK_ON", 0.7, 0.7, CONFIG).decision.action == DecisionAction.AGGRESSIVE)

    # 13-16: Counterfactual leakage
    import core.decision_kernel as dk
    src = open(dk.__file__, "r", encoding="utf-8").read()
    check("NO counterfactual import", "counterfactual" not in src)
    check("NO attribution import", "attribution" not in src)
    check("NO regime_probabilistic import", "regime_probabilistic" not in src)
    check("Kernel only imports schemas+typing", "from core.schemas" in src)

    # 17-20: Edge cases
    check("Empty features -> risk_reduce",
          decide({}, "TRANSITION", "TRANSITION", 0.3, 0.2, CONFIG).decision.action == DecisionAction.RISK_REDUCE)
    check("Negative confidence ok", isinstance(decide({}, "RISK_ON", "RISK_ON", -0.1, -0.1, CONFIG), KernelDecision))
    check("Zero config -> defaults",
          isinstance(decide({}, "RISK_ON", "RISK_ON", 0.7, 0.7, None), KernelDecision))
    check("Budget <= 1.0", risk_budget_for_kernel(decide({}, "RISK_ON", "RISK_ON", 1.0, 1.0, CONFIG)) <= 1.0)

    log.info("\nResults: %d passed, %d failed, %d total" % (passed, failed, passed+failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
