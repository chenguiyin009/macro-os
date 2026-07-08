"""Decision Kernel v5 — CLI simulator.

Usage:
    python scripts/simulate_kernel.py --phase EARLY --regime RISK_ON --soft-regime-label RISK_ON --risk-score 0.8 --confidence 0.8 --proposed-risk 0.8 --recovery false --days-in-recovery 0 --previous-risk-budget 0.0
"""
from __future__ import annotations

import argparse
import json
import time

from core.decision_kernel import decide


def main(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description="Decision Kernel v5 Simulator")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--regime", required=True)
    parser.add_argument("--soft-regime-label", required=True)
    parser.add_argument("--risk-score", type=float, required=True)
    parser.add_argument("--confidence", type=float, required=True)
    parser.add_argument("--proposed-risk", type=float, required=True)
    parser.add_argument("--recovery", type=str, required=True)
    parser.add_argument("--days-in-recovery", type=int, required=True)
    parser.add_argument("--previous-risk-budget", type=float, required=True)
    args = parser.parse_args(argv)

    kd = decide(
        {},
        args.regime,
        args.soft_regime_label,
        args.risk_score,
        args.confidence,
        None,
        divergence_phase=args.phase,
        recovery_active=args.recovery.lower() == "true",
        proposed_risk=args.proposed_risk,
        days_in_recovery=args.days_in_recovery,
        previous_risk_budget=args.previous_risk_budget,
    )
    payload = {
        "timestamp": int(time.time()),
        "audit_trail": kd.audit_trail,
        "execution_outcome": {
            "governing_authority": kd.authority.value,
            "final_risk_budget": kd.risk_budget,
            "final_defense_budget": kd.defense_budget,
            "action_required": kd.decision.action.value,
            "reason_code": kd.reason_code,
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=False)


if __name__ == "__main__":
    print(main())
