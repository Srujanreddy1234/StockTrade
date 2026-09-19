"""Broker order lifecycle: state machine, submission, and fill verification.

This module exists because "place_order() returned without raising" was
previously treated as proof of a fill (backend/autonomous/execution.py, pre
this change). It is not: a MARKET order can be accepted-but-pending,
partially filled, rejected, or the response can simply be lost to a network
failure after the broker already accepted it. This module never creates or
updates a position until a broker-confirmed fill exists; the trader/exit
paths call into it instead of GrowwClient.place_order() directly.

IMPORTANT / HONEST LIMITATION: the exact JSON field names Groww's servers
return in get_order_status()/get_order_status_by_reference()/place_order()
payloads are NOT documented in the installed growwapi SDK source (the SDK
only defines the request shape and unwraps the `payload` envelope -- the
payload's own keys are server-defined). `_normalize_status()` and
`_extract_fill_info()` below are therefore a best-effort normalization
layer using multiple plausible key names, verified against the SDK's own
constants/docstrings where those exist (see growwapi.groww.client) but NOT
against a real response, since GROWW_ALLOW_REAL_ORDERS is false and no real
order may be submitted during this development stage. Before this system
is ever trusted with real money, the exact response schema MUST be
confirmed against a real (small, manual) order and this mapping adjusted if
needed -- do not assume it is correct as shipped.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import requests
from growwapi.groww.exceptions import (
    BaseGrowwException,
    GrowwAPIAuthenticationException,
    GrowwAPIAuthorisationException,
    GrowwAPIBadRequestException,
    GrowwAPIException,
    GrowwAPIRateLimitException,
    GrowwAPITimeoutException,
)

from backend.db.engine import SessionLocal
from backend.db.repository import OrderRepository

logger = logging.getLogger("orders.order_manager")

# --- Internal order lifecycle states ---
CREATED = "CREATED"
SUBMITTING = "SUBMITTING"
SUBMITTED = "SUBMITTED"
PENDING = "PENDING"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
FILLED = "FILLED"
REJECTED = "REJECTED"
CANCEL_PENDING = "CANCEL_PENDING"
CANCELLED = "CANCELLED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

TERMINAL_STATES = {FILLED, REJECTED, CANCELLED, FAILED}
NON_TERMINAL_STATES = {
    CREATED,
    SUBMITTING,
    SUBMITTED,
    PENDING,
    PARTIALLY_FILLED,
    CANCEL_PENDING,
    UNKNOWN,
}

# --- Error classification categories ---
BROKER_REJECTED = "BROKER_REJECTED"
NETWORK_ERROR = "NETWORK_ERROR"
TIMEOUT = "TIMEOUT"
AUTH_ERROR = "AUTH_ERROR"
INVALID_ORDER = "INVALID_ORDER"
RATE_LIMITED = "RATE_LIMITED"
UNKNOWN_ERROR = "UNKNOWN_ERROR"

# Broker statuses that map to each internal terminal/non-terminal state.
# Best-effort (see module docstring) -- these are plausible names based on
# common broker API conventions, not confirmed against a real Groww response.
_FILLED_ALIASES = {"FILLED", "EXECUTED", "COMPLETE", "COMPLETED"}
_PARTIAL_ALIASES = {"PARTIALLY_FILLED", "PARTIAL", "PARTIAL_FILL"}
_REJECTED_ALIASES = {"REJECTED", "FAILED"}
_CANCELLED_ALIASES = {"CANCELLED", "CANCELED"}
_PENDING_ALIASES = {"OPEN", "PENDING", "NEW", "ACKED", "ACK", "TRIGGER_PENDING", "APPROVED", "PLACED"}


def classify_exception(exc: Exception) -> str:
    """Map a raised exception to an error category using the ACTUAL
    exception hierarchy shipped in growwapi.groww.exceptions (inspected from
    the installed package, not guessed) plus raw `requests` network errors
    that the SDK's own `_request_get`/`_request_post` only partially wraps
    (it converts `requests.Timeout` to GrowwAPITimeoutException but lets
    `requests.ConnectionError` and other network failures propagate as-is).
    """
    if isinstance(exc, GrowwAPITimeoutException):
        return TIMEOUT
    if isinstance(exc, (GrowwAPIAuthenticationException, GrowwAPIAuthorisationException)):
        return AUTH_ERROR
    if isinstance(exc, GrowwAPIRateLimitException):
        return RATE_LIMITED
    if isinstance(exc, GrowwAPIBadRequestException):
        return INVALID_ORDER
    if isinstance(exc, GrowwAPIException):
        # Generic server-reported failure (response envelope status=="FAILURE").
        return BROKER_REJECTED
    if isinstance(exc, BaseGrowwException):
        return UNKNOWN_ERROR
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.RequestException)):
        return NETWORK_ERROR
    return UNKNOWN_ERROR


# Ambiguous outcomes: we do NOT know whether the broker actually received/
# accepted the order. These must never be treated as "not submitted".
_AMBIGUOUS_ERROR_CATEGORIES = {NETWORK_ERROR, TIMEOUT, UNKNOWN_ERROR}
# Unambiguous "the broker did not accept this order" outcomes -- safe to
# mark the order terminal without reconciliation.
_DEFINITELY_NOT_SUBMITTED_CATEGORIES = {AUTH_ERROR, RATE_LIMITED, INVALID_ORDER, BROKER_REJECTED}


def _normalize_status(raw: dict[str, Any]) -> str:
    raw_status = str(
        raw.get("order_status") or raw.get("status") or raw.get("state") or ""
    ).upper()
    if raw_status in _FILLED_ALIASES:
        return FILLED
    if raw_status in _PARTIAL_ALIASES:
        return PARTIALLY_FILLED
    if raw_status in _REJECTED_ALIASES:
        return REJECTED
    if raw_status in _CANCELLED_ALIASES:
        return CANCELLED
    if raw_status in _PENDING_ALIASES:
        return PENDING
    return UNKNOWN


def _extract_fill_info(raw: dict[str, Any]) -> tuple[Optional[int], Optional[int], Optional[float]]:
    """Return (filled_quantity, remaining_quantity, average_fill_price),
    each None if genuinely not present in the response -- never fabricated.
    """

    def _num(*keys: str) -> Optional[float]:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None

    filled = _num("filled_quantity", "quantity_filled", "executed_quantity", "filled_qty")
    remaining = _num("remaining_quantity", "pending_quantity", "remaining_qty")
    avg_price = _num("average_fill_price", "average_price", "avg_price", "trade_price")
    return (
        int(filled) if filled is not None else None,
        int(remaining) if remaining is not None else None,
        avg_price,
    )


def _order_reference_id(decision_id: str) -> str:
    """Deterministic, broker-safe reference id for a given decision.

    Groww's SDK docstring says order_reference_id "defaults to a random
    8-digit number" if omitted; we always pass one so the SAME decision
    always produces the SAME reference id, which is what makes a
    lost-response lookup via get_order_status_by_reference() possible, and
    is a natural broker-side duplicate guard if a resubmission is ever
    attempted for the same decision.
    """
    digest = hashlib.sha256(decision_id.encode()).hexdigest()
    # Keep it numeric-looking and short in case the broker's format
    # expectations resemble the SDK's own "8-digit number" default -- this
    # has not been confirmed against real API validation rules.
    return str(int(digest[:12], 16))[-10:]


@dataclass
class SubmitResult:
    order: dict[str, Any]


class OrderManager:
    """Coordinates order persistence (OrderRepository) with broker calls
    (a GrowwClient-shaped object). Every method opens/closes its own DB
    session, matching the existing project convention (position_store,
    trader.log_event) so callers never have to manage sessions.
    """

    def __init__(self, client: Any, config: Any) -> None:
        self.client = client
        self.config = config

    def _repo(self):
        db = SessionLocal()
        return db, OrderRepository(db)

    # --- Creation / idempotency ---

    def create_order(
        self,
        decision_id: str,
        ticker: str,
        exchange: str,
        side: str,
        order_type: str,
        product: str,
        quantity: int,
        price: float | None,
        mode: str,
        segment: str | None = None,
        setup_reference: dict | None = None,
    ) -> dict[str, Any]:
        """Create (or return the existing) order for this decision.

        Idempotent by decision_id: if a non-terminal order already exists
        for this exact decision, it is returned unchanged instead of
        creating a second one. Callers must generate a decision_id that is
        stable for a single trading decision and changes for a genuinely
        new one (e.g. f"{ticker}:{side}:{pipeline_refreshed_at}").
        """
        db, repo = self._repo()
        try:
            existing = repo.get_open_for_decision(decision_id)
            if existing:
                logger.info("Reusing existing non-terminal order %s for decision %s", existing["id"], decision_id)
                return existing
            return repo.create(
                {
                    "id": str(uuid.uuid4()),
                    "decision_id": decision_id,
                    "order_reference_id": _order_reference_id(decision_id),
                    "ticker": ticker,
                    "exchange": exchange,
                    "segment": segment or self.config.order_segment,
                    "side": side,
                    "order_type": order_type,
                    "product": product,
                    "requested_quantity": quantity,
                    "remaining_quantity": quantity,
                    "requested_price": price,
                    "status": CREATED,
                    "mode": mode,
                    "setup_reference": setup_reference,
                }
            )
        finally:
            db.close()

    def has_in_flight(self, ticker: str, side: str) -> bool:
        """Persisted (survives restart) duplicate-order guard: is there
        already a non-terminal order for this ticker+side?
        """
        db, repo = self._repo()
        try:
            return repo.get_in_flight_for_ticker_side(ticker, side) is not None
        finally:
            db.close()

    # --- Submission ---

    def submit(self, order: dict[str, Any], reference_price: float) -> dict[str, Any]:
        """Submit a CREATED order. Returns the updated order record.

        Paper mode: no network call exists to fail, so the order is carried
        through the exact same state machine (CREATED -> SUBMITTED ->
        FILLED) using `reference_price` as the simulated fill price, purely
        so paper and live trades produce identically-shaped audit trails and
        exercise the same downstream (position-creation) code path.

        Live mode: never assumes success. An ambiguous failure (timeout /
        network error / unclassified exception) marks the order UNKNOWN
        and immediately attempts one reconciliation lookup by
        order_reference_id -- it does NOT resubmit.
        """
        if order["mode"] == "paper":
            return self._submit_paper(order, reference_price)
        return self._submit_live(order)

    def _submit_paper(self, order: dict[str, Any], price: float) -> dict[str, Any]:
        db, repo = self._repo()
        try:
            now = _now()
            order = repo.update(
                order["id"],
                {
                    "status": FILLED,
                    "broker_order_id": f"PAPER-{uuid.uuid4()}",
                    "filled_quantity": order["requested_quantity"],
                    "remaining_quantity": 0,
                    "average_fill_price": price,
                    "submitted_at": now,
                    "terminal_at": now,
                    "last_checked_at": now,
                },
            )
            return order
        finally:
            db.close()

    def _submit_live(self, order: dict[str, Any]) -> dict[str, Any]:
        db, repo = self._repo()
        try:
            repo.update(order["id"], {"status": SUBMITTING})
            payload = {
                "trading_symbol": order["ticker"],
                "exchange": order["exchange"],
                "segment": order["segment"],
                "product": order["product"],
                "order_type": order["order_type"],
                "transaction_type": order["side"],
                "quantity": order["requested_quantity"],
                "validity": self.config.order_validity,
                "order_reference_id": order["order_reference_id"],
                "price": order.get("requested_price") or 0.0,
            }
            try:
                response = self.client.place_order(
                    payload, timeout=self.config.order_submission_timeout_seconds
                )
            except Exception as exc:  # noqa: BLE001 - classified immediately below
                category = classify_exception(exc)
                logger.warning(
                    "Order submission failed for %s (%s): %s [%s]",
                    order["ticker"], order["side"], exc, category,
                )
                if category in _AMBIGUOUS_ERROR_CATEGORIES:
                    order = repo.update(
                        order["id"],
                        {
                            "status": UNKNOWN,
                            "error_category": category,
                            "error_message": str(exc),
                            "submitted_at": _now(),
                        },
                    )
                    # We genuinely don't know if the broker got it -- check
                    # once via order_reference_id before leaving it for the
                    # periodic reconciliation pass. reconcile() opens its own
                    # session, independent of this method's `db`.
                    return self.reconcile(order)
                else:
                    # INVALID_ORDER/BROKER_REJECTED: the broker evaluated and
                    # rejected the order's content -- REJECTED. AUTH_ERROR/
                    # RATE_LIMITED: an infrastructure-level failure that
                    # never reached order evaluation -- FAILED.
                    rejected = category in (INVALID_ORDER, BROKER_REJECTED)
                    return repo.update(
                        order["id"],
                        {
                            "status": REJECTED if rejected else FAILED,
                            "error_category": category,
                            "error_message": str(exc),
                            "submitted_at": _now(),
                            "terminal_at": _now(),
                        },
                    )

            broker_order_id = (
                response.get("groww_order_id")
                or response.get("order_id")
                or response.get("id")
            )
            raw_status = response.get("order_status") or response.get("status")
            normalized = _normalize_status(response) if raw_status else PENDING
            filled_qty, remaining_qty, avg_price = _extract_fill_info(response)
            changes: dict[str, Any] = {
                "status": normalized if normalized != UNKNOWN else PENDING,
                "broker_order_id": broker_order_id,
                "submitted_at": _now(),
                "last_checked_at": _now(),
            }
            if filled_qty is not None:
                changes["filled_quantity"] = filled_qty
                changes["remaining_quantity"] = max(0, order["requested_quantity"] - filled_qty)
            if avg_price is not None:
                changes["average_fill_price"] = avg_price
            if changes["status"] in TERMINAL_STATES:
                changes["terminal_at"] = _now()
            return repo.update(order["id"], changes)
        finally:
            db.close()

    # --- Fill verification ---

    def poll_until_terminal(self, order: dict[str, Any]) -> dict[str, Any]:
        """Poll broker status until a terminal state or the confirmation
        timeout budget is exhausted. Bounded and non-blocking to the rest of
        the system: callers run this inside a single ticker's own thread
        (see trader.py), so it never stalls other tickers' ticks.

        Leaves the order PENDING/PARTIALLY_FILLED/UNKNOWN in the database if
        the timeout elapses without a terminal state -- it is NOT lost, and
        is picked up by reconcile_pending() on a later loop iteration.
        """
        if order["mode"] == "paper" or order["status"] in TERMINAL_STATES:
            return order
        if not order.get("broker_order_id") and not order.get("order_reference_id"):
            return order

        deadline = time.monotonic() + self.config.order_confirmation_timeout_seconds
        current = order
        while time.monotonic() < deadline:
            current = self.reconcile(current)
            if current["status"] in TERMINAL_STATES:
                return current
            time.sleep(self.config.order_poll_interval_seconds)
        return current

    def reconcile(self, order: dict[str, Any]) -> dict[str, Any]:
        """Single, non-looping broker status check. Safe to call repeatedly
        (e.g. once per autonomous-loop iteration for every non-terminal
        order) without blocking -- each call is one bounded network request.
        """
        if order["mode"] == "paper" or order["status"] in TERMINAL_STATES:
            return order

        db, repo = self._repo()
        try:
            try:
                if order.get("broker_order_id"):
                    raw = self.client.get_order_status(
                        order["broker_order_id"],
                        segment=order["segment"],
                    )
                elif order.get("order_reference_id"):
                    raw = self.client.get_order_status_by_reference(
                        order["order_reference_id"],
                        segment=order["segment"],
                    )
                else:
                    return order
            except Exception as exc:  # noqa: BLE001
                category = classify_exception(exc)
                logger.warning(
                    "Order status check failed for %s: %s [%s]", order["id"], exc, category
                )
                # A failed status check does not change what we know about
                # the order -- leave it UNKNOWN/non-terminal for next time.
                return repo.update(
                    order["id"],
                    {
                        "error_category": category,
                        "error_message": str(exc),
                        "last_checked_at": _now(),
                        "status": UNKNOWN if order["status"] != PARTIALLY_FILLED else order["status"],
                    },
                )

            normalized = _normalize_status(raw)
            filled_qty, remaining_qty, avg_price = _extract_fill_info(raw)
            broker_order_id = raw.get("groww_order_id") or raw.get("order_id") or order.get("broker_order_id")
            changes: dict[str, Any] = {
                "last_checked_at": _now(),
                "broker_order_id": broker_order_id,
            }
            if normalized != UNKNOWN:
                changes["status"] = normalized
            if filled_qty is not None:
                changes["filled_quantity"] = filled_qty
                changes["remaining_quantity"] = (
                    remaining_qty if remaining_qty is not None else max(0, order["requested_quantity"] - filled_qty)
                )
            if avg_price is not None:
                changes["average_fill_price"] = avg_price
            if changes.get("status") in TERMINAL_STATES:
                changes["terminal_at"] = _now()
            return repo.update(order["id"], changes)
        finally:
            db.close()

    def reconcile_all_pending(self) -> list[dict[str, Any]]:
        """Called once per autonomous-loop iteration: advance every
        non-terminal LIVE order a single step. This is what recovers an
        UNKNOWN order left behind by a lost response, and what makes order
        state recoverable across a process restart (orders are persisted,
        so a freshly-started trader sees the same pending rows).
        """
        db, repo = self._repo()
        try:
            pending = [o for o in repo.list_non_terminal() if o["mode"] == "live"]
        finally:
            db.close()
        return [self.reconcile(o) for o in pending]

    # --- Cancellation ---

    def cancel(self, order: dict[str, Any]) -> dict[str, Any]:
        if order["status"] in TERMINAL_STATES or order["mode"] == "paper":
            return order
        db, repo = self._repo()
        try:
            order = repo.update(order["id"], {"status": CANCEL_PENDING})
        finally:
            db.close()
        try:
            self.client.cancel_order(order["broker_order_id"], segment=order["segment"])
        except Exception as exc:  # noqa: BLE001
            category = classify_exception(exc)
            db, repo = self._repo()
            try:
                return repo.update(
                    order["id"], {"error_category": category, "error_message": str(exc)}
                )
            finally:
                db.close()
        return self.reconcile(order)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
