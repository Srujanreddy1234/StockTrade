"""Tests for candidate observability (Phase 1 of the paper-trading work):
every call to _maybe_enter must record a structured 'candidate' event --
entered or rejected with a specific reason -- never a silent return.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.autonomous.trader import AutonomousTrader
from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import AutonomousEventDB, OrderDB, PositionDB
from tests.test_order_manager import FakeGrowwClient
from tests.test_trader_execution import _bullish_snapshot, _config, _ready_signal, _trader


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


def _candidate_events(ticker="TESTCO"):
    db = SessionLocal()
    try:
        return (
            db.query(AutonomousEventDB)
            .filter(AutonomousEventDB.ticker == ticker, AutonomousEventDB.event_type == "candidate")
            .all()
        )
    finally:
        db.close()


def test_structural_rejection_is_logged_with_reason(tmp_path):
    trader = _trader(_config(tmp_path))
    snapshot = _bullish_snapshot(status="NO TRADE")  # fails structural_ok
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    events = _candidate_events()
    assert len(events) == 1
    assert events[0].decision == "rejected"
    assert events[0].rejection_reason == "structural_signal_not_eligible"
    assert events[0].strategy_version


def test_probability_rejection_is_logged_with_reason(tmp_path):
    trader = _trader(_config(tmp_path, buy_probability_threshold=0.99))
    snapshot = _bullish_snapshot(status="ENTRY")
    low_prob_signal = _ready_signal(buy_p=0.1, sell_p=0.1)
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, low_prob_signal, snapshot, available_margin=100000.0))

    events = _candidate_events()
    assert len(events) == 1
    assert events[0].decision == "rejected"
    assert events[0].rejection_reason == "probability_below_threshold"
    assert events[0].confluence_score == snapshot.confluence_score
    assert events[0].probability_threshold == 0.99


def test_entered_candidate_is_logged(tmp_path):
    client = FakeGrowwClient()
    trader = _trader(_config(tmp_path), client=client)
    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    events = _candidate_events()
    assert len(events) == 1
    assert events[0].decision == "entered"
    assert events[0].rejection_reason is None


def test_cutoff_rejection_is_logged(tmp_path):
    trader = _trader(_config(tmp_path, no_new_entries_after="00:00"))
    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    events = _candidate_events()
    assert len(events) == 1
    assert events[0].rejection_reason == "past_no_new_entries_cutoff"
