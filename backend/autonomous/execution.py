"""Order execution for the autonomous loop.

Two modes, selected purely by the existing GROWW_ALLOW_REAL_ORDERS flag
(backend/groww/auth.py):

- paper (default, safe): no network call to place an order. The fill is
  simulated at the last observed price and recorded exactly like a real
  trade so the audit trail (backend.autonomous_events, backend.positions)
  looks identical either way.
- live (opt-in): places a real MARKET order via the existing Groww client.

Long-only by design: the strategy buys dips and sells into strength. Short
selling is not implemented here -- it requires margin/collateral handling
this bot does not attempt.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from backend.autonomous.config import AutonomousConfig
from backend.groww.auth import is_real_trading_enabled
from backend.groww.client import GrowwClientError, get_client
from backend.positions.position_store import position_store

logger = logging.getLogger(__name__)

_PAPER_STARTING_CAPITAL = float(os.environ.get("AUTOTRADE_PAPER_CAPITAL", "100000"))


@dataclass
class FillResult:
    order_id: str
    price: float
    quantity: int
    mode: str  # "paper" | "live"


class ExecutionEngine:
    def __init__(self, config: AutonomousConfig) -> None:
        self.config = config
        self.mode = "live" if is_real_trading_enabled() else "paper"

    def get_available_margin(self) -> float:
        if self.mode == "live":
            client = get_client()
            margin = client.get_margin()
            return float(margin.get("available_margin") or margin.get("available_cash") or 0.0)
        # Paper mode: try a real margin read if credentials happen to be
        # configured (useful for sizing against a real account while still
        # refusing to place real orders), otherwise fall back to a fixed
        # paper bankroll.
        try:
            client = get_client()
            margin = client.get_margin()
            value = float(margin.get("available_margin") or margin.get("available_cash") or 0.0)
            if value > 0:
                return value
        except Exception:
            pass
        return _PAPER_STARTING_CAPITAL

    def buy(self, ticker: str, quantity: int, last_price: float) -> FillResult:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.mode == "live":
            client = get_client()
            try:
                result = client.place_order(
                    {
                        "trading_symbol": ticker,
                        "exchange": self.config.exchange,
                        "segment": "EQ",
                        "product": self.config.product,
                        "order_type": self.config.order_type,
                        "transaction_type": "BUY",
                        "quantity": quantity,
                    }
                )
            except GrowwClientError as exc:
                raise
            order_id = str(result.get("order_id") or result.get("id") or uuid.uuid4())
            fill_price = float(result.get("price") or last_price)
            return FillResult(order_id=order_id, price=fill_price, quantity=quantity, mode="live")

        order_id = f"PAPER-{uuid.uuid4()}"
        logger.info("[paper] BUY %s x%s @ %.2f (order_id=%s)", ticker, quantity, last_price, order_id)
        return FillResult(order_id=order_id, price=last_price, quantity=quantity, mode="paper")

    def sell(self, ticker: str, quantity: int, last_price: float) -> FillResult:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.mode == "live":
            client = get_client()
            result = client.place_order(
                {
                    "trading_symbol": ticker,
                    "exchange": self.config.exchange,
                    "segment": "EQ",
                    "product": self.config.product,
                    "order_type": self.config.order_type,
                    "transaction_type": "SELL",
                    "quantity": quantity,
                }
            )
            order_id = str(result.get("order_id") or result.get("id") or uuid.uuid4())
            fill_price = float(result.get("price") or last_price)
            return FillResult(order_id=order_id, price=fill_price, quantity=quantity, mode="live")

        order_id = f"PAPER-{uuid.uuid4()}"
        logger.info("[paper] SELL %s x%s @ %.2f (order_id=%s)", ticker, quantity, last_price, order_id)
        return FillResult(order_id=order_id, price=last_price, quantity=quantity, mode="paper")

    def open_position_record(
        self,
        ticker: str,
        fill: FillResult,
        target1: float,
        invalidation: float,
        interval: str,
    ) -> dict[str, Any]:
        return position_store.create(
            {
                "ticker": ticker,
                "interval": interval,
                "direction": "bullish",
                "entry_price": fill.price,
                "target1": target1,
                "invalidation": invalidation,
                "source": "autonomous",
                "quantity": fill.quantity,
                "order_id": fill.order_id,
            }
        )

    def close_position_record(self, position_id: str, exit_price: float, reason: str) -> dict[str, Any] | None:
        return position_store.close(position_id, exit_price, reason)
