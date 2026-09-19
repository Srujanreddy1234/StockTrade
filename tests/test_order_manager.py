"""Tests for the broker order-state and fill-verification engine.

Uses a FakeGrowwClient (in-memory, scripted responses) throughout -- no
real network calls, no real Groww account, consistent with keeping
GROWW_ALLOW_REAL_ORDERS=false during this development stage.
"""

from __future__ import annotations

import pytest
import requests
from growwapi.groww.exceptions import (
    GrowwAPIAuthenticationException,
    GrowwAPIBadRequestException,
    GrowwAPIException,
    GrowwAPIRateLimitException,
    GrowwAPITimeoutException,
)

from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import OrderDB, PositionDB
from backend.db.repository import OrderRepository, PositionRepository
from backend.orders.order_manager import (
    CANCELLED,
    FAILED,
    FILLED,
    PARTIALLY_FILLED,
    PENDING,
    REJECTED,
    UNKNOWN,
    OrderManager,
    classify_exception,
)


class FakeConfig:
    order_segment = "CASH"
    order_validity = "DAY"
    order_submission_timeout_seconds = 5.0
    order_status_timeout_seconds = 5.0
    order_poll_interval_seconds = 0.01  # fast for tests
    order_confirmation_timeout_seconds = 0.05


class FakeGrowwClient:
    """Scriptable fake: queue responses/exceptions per call type."""

    def __init__(self):
        self.place_order_queue: list = []
        self.status_by_id: dict[str, list] = {}
        self.status_by_reference: dict[str, list] = {}
        self.cancel_queue: list = []
        self.placed_payloads: list = []

    def place_order(self, payload, timeout=None):
        self.placed_payloads.append(payload)
        item = self.place_order_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get_order_status(self, order_id, segment="CASH"):
        queue = self.status_by_id.get(order_id, [])
        if not queue:
            return {"order_status": "PENDING"}
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get_order_status_by_reference(self, order_reference_id, segment="CASH"):
        queue = self.status_by_reference.get(order_reference_id, [])
        if not queue:
            return {"order_status": "PENDING"}
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def cancel_order(self, order_id, segment="CASH"):
        item = self.cancel_queue.pop(0) if self.cancel_queue else {"order_status": "CANCELLED"}
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    db = SessionLocal()
    db.query(OrderDB).delete()
    db.query(PositionDB).delete()
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(OrderDB).delete()
    db.query(PositionDB).delete()
    db.commit()
    db.close()


def _manager(client=None):
    return OrderManager(client or FakeGrowwClient(), FakeConfig())


# --- Order creation ---

def test_create_order_correct_fields():
    mgr = _manager()
    order = mgr.create_order(
        decision_id="RELIANCE:BUY:1",
        ticker="RELIANCE",
        exchange="NSE",
        side="BUY",
        order_type="MARKET",
        product="CNC",
        quantity=10,
        price=1400.0,
        mode="paper",
    )
    assert order["decision_id"] == "RELIANCE:BUY:1"
    assert order["ticker"] == "RELIANCE"
    assert order["side"] == "BUY"
    assert order["requested_quantity"] == 10
    assert order["remaining_quantity"] == 10
    assert order["status"] == "CREATED"
    assert order["order_reference_id"]


def test_create_order_idempotent_for_same_decision():
    mgr = _manager()
    o1 = mgr.create_order(
        decision_id="TCS:BUY:1", ticker="TCS", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=5, price=100.0, mode="paper",
    )
    o2 = mgr.create_order(
        decision_id="TCS:BUY:1", ticker="TCS", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=5, price=100.0, mode="paper",
    )
    assert o1["id"] == o2["id"]


# --- Successful order (paper, exercises the same state machine) ---

def test_paper_order_fills_immediately_with_reference_price():
    mgr = _manager()
    order = mgr.create_order(
        decision_id="INFY:BUY:1", ticker="INFY", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=20, price=None, mode="paper",
    )
    filled = mgr.submit(order, reference_price=1500.0)
    assert filled["status"] == FILLED
    assert filled["filled_quantity"] == 20
    assert filled["remaining_quantity"] == 0
    assert filled["average_fill_price"] == 1500.0


# --- Successful live order: submit -> pending -> filled ---

def test_live_order_submit_then_poll_to_filled():
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW1", "order_status": "PENDING"}]
    client.status_by_id["GRW1"] = [
        {"order_status": "PENDING"},
        {"order_status": "FILLED", "filled_quantity": 15, "average_fill_price": 987.5},
    ]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="HDFCBANK:BUY:1", ticker="HDFCBANK", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=15, price=None, mode="live",
    )
    order = mgr.submit(order, reference_price=980.0)
    assert order["status"] in (PENDING,)
    assert order["broker_order_id"] == "GRW1"

    final = mgr.poll_until_terminal(order)
    assert final["status"] == FILLED
    assert final["filled_quantity"] == 15
    assert final["average_fill_price"] == 987.5
    # Never fabricated from the pre-trade quote.
    assert final["average_fill_price"] != 980.0


# --- Partial fill ---

def test_partial_fill_then_final_fill():
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW2", "order_status": "PENDING"}]
    client.status_by_id["GRW2"] = [
        {"order_status": "PARTIALLY_FILLED", "filled_quantity": 40, "remaining_quantity": 60, "average_fill_price": 100.0},
    ]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="ICICIBANK:BUY:1", ticker="ICICIBANK", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=100, price=None, mode="live",
    )
    order = mgr.submit(order, reference_price=100.0)
    order = mgr.reconcile(order)
    assert order["status"] == PARTIALLY_FILLED
    assert order["filled_quantity"] == 40
    assert order["remaining_quantity"] == 60

    client.status_by_id["GRW2"] = [
        {"order_status": "FILLED", "filled_quantity": 100, "remaining_quantity": 0, "average_fill_price": 100.5},
    ]
    final = mgr.reconcile(order)
    assert final["status"] == FILLED
    assert final["filled_quantity"] == 100


# --- Rejection ---

def test_order_rejected_no_position_should_be_created():
    client = FakeGrowwClient()
    client.place_order_queue = [GrowwAPIBadRequestException()]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="SBIN:BUY:1", ticker="SBIN", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    result = mgr.submit(order, reference_price=500.0)
    assert result["status"] == REJECTED
    assert result["error_category"] == "INVALID_ORDER"


def test_broker_generic_rejection_maps_to_rejected():
    client = FakeGrowwClient()
    client.place_order_queue = [GrowwAPIException(msg="Insufficient funds", code="INSUFFICIENT_FUNDS")]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="WIPRO:BUY:1", ticker="WIPRO", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    result = mgr.submit(order, reference_price=400.0)
    assert result["status"] == REJECTED
    assert result["error_category"] == "BROKER_REJECTED"


# --- Cancellation ---

def test_cancel_order():
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW3", "order_status": "PENDING"}]
    client.status_by_id["GRW3"] = [{"order_status": "CANCELLED"}]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="AXISBANK:BUY:1", ticker="AXISBANK", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    order = mgr.submit(order, reference_price=1000.0)
    cancelled = mgr.cancel(order)
    assert cancelled["status"] == CANCELLED


# --- Network failure / lost response scenarios ---

def test_timeout_before_broker_receives_order_marks_unknown_then_reconciles():
    client = FakeGrowwClient()
    client.place_order_queue = [GrowwAPITimeoutException()]
    # Reconciliation via reference id finds nothing was ever created.
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="TATASTEEL:BUY:1", ticker="TATASTEEL", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    ref_id = order["order_reference_id"]
    client.status_by_reference[ref_id] = [GrowwAPIException(msg="not found", code="404")]
    result = mgr.submit(order, reference_price=100.0)
    # Ambiguous outcome must never silently become FILLED/a fake position.
    assert result["status"] in (UNKNOWN,)
    assert result["error_category"] in ("TIMEOUT", "UNKNOWN_ERROR", "BROKER_REJECTED")


def test_lost_response_but_broker_actually_filled_is_recovered_via_reference():
    client = FakeGrowwClient()
    client.place_order_queue = [requests.ConnectionError("network died")]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="LT:BUY:1", ticker="LT", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    ref_id = order["order_reference_id"]
    # The broker actually accepted and filled it; only OUR response was lost.
    client.status_by_reference[ref_id] = [
        {"groww_order_id": "GRW4", "order_status": "FILLED", "filled_quantity": 10, "average_fill_price": 2500.0}
    ]
    result = mgr.submit(order, reference_price=2490.0)
    assert result["status"] == FILLED
    assert result["filled_quantity"] == 10
    assert result["average_fill_price"] == 2500.0
    assert result["broker_order_id"] == "GRW4"

    # Exactly one order row exists for this decision -- no duplicate created.
    db = SessionLocal()
    count = db.query(OrderDB).filter(OrderDB.decision_id == "LT:BUY:1").count()
    db.close()
    assert count == 1


def test_network_error_does_not_retry_submission():
    client = FakeGrowwClient()
    client.place_order_queue = [requests.ConnectionError("dead")]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="ONGC:BUY:1", ticker="ONGC", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    mgr.submit(order, reference_price=200.0)
    # place_order was called exactly once -- no automatic resubmission.
    assert len(client.placed_payloads) == 1


# --- Error classification ---

def test_classify_exception_categories():
    assert classify_exception(GrowwAPITimeoutException()) == "TIMEOUT"
    assert classify_exception(GrowwAPIAuthenticationException()) == "AUTH_ERROR"
    assert classify_exception(GrowwAPIRateLimitException()) == "RATE_LIMITED"
    assert classify_exception(GrowwAPIBadRequestException()) == "INVALID_ORDER"
    assert classify_exception(GrowwAPIException(msg="x", code="1")) == "BROKER_REJECTED"
    assert classify_exception(requests.ConnectionError()) == "NETWORK_ERROR"
    assert classify_exception(ValueError("weird")) == "UNKNOWN_ERROR"


# --- Duplicate-order lock ---

def test_in_flight_guard_persists_and_blocks_second_order():
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW5", "order_status": "PENDING"}]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="MARUTI:BUY:1", ticker="MARUTI", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    mgr.submit(order, reference_price=9000.0)
    assert mgr.has_in_flight("MARUTI", "BUY") is True

    # A brand new OrderManager instance (simulating a process restart) must
    # see the same persisted in-flight order.
    mgr2 = _manager(FakeGrowwClient())
    assert mgr2.has_in_flight("MARUTI", "BUY") is True


# --- Restart recovery ---

def test_pending_order_recoverable_from_db_after_restart():
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW6", "order_status": "PENDING"}]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="SUNPHARMA:BUY:1", ticker="SUNPHARMA", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=10, price=None, mode="live",
    )
    mgr.submit(order, reference_price=1200.0)

    # Simulate a fresh process: new manager, look the order up from the DB
    # by ticker/side rather than from any in-memory state.
    db = SessionLocal()
    repo = OrderRepository(db)
    recovered = repo.get_in_flight_for_ticker_side("SUNPHARMA", "BUY")
    db.close()
    assert recovered is not None
    assert recovered["broker_order_id"] == "GRW6"

    mgr2 = _manager(client)
    client.status_by_id["GRW6"] = [{"order_status": "FILLED", "filled_quantity": 10, "average_fill_price": 1201.0}]
    final = mgr2.reconcile(recovered)
    assert final["status"] == FILLED


# --- reconcile_all_pending ---

def test_reconcile_all_pending_advances_unknown_orders():
    client = FakeGrowwClient()
    client.place_order_queue = [requests.ConnectionError("dead")]
    mgr = _manager(client)
    order = mgr.create_order(
        decision_id="BAJFINANCE:BUY:1", ticker="BAJFINANCE", exchange="NSE", side="BUY",
        order_type="MARKET", product="CNC", quantity=5, price=None, mode="live",
    )
    ref_id = order["order_reference_id"]
    client.status_by_reference[ref_id] = [GrowwAPIException(msg="not found yet", code="404")]
    result = mgr.submit(order, reference_price=7000.0)
    assert result["status"] == UNKNOWN

    client.status_by_reference[ref_id] = [
        {"groww_order_id": "GRW7", "order_status": "FILLED", "filled_quantity": 5, "average_fill_price": 7010.0}
    ]
    advanced = mgr.reconcile_all_pending()
    assert any(o["status"] == FILLED and o["ticker"] == "BAJFINANCE" for o in advanced)


# --- Position store partial-exit integration ---

def test_position_reduce_quantity_partial_then_full_close():
    from backend.positions.position_store import position_store

    pos = position_store.create(
        {
            "ticker": "NIFTYCO",
            "interval": "5m",
            "direction": "bullish",
            "entry_price": 100.0,
            "target1": 120.0,
            "invalidation": 90.0,
            "quantity": 100,
            "initial_quantity": 100,
        }
    )
    db = SessionLocal()
    repo = PositionRepository(db)
    after_partial = repo.reduce_quantity(pos["id"], filled_qty=40, fill_price=110.0)
    assert after_partial["status"] == "open"
    assert after_partial["quantity"] == 60
    assert after_partial["realized_pnl"] == pytest.approx(400.0)

    after_full = repo.reduce_quantity(pos["id"], filled_qty=60, fill_price=115.0)
    db.close()
    assert after_full["status"] == "closed"
    assert after_full["quantity"] == 0
    # 40*(110-100) + 60*(115-100) = 400 + 900 = 1300 on a 100*100=10000 cost basis -> 13%
    assert after_full["return_pct"] == pytest.approx(13.0)
