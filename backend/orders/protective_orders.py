"""Broker-side protective orders (Groww Smart Order OCO) -- investigation +
minimal opt-in implementation.

INVESTIGATION SUMMARY (grounded in Groww's official docs,
groww.in/trade-api/docs/curl/smart-orders, fetched during this development
stage, plus the installed growwapi SDK's own `create_smart_order` docstring
inspected via `inspect.signature`):

- Groww supports two smart order types: GTT (single trigger) and OCO (One
  Cancels the Other -- places a target + stop-loss together; when one leg
  executes, the other is cancelled automatically by the broker).
- OCO is documented to work with the CASH segment and CNC product on NSE
  equity -- exactly what this bot trades (backend/autonomous/config.py
  defaults: order_segment="CASH", product="CNC"). This is what makes OCO
  usable here at all; it would NOT be usable for FNO/MIS-only strategies
  without re-checking that combination separately.
- The installed SDK's create_smart_order() takes, for OCO: quantity,
  net_position_quantity (must be <= abs(net_position_quantity) per docs),
  target={trigger_price, order_type, price?}, stop_loss={trigger_price,
  order_type, price?}, transaction_type (the EXIT side, e.g. SELL to close
  a long).
- Cancellation: cancel_smart_order(segment, smart_order_type, smart_order_id).
- Status: get_smart_order(...)/get_smart_order_list(status=ACTIVE|CANCELLED|COMPLETED).

WHY THIS DEFAULTS TO DISABLED (AutonomousConfig.use_protective_orders=False,
env AUTOTRADE_USE_PROTECTIVE_ORDERS): only the CREATE path is implemented
and tested here. The full lifecycle the audit's own protective-order
section calls for -- modifying/cancelling the OCO when a position is
manually closed or partially exited outside this order's control,
reconciling smart-order state against local state, and confirming exactly
how a partial ENTRY fill should size the protective quantity when more of
the entry fills later -- is NOT built. Creating a protective order this
bot cannot properly track or cancel would be worse than the current
client-side-only monitoring, so this stays an explicit opt-in for anyone
who has verified the rest of that lifecycle for their own deployment,
not a default-on behavior.

Only ever called for a FILLED/PARTIALLY_FILLED entry order with a
confirmed filled_quantity > 0 -- never for an unfilled or rejected entry.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("orders.protective_orders")


def create_protective_oco(
    client: Any,
    ticker: str,
    exchange: str,
    segment: str,
    product: str,
    quantity: int,
    target_price: float,
    stop_price: float,
    exit_transaction_type: str = "SELL",
    reference_id: str | None = None,
) -> dict[str, Any]:
    """Create a broker-side OCO (target + stop-loss) for a just-filled
    entry. `quantity` must be the ACTUAL filled quantity of that entry, not
    the originally requested quantity -- callers must never protect
    quantity that was not actually filled (see module docstring / the
    audit's protective-order-lifecycle requirement).
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive -- never create protection for an unfilled entry")
    return client.create_smart_order(
        smart_order_type="OCO",
        segment=segment,
        trading_symbol=ticker,
        quantity=quantity,
        product_type=product,
        exchange=exchange,
        duration="DAY",
        reference_id=reference_id,
        net_position_quantity=quantity,
        target={"trigger_price": f"{target_price:.2f}", "order_type": "LIMIT"},
        stop_loss={"trigger_price": f"{stop_price:.2f}", "order_type": "SL_M"},
        transaction_type=exit_transaction_type,
    )


def cancel_protective_oco(client: Any, smart_order_id: str, segment: str) -> dict[str, Any]:
    return client.cancel_smart_order(segment=segment, smart_order_type="OCO", smart_order_id=smart_order_id)
