"""Macro OS - Futu Read-Only Sensor (v5.0 Research Edition).

Provides a strictly read-only interface to Futu OpenD.
Secured against order placement. Automatically merges Cash balances with Security positions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OpenSecTradeContext = None
TrdEnv = None
TrdMarket = None
FUTU_AVAILABLE = False

try:
    from futu import OpenSecTradeContext as _OpenSecTradeContext, TrdEnv as _TrdEnv, TrdMarket as _TrdMarket

    OpenSecTradeContext = _OpenSecTradeContext
    TrdEnv = _TrdEnv
    TrdMarket = _TrdMarket
    FUTU_AVAILABLE = True
except ImportError:
    logger.warning("futu-api not installed. FutuSensor will operate in DEGRADED mode.")


@dataclass
class Position:
    ticker: str
    quantity: float
    cost_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    actual_weight: float = 0.0


@dataclass
class AccountSnapshot:
    positions: List[Position] = field(default_factory=list)
    total_market_value: float = 0.0
    total_assets: float = 0.0
    cash_balance: float = 0.0
    fetched_at: float = 0.0
    is_stale: bool = False

    def to_dict(self) -> Dict[str, float]:
        """Output a weight dictionary for downstream reconciliation."""
        return {p.ticker: round(p.actual_weight, 4) for p in self.positions}


class FutuSensor:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        env: str = "REAL",
        market: str = "US",
        stale_threshold_seconds: int = 900,
    ) -> None:
        self.host = host
        self.port = port
        if FUTU_AVAILABLE and TrdEnv is not None and TrdMarket is not None:
            self.env = TrdEnv.REAL if env.upper() == "REAL" else TrdEnv.SIMULATE
            self.market = TrdMarket.US if market.upper() == "US" else TrdMarket.HK
        else:
            self.env = env.upper()
            self.market = market.upper()
        self.stale_threshold = stale_threshold_seconds
        self._last_snapshot: Optional[AccountSnapshot] = None

    def fetch_positions(self) -> AccountSnapshot:
        if not FUTU_AVAILABLE:
            return AccountSnapshot(is_stale=True, fetched_at=time.time())
        try:
            snapshot = self._fetch_impl()
            self._last_snapshot = snapshot
            return snapshot
        except Exception as e:
            logger.error("FutuSensor Fetch Failed: %s", e)
            if self._last_snapshot:
                self._last_snapshot.is_stale = (time.time() - self._last_snapshot.fetched_at) > self.stale_threshold
                return self._last_snapshot
            return AccountSnapshot(is_stale=True, fetched_at=time.time())

    def _fetch_impl(self) -> AccountSnapshot:
        trd_ctx = OpenSecTradeContext(host=self.host, port=self.port, filter_trdmarket=self.market)
        try:
            ret_acc, data_acc = trd_ctx.accinfo_query(trd_env=self.env, refresh_cache=True)
            cash_balance = 0.0
            if ret_acc == 0 and data_acc is not None and not data_acc.empty:
                cash_balance = float(data_acc.get("cash", [0.0])[0])

            ret_pos, data_pos = trd_ctx.position_list_query(trd_env=self.env, refresh_cache=True)
            if ret_pos != 0:
                raise ConnectionError(f"position_list_query failed: {data_pos}")

            positions: List[Position] = []
            total_mv = 0.0

            if data_pos is not None and not data_pos.empty:
                for _, row in data_pos.iterrows():
                    ticker = str(row.get("code", "")).split(".")[-1]
                    qty = float(row.get("qty", 0) or 0)
                    mv = float(row.get("market_val", 0) or 0)

                    if qty > 0:
                        positions.append(
                            Position(
                                ticker=ticker,
                                quantity=qty,
                                cost_price=float(row.get("cost_price", 0) or 0),
                                market_value=mv,
                                unrealized_pnl=float(row.get("unrealized_pl", 0) or 0),
                                unrealized_pnl_pct=float(row.get("pl_ratio", 0) or 0) * 100,
                            )
                        )
                        total_mv += mv

            total_assets = total_mv + cash_balance
            for p in positions:
                p.actual_weight = p.market_value / total_assets if total_assets > 0 else 0.0

            positions.append(
                Position(
                    ticker="CASH",
                    quantity=cash_balance,
                    cost_price=1.0,
                    market_value=cash_balance,
                    unrealized_pnl=0.0,
                    unrealized_pnl_pct=0.0,
                    actual_weight=cash_balance / total_assets if total_assets > 0 else 1.0,
                )
            )

            positions.sort(key=lambda x: x.market_value, reverse=True)

            logger.info(
                "FutuSensor: Securities MV=%.0f | Cash=%.0f | Total Assets=%.0f",
                total_mv,
                cash_balance,
                total_assets,
            )

            return AccountSnapshot(
                positions=positions,
                total_market_value=total_mv,
                total_assets=total_assets,
                cash_balance=cash_balance,
                fetched_at=time.time(),
            )
        finally:
            trd_ctx.close()


def _security_assertion():
    forbidden = ["TrdCtx", "place_order", "modify_order", "cancel_order"]
    for name in forbidden:
        if name in dir():
            raise RuntimeError(f"SECURITY VIOLATION: {name} found. Module must be READ-ONLY.")


_security_assertion()
