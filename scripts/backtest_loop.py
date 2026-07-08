"""Macro OS v5.0 Backtest Engine ? time-machine for historical decision replay.

Feeds historical macro data through decide() day-by-day, maintaining
system state (previous_risk_budget, days_in_recovery) across sessions.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import pandas as pd
from core.decision_kernel import decide

# Default config matching tests/test_decision_kernel.py
CONFIG: Dict[str, Any] = {
    "decision": {
        "long_confidence_min": 0.60,
        "short_confidence_min": 0.65,
        "no_trade_confidence_max": 0.35,
        "reduce_threshold": 0.30,
    },
}


class MacroBacktestEngine:
    """Replay historical macro data through the Decision Kernel day-by-day."""

    def __init__(self, historical_data: pd.DataFrame):
        """
        Args:
            historical_data: DataFrame with columns
                date, hard_regime, divergence_phase, recovery_active,
                risk_score, proposed_risk, QQQ_close, and macro features.
        """
        self.data = historical_data
        self.state: Dict[str, Any] = {
            "previous_risk_budget": 0.0,
            "days_in_recovery": 0,
            "last_phase": "NONE",
        }
        self.trace_log: list[Dict[str, Any]] = []

    def _update_recovery_state(self, current_phase: str, recovery_signal: bool):
        """Maintain days_in_recovery counter across consecutive recovery days."""
        if recovery_signal and current_phase in ("LATE", "MID"):
            self.state["days_in_recovery"] += 1
        else:
            self.state["days_in_recovery"] = 0

    def run(self):
        """Execute the backtest: feed each day through decide() with state tracking."""
        print("Starting Macro OS v5.0 historical backtest...")
        for index, row in self.data.iterrows():
            current_date = row.get("date", index)
            hard_regime = row.get("hard_regime", "RISK_ON")
            phase = row.get("divergence_phase", "NONE")
            recovery_signal = row.get("recovery_active", False)
            proposed_risk = row.get("proposed_risk", 0.8)
            risk_score = row.get("risk_score", 0.5)
            self._update_recovery_state(phase, recovery_signal)
            decision = decide(
                features=row.to_dict(),
                hard_regime=hard_regime,
                soft_regime_label="RISK_ON",
                risk_score=risk_score,
                confidence=0.8,
                config=CONFIG,
                divergence_phase=phase,
                recovery_active=recovery_signal,
                proposed_risk=proposed_risk,
                days_in_recovery=self.state["days_in_recovery"],
                previous_risk_budget=self.state["previous_risk_budget"],
            )
            self.state["previous_risk_budget"] = decision.risk_budget
            self.state["last_phase"] = phase
            self.trace_log.append({
                "date": current_date,
                "regime": hard_regime,
                "phase": phase,
                "days_in_recovery": self.state["days_in_recovery"],
                "risk_budget": decision.risk_budget,
                "defense_budget": decision.defense_budget,
                "authority": decision.authority.value,
                "reason_code": decision.reason_code,
                "qqq_close": row.get("QQQ_close", 0.0),
            })
        trace_df = pd.DataFrame(self.trace_log)
        print(f"Backtest complete: {len(self.trace_log)} trading days replayed.")
        return trace_df

    def generate_tearsheet(self, trace_df: pd.DataFrame):
        """Print performance metrics comparing portfolio vs benchmark."""
        if trace_df.empty or "qqq_close" not in trace_df.columns:
            print("No data for tearsheet.")
            return
        df = trace_df.copy()
        df["benchmark_return"] = df["qqq_close"].pct_change().fillna(0.0)
        df["cash_return"] = 0.00008
        df["portfolio_return"] = (df["risk_budget"] * df["benchmark_return"] +
                                   df["defense_budget"] * df["cash_return"])
        df["portfolio_equity"] = (1.0 + df["portfolio_return"]).cumprod()
        df["benchmark_equity"] = (1.0 + df["benchmark_return"]).cumprod()
        df["portfolio_dd"] = (df["portfolio_equity"].cummax() - df["portfolio_equity"]) / df["portfolio_equity"].cummax()
        df["benchmark_dd"] = (df["benchmark_equity"].cummax() - df["benchmark_equity"]) / df["benchmark_equity"].cummax()
        total_days = len(df)
        years = total_days / 252.0
        final_p = df["portfolio_equity"].iloc[-1]
        final_b = df["benchmark_equity"].iloc[-1]
        cagr_p = final_p ** (1.0 / years) - 1.0 if years > 0 else 0.0
        cagr_b = final_b ** (1.0 / years) - 1.0 if years > 0 else 0.0
        max_dd_p = float(df["portfolio_dd"].max())
        max_dd_b = float(df["benchmark_dd"].max())
        std_p = float(df["portfolio_return"].std())
        sharpe = (df["portfolio_return"].mean() / std_p * math.sqrt(252.0)) if std_p > 0 else 0.0
        print("\n========= Tearsheet =========")
        print(f"Period: {df["date"].iloc[0]} to {df["date"].iloc[-1]} ({total_days} days)")
        print(f"Portfolio CAGR: {cagr_p*100:.2f}%")
        print(f"Benchmark (QQQ) CAGR: {cagr_b*100:.2f}%")
        print(f"Portfolio Max DD: {max_dd_p*100:.2f}%")
        print(f"Benchmark (QQQ) Max DD: {max_dd_b*100:.2f}%")
        print(f"Sharpe Ratio (annualized): {sharpe:.2f}")
        print("=============================")
        return df


if __name__ == "__main__":
    """Generate synthetic 1-year market data and run a demo backtest."""
    import numpy as np
    from datetime import datetime, timedelta

    n_days = 252
    base_date = datetime(2024, 1, 2)
    rng = np.random.default_rng(42)
    data = []
    qqq = 400.0
    for day in range(n_days):
        date_str = (base_date + timedelta(days=day)).strftime("%Y-%m-%d")
        if day < 80:
            regime, phase, recovery = "RISK_ON", "NONE", False
            qqq *= 1.0 + rng.normal(0.001, 0.008)
            risk_score = round(0.7 + rng.random() * 0.2, 4)
        elif day < 140:
            regime, phase = "TIGHT_LIQUIDITY", "EARLY" if day < 100 else "MID"
            recovery = True
            qqq *= 1.0 + rng.normal(-0.002, 0.015)
            risk_score = round(0.3 + rng.random() * 0.2, 4)
        elif day < 220:
            regime = "RISK_ON"
            if day < 160:
                phase = "LATE"
            elif day < 190:
                phase = "MID"
            else:
                phase = "NONE"
            recovery = day < 190
            qqq *= 1.0 + rng.normal(0.002, 0.012)
            risk_score = round(0.5 + rng.random() * 0.3, 4)
        else:
            regime, phase, recovery = "RISK_ON", "NONE", False
            qqq *= 1.0 + rng.normal(0.001, 0.009)
            risk_score = round(0.6 + rng.random() * 0.2, 4)
        data.append({
            "date": date_str,
            "hard_regime": regime,
            "divergence_phase": phase,
            "recovery_active": recovery,
            "risk_score": risk_score,
            "proposed_risk": 0.8,
            "QQQ_close": round(qqq, 2),
        })

    df = pd.DataFrame(data)
    engine = MacroBacktestEngine(df)
    trace = engine.run()
    engine.generate_tearsheet(trace)
