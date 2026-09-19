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

Order placement and fill verification (submit -> pending -> filled/rejected/
partial, never assuming a fill from a bare place_order() response) is
delegated entirely to backend/orders/order_manager.py -- this module no
longer talks to GrowwClient.place_order() directly. See that module for the
state machine and the honest limitations of its broker-response parsing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.autonomous.config import AutonomousConfig
from backend.groww.auth import is_real_trading_enabled
from backend.groww.client import get_client
from backend.orders.order_manager import OrderManager
from backend.positions.position_store import position_store

logger = logging.getLogger(__name__)

_PAPER_STARTING_CAPITAL = float(os.environ.get("AUTOTRADE_PAPER_CAPITAL", "100000"))


class ExecutionEngine:
    def __init__(self, config: AutonomousConfig) -> None:
        self.config = config
        self.mode = "live" if is_real_trading_enabled() else "paper"
        self.order_manager = OrderManager(get_client(), config)

    def get_available_margin(self) -> float:
        # Uses order_manager.client rather than a fresh get_client() call so
        # that a test (or any caller) injecting a fake client into
        # order_manager sees consistent behavior across every broker call
        # this engine makes -- this is also what makes margin re-validation
        # immediately before a live submission (trader._pre_live_submission_checks)
        # actually testable without a real account.
        if self.mode == "live":
            margin = self.order_manager.client.get_margin()
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

    def has_in_flight_order(self, ticker: str, side: str) -> bool:
        return self.order_manager.has_in_flight(ticker, side)

    def submit_and_confirm(
        self,
        decision_id: str,
        ticker: str,
        side: str,
        quantity: int,
        reference_price: float,
        setup_reference: dict | None = None,
    ) -> dict[str, Any]:
        """Create, submit, and (bounded) poll an order to a terminal state
        where possible. Returns the order record -- callers must check
        `status` and `filled_quantity` themselves; a non-FILLED/
        non-PARTIALLY_FILLED result means no position should be created yet.

        Never blocks longer than config.order_confirmation_timeout_seconds
        on the broker; a still-pending order is left in the database for
        `reconcile_all_pending()` to pick up on a later loop iteration.
        """
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        order = self.order_manager.create_order(
            decision_id=decision_id,
            ticker=ticker,
            exchange=self.config.exchange,
            side=side,
            order_type=self.config.order_type,
            product=self.config.product,
            quantity=quantity,
            price=None,
            mode=self.mode,
            setup_reference=setup_reference,
        )
        order = self.order_manager.submit(order, reference_price=reference_price)
        order = self.order_manager.poll_until_terminal(order)
        return order

    def reconcile_pending_orders(self) -> list[dict[str, Any]]:
        return self.order_manager.reconcile_all_pending()

    def open_position_record(
        self,
        ticker: str,
        order: dict[str, Any],
        target1: float,
        invalidation: float,
        interval: str,
    ) -> dict[str, Any]:
        """Create the position using the BROKER-CONFIRMED fill, never the
        requested quantity or the pre-trade quote. Caller must have already
        verified order['status'] in (FILLED, PARTIALLY_FILLED) and
        order['filled_quantity'] > 0.
        """
        filled_qty = int(order["filled_quantity"])
        fill_price = order.get("average_fill_price")
        if fill_price is None:
            # Broker didn't return a fill price and we won't fabricate one
            # from the pre-trade quote -- caller should treat this as
            # "position not yet safely recordable" rather than call here.
            raise ValueError(f"Order {order['id']} has no confirmed average_fill_price; cannot open position.")
        return position_store.create(
            {
                "ticker": ticker,
                "interval": interval,
                "direction": "bullish",
                "entry_price": float(fill_price),
                "target1": target1,
                "invalidation": invalidation,
                "source": "autonomous",
                "quantity": filled_qty,
                "initial_quantity": filled_qty,
                "order_id": order.get("broker_order_id") or order["id"],
            }
        )

    def apply_exit_fill(self, position_id: str, order: dict[str, Any], reason: str) -> dict[str, Any] | None:
        """Apply a broker-confirmed exit fill (full or partial) to a
        position. Only reduces the position by the ACTUAL filled quantity;
        never assumes the whole requested quantity exited.
        """
        from backend.db.engine import SessionLocal
        from backend.db.repository import PositionRepository

        filled_qty = int(order.get("filled_quantity") or 0)
        fill_price = order.get("average_fill_price")
        if filled_qty <= 0 or fill_price is None:
            return None
        db = SessionLocal()
        try:
            return PositionRepository(db).reduce_quantity(
                position_id, filled_qty=filled_qty, fill_price=float(fill_price), exit_reason=reason
            )
        finally:
            db.close()
