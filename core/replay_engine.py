"""Macro OS — Replay Engine.

Causal simulation system over historical event stream.

Key constraints:
- NO LOOKAHEAD BIAS: feature buffer uses only data strictly before event.timestamp
- Fully deterministic
- Any lookahead violation crashes execution
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.evaluation import ConfusionMatrix, ReplayEvaluation, transition_accuracy, stability_score
from core.pnl_simulator import PnLSimulator
from core.regime import compute_regime
from core.regime_labeler import label_regime
from core.schemas import Event, FeatureSchema, RegimeType

logger = logging.getLogger(__name__)


class TemporalViolation(Exception):
    """Raised when lookahead bias is detected."""
    pass


class TemporalBuffer:
    """Strictly right-open time-ordered buffer for feature data.

    Enforces that all buffered data timestamps are strictly less than
    the current event timestamp.
    """

    def __init__(self) -> None:
        self._features: List[Dict[str, Any]] = []
        self._timestamps: List[str] = []

    def add(self, features: Dict[str, Any], timestamp: str) -> None:
        """Add features at a given timestamp."""
        self._features.append(features)
        self._timestamps.append(timestamp)

    def collect_before(self, ts: str) -> Dict[str, Any]:
        """Collect the latest feature snapshot strictly before ts.

        Args:
            ts: ISO timestamp string. Must be strictly greater than
                all buffered timestamps used for this collection.

        Returns:
            Latest available feature dict before ts.

        Raises:
            TemporalViolation: If any buffered feature has timestamp >= ts.
        """
        if not self._features:
            return FeatureSchema().model_dump()

        for buf_ts in self._timestamps:
            if buf_ts >= ts:
                raise TemporalViolation(
                    f"LOOKAHEAD VIOLATION: buffer has event at {buf_ts} "
                    f"but current event timestamp is {ts}. "
                    f"All buffer timestamps must be < current event ts."
                )

        return self._features[-1]

    def clear_before(self, ts: str) -> None:
        """Remove entries before ts to bound memory (optional)."""
        keep_features: List[Dict[str, Any]] = []
        keep_timestamps: List[str] = []
        for f, t in zip(self._features, self._timestamps):
            if t >= ts:
                keep_features.append(f)
                keep_timestamps.append(t)
        self._features = keep_features
        self._timestamps = keep_timestamps

    @property
    def size(self) -> int:
        return len(self._features)

    @property
    def max_timestamp(self) -> Optional[str]:
        return self._timestamps[-1] if self._timestamps else None


class ReplayEngine:
    """Causal replay engine over event-sourced macro decisions.

    Orchestrates the full replay pipeline:
    events -> temporal buffer -> regime model -> ground truth -> evaluation
    """

    def __init__(
        self,
        events_path: Path,
        config: Optional[Dict[str, Any]] = None,
        drawdown_threshold: float = -0.05,
        volatility_threshold: float = 0.03,
        price_field: str = "close",
        spread_bps: float = 1.0,
        slippage_bps: float = 2.0,
        switch_penalty_bps: float = 5.0,
    ) -> None:
        self.events_path = events_path
        self.config = config or {}
        self.drawdown_threshold = drawdown_threshold
        self.volatility_threshold = volatility_threshold
        self.price_field = price_field
        self.spread_bps = spread_bps
        self.slippage_bps = slippage_bps
        self.switch_penalty_bps = switch_penalty_bps

        self.buffer = TemporalBuffer()
        self.pnl_sim = PnLSimulator(
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            regime_switch_penalty_bps=switch_penalty_bps,
        )

    def load_events(self) -> List[Event]:
        """Load and time-order events from the vault."""
        events: List[Event] = []
        if not self.events_path.exists():
            logger.warning("No events found at %s", self.events_path)
            return events

        with open(self.events_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(Event.from_jsonl(line))

        # Sort chronologically (stable for deterministic replay)
        events.sort(key=lambda e: e.ts)
        logger.info("Loaded %d events from %s", len(events), self.events_path)
        return events

    def run(self) -> Dict[str, Any]:
        """Execute full replay over the event stream.

        Returns:
            Dict with full evaluation metrics.

        Raises:
            TemporalViolation: If lookahead bias is detected.
        """
        events = self.load_events()
        if not events:
            logger.warning("Replay aborted: no events")
            return {"error": "no_events", "message": "Event store is empty"}

        # Separate feature events from decision events
        feature_events = [e for e in events if e.event_type == "FEATURE"]
        decision_events = [
            e for e in events
            if e.event_type in ("DECISION", "INIT")
        ]

        # Phase 1: Label ground truth from future market data
        # (uses forward-looking data from the event prices)
        actual_regimes = self._compute_ground_truth(events)

        # Phase 2: Simulate forward through decision events
        predicted_regimes: List[str] = []
        decisions_for_pnl: List[Dict[str, Any]] = []
        market_returns: List[float] = []

        for i, event in enumerate(decision_events):
            # Check temporal boundary
            if event.event_type == "DECISION":
                ts = event.ts
            else:
                ts = event.ts

            # Collect features strictly before this event's timestamp
            try:
                latest_features = self.buffer.collect_before(ts)
            except TemporalViolation:
                raise

            # Run regime model on buffered features
            regime = compute_regime(latest_features, self.config)
            predicted_regimes.append(regime)

            # Extract decision action for PnL
            action = event.payload.get("action", "NO_TRADE")
            confidence = event.payload.get("confidence", 0.0)
            decisions_for_pnl.append({
                "action": action,
                "confidence": confidence,
            })

            # Compute period return from next event (if available)
            if i + 1 < len(decision_events):
                ret = self._period_return(event, decision_events[i + 1])
            else:
                ret = 0.0
            market_returns.append(ret)

            # After processing, add this event's features to buffer
            # for future time steps (only if it contains feature data)
            if "features" in event.payload:
                self.buffer.add(event.payload["features"], ts)

        # Phase 3: PnL simulation
        pnl_result = self.pnl_sim.simulate(decisions_for_pnl, market_returns)

        # Phase 4: Evaluation
        # Pad or truncate to match lengths
        min_len = min(len(actual_regimes), len(predicted_regimes))
        evaluation = ReplayEvaluation(
            pnl_result=pnl_result,
            actual_regimes=actual_regimes[:min_len],
            predicted_regimes=predicted_regimes[:min_len],
        )

        metrics = evaluation.metrics()

        logger.info(
            "Replay complete: accuracy=%.2f%% sharpe=%.2f stability=%.2f",
            metrics["confusion_matrix"]["accuracy"] * 100,
            metrics["pnl"]["sharpe"],
            metrics["stability_score"],
        )

        return metrics

    def _compute_ground_truth(self, events: List[Event]) -> List[str]:
        """Label ground-truth regimes from forward-looking event payload data.

        For each decision event, looks ahead N events to simulate
        future market path for labeling.
        """
        regimes: List[str] = []

        for i, event in enumerate(events):
            if event.event_type not in ("DECISION", "INIT"):
                continue

            # Gather future prices (mock: use subsequent event payloads)
            future_prices = self._extract_future_prices(events, i, horizon=21)

            if len(future_prices) < 2:
                regimes.append(RegimeType.TRANSITION.value)
                continue

            label = label_regime(
                future_prices=future_prices,
                drawdown_threshold=self.drawdown_threshold,
                volatility_threshold=self.volatility_threshold,
            )
            regimes.append(label["regime"])

        return regimes

    def _extract_future_prices(
        self, events: List[Event], start_idx: int, horizon: int = 21
    ) -> List[float]:
        """Extract a forward price series for ground truth labeling.

        Walks forward from start_idx to collect price data from
        subsequent events.
        """
        prices: List[float] = []
        for j in range(start_idx + 1, min(len(events), start_idx + 1 + horizon)):
            payload = events[j].payload
            features = payload.get("features", {})
            # Try common price fields
            price = features.get(
                self.price_field,
                features.get("close", features.get("gold", features.get("dxy"))),
            )
            if price is not None:
                prices.append(float(price))
            else:
                # Extend with last known price
                if prices:
                    prices.append(prices[-1] * 1.001)  # small drift
                else:
                    prices.append(100.0)

        return prices

    def _period_return(
        self, event_a: Event, event_b: Event
    ) -> float:
        """Compute period return between two consecutive events."""
        price_a = event_a.payload.get("risk_score", 0.5)
        price_b = event_b.payload.get("risk_score", 0.5)
        if price_a == 0:
            return 0.0
        return (price_b - price_a) / price_a

    def save_results(self, metrics: Dict[str, Any], output_dir: Path) -> None:
        """Save replay results to disk."""
        output_dir.mkdir(parents=True, exist_ok=True)

        # JSON metrics
        metrics_path = output_dir / "REPLAY_RESULTS.json"
        with open(metrics_path, "w") as f:
            json.dump(metrics, f, indent=2, default=str)
        logger.info("Metrics saved to %s", metrics_path)

        # Confusion matrix heatmap
        # Rebuild from metrics data if needed
        cm_path = output_dir / "confusion_matrix.png"
        cm = ConfusionMatrix()
        # (Optional: reconstruct from stored data)
        cm.to_heatmap(cm_path)
        logger.info("Heatmap saved to %s", cm_path)
