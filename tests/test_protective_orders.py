"""Tests for the opt-in broker-side protective order (OCO) creation path.

Grounded in the actual installed growwapi SDK's create_smart_order()
signature (verified via inspect.signature during this development stage) --
uses a fake client, no real network call, no real account.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.orders.protective_orders import cancel_protective_oco, create_protective_oco
from tests.test_order_manager import FakeGrowwClient
from tests.test_trader_execution import _bullish_snapshot, _config, _ready_signal, _trader
from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import AutonomousEventDB, OrderDB, PositionDB


def _clean():
    init_db()
    db = SessionLocal()
    db.query(OrderDB).delete()
    db.query(PositionDB).delete()
    db.query(AutonomousEventDB).delete()
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def _clean_db():
    _clean()
    yield
    _clean()


@pytest.fixture(autouse=True)
def _event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


class SmartOrderClient(FakeGrowwClient):
    def __init__(self):
        super().__init__()
        self.smart_order_calls: list = []
        self.smart_order_response = {"smart_order_id": "oco_abc123"}
        self.smart_order_exception: Exception | None = None

    def create_smart_order(self, **kwargs):
        self.smart_order_calls.append(kwargs)
        if self.smart_order_exception:
            raise self.smart_order_exception
        return self.smart_order_response

    def cancel_smart_order(self, segment, smart_order_type, smart_order_id):
        return {"smart_order_id": smart_order_id, "status": "CANCELLED"}


def test_create_protective_oco_builds_documented_request_shape():
    client = SmartOrderClient()
    create_protective_oco(
        client, ticker="RELIANCE", exchange="NSE", segment="CASH", product="CNC",
        quantity=10, target_price=1450.0, stop_price=1380.0, reference_id="REF1",
    )
    call = client.smart_order_calls[0]
    assert call["smart_order_type"] == "OCO"
    assert call["segment"] == "CASH"
    assert call["product_type"] == "CNC"
    assert call["quantity"] == 10
    assert call["net_position_quantity"] == 10
    assert call["target"]["trigger_price"] == "1450.00"
    assert call["stop_loss"]["trigger_price"] == "1380.00"
    assert call["transaction_type"] == "SELL"


def test_create_protective_oco_rejects_zero_or_unfilled_quantity():
    client = SmartOrderClient()
    with pytest.raises(ValueError):
        create_protective_oco(
            client, ticker="RELIANCE", exchange="NSE", segment="CASH", product="CNC",
            quantity=0, target_price=1450.0, stop_price=1380.0,
        )


def test_cancel_protective_oco_uses_documented_signature():
    client = SmartOrderClient()
    result = cancel_protective_oco(client, "oco_abc123", segment="CASH")
    assert result["status"] == "CANCELLED"


# --- Trader integration: opt-in, off by default ---

def test_protective_order_not_created_when_feature_disabled(tmp_path):
    client = SmartOrderClient()
    client.place_order_queue = [{"groww_order_id": "GRW1", "order_status": "EXECUTED", "filled_quantity": 50, "remaining_quantity": 0, "average_fill_price": 100.0}]
    trader = _trader(_config(tmp_path, use_protective_orders=False), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    assert client.smart_order_calls == []


def test_protective_order_created_on_confirmed_fill_when_enabled(tmp_path):
    client = SmartOrderClient()
    client.place_order_queue = [{"groww_order_id": "GRW2", "order_status": "EXECUTED", "filled_quantity": 50, "remaining_quantity": 0, "average_fill_price": 100.0}]
    trader = _trader(_config(tmp_path, use_protective_orders=True), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    assert len(client.smart_order_calls) == 1
    call = client.smart_order_calls[0]
    assert call["quantity"] == 50  # the ACTUAL filled quantity, not the requested one
    assert call["trading_symbol"] == "TESTCO"

    db = SessionLocal()
    event = (
        db.query(AutonomousEventDB)
        .filter(AutonomousEventDB.event_type == "protective_order_created")
        .first()
    )
    db.close()
    assert event is not None
    assert event.order_id == "oco_abc123"


def test_protective_order_failure_does_not_undo_the_position(tmp_path):
    client = SmartOrderClient()
    client.place_order_queue = [{"groww_order_id": "GRW3", "order_status": "EXECUTED", "filled_quantity": 50, "remaining_quantity": 0, "average_fill_price": 100.0}]
    client.smart_order_exception = RuntimeError("smart order API down")
    trader = _trader(_config(tmp_path, use_protective_orders=True), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    from backend.positions.position_store import position_store
    assert position_store.count_open(source="autonomous") == 1

    db = SessionLocal()
    event = (
        db.query(AutonomousEventDB)
        .filter(AutonomousEventDB.event_type == "protective_order_failed")
        .first()
    )
    db.close()
    assert event is not None


def test_protective_order_not_created_in_paper_mode_even_if_enabled(tmp_path):
    from backend.autonomous.trader import AutonomousTrader

    trader = AutonomousTrader(_config(tmp_path, use_protective_orders=True))
    assert trader.execution.mode == "paper"
    client = SmartOrderClient()
    trader.execution.order_manager.client = client

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    assert client.smart_order_calls == []
