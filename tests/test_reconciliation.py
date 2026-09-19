"""Tests for startup/periodic broker-vs-local position reconciliation.

Uses a fake Groww client -- no real network call, no real account.
"""

from __future__ import annotations

import pytest

from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import AutonomousEventDB, PositionDB
from backend.positions.position_store import position_store
from backend.reconciliation.reconciliation_service import (
    BROKER_ONLY,
    LOCAL_ONLY,
    MATCHED,
    PRICE_MISMATCH,
    QUANTITY_MISMATCH,
    RECONCILIATION_FAILED,
    ReconciliationService,
)


class FakePositionsClient:
    def __init__(self, positions: list[dict] | None = None, raise_exc: Exception | None = None):
        self._positions = positions or []
        self._raise = raise_exc

    def get_positions(self):
        if self._raise:
            raise self._raise
        return self._positions


def _groww_position(symbol: str, quantity: float, average_price: float) -> dict:
    return {
        "trading_symbol": symbol,
        "exchange": "NSE",
        "segment": "CASH",
        "product": "CNC",
        "quantity": quantity,
        "average_price": average_price,
        "overnight_quantity": 0,
        "overnight_average_price": 0,
    }


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    db = SessionLocal()
    db.query(PositionDB).delete()
    db.query(AutonomousEventDB).delete()
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(PositionDB).delete()
    db.query(AutonomousEventDB).delete()
    db.commit()
    db.close()


def _open_local_position(ticker: str, quantity: int, entry_price: float) -> dict:
    return position_store.create(
        {
            "ticker": ticker, "interval": "5m", "direction": "bullish",
            "entry_price": entry_price, "target1": entry_price * 1.1, "invalidation": entry_price * 0.9,
            "source": "autonomous", "quantity": quantity, "initial_quantity": quantity,
        }
    )


def test_matched_local_and_broker_positions():
    _open_local_position("RELIANCE", 10, 1400.0)
    client = FakePositionsClient([_groww_position("RELIANCE", 10, 1400.0)])
    svc = ReconciliationService(client, price_tolerance_pct=1.0)
    results = svc.run("check")
    assert any(r.ticker == "RELIANCE" and r.classification == MATCHED for r in results)
    assert not svc.has_critical_mismatch(results)


def test_local_only_position_detected():
    _open_local_position("TCS", 5, 3500.0)
    client = FakePositionsClient([])  # broker reports nothing
    svc = ReconciliationService(client)
    results = svc.run("check")
    match = next(r for r in results if r.ticker == "TCS")
    assert match.classification == LOCAL_ONLY
    assert svc.has_critical_mismatch(results)
    assert "TCS" in svc.critical_tickers(results)


def test_broker_only_position_detected():
    client = FakePositionsClient([_groww_position("INFY", 20, 1500.0)])
    svc = ReconciliationService(client)
    results = svc.run("check")
    match = next(r for r in results if r.ticker == "INFY")
    assert match.classification == BROKER_ONLY
    assert svc.has_critical_mismatch(results)


def test_quantity_mismatch_detected():
    _open_local_position("HDFCBANK", 10, 1600.0)
    client = FakePositionsClient([_groww_position("HDFCBANK", 6, 1600.0)])
    svc = ReconciliationService(client)
    results = svc.run("check")
    match = next(r for r in results if r.ticker == "HDFCBANK")
    assert match.classification == QUANTITY_MISMATCH
    assert match.local_quantity == 10
    assert match.broker_quantity == 6
    assert svc.has_critical_mismatch(results)


def test_price_mismatch_is_recorded_but_not_critical():
    _open_local_position("ICICIBANK", 10, 1000.0)
    # 5% price difference, above the 1% default tolerance, but quantity matches.
    client = FakePositionsClient([_groww_position("ICICIBANK", 10, 1050.0)])
    svc = ReconciliationService(client, price_tolerance_pct=1.0)
    results = svc.run("check")
    match = next(r for r in results if r.ticker == "ICICIBANK")
    assert match.classification == PRICE_MISMATCH
    assert not svc.has_critical_mismatch(results)


def test_price_within_tolerance_is_matched():
    _open_local_position("WIPRO", 10, 400.0)
    client = FakePositionsClient([_groww_position("WIPRO", 10, 401.5)])  # 0.375% diff
    svc = ReconciliationService(client, price_tolerance_pct=1.0)
    results = svc.run("check")
    match = next(r for r in results if r.ticker == "WIPRO")
    assert match.classification == MATCHED


def test_broker_unreachable_is_treated_as_critical_not_matched():
    _open_local_position("SBIN", 10, 500.0)
    client = FakePositionsClient(raise_exc=ConnectionError("network down"))
    svc = ReconciliationService(client)
    results = svc.run("check")
    assert results[0].classification == RECONCILIATION_FAILED
    assert svc.has_critical_mismatch(results)


def test_reconciliation_events_are_audited():
    _open_local_position("AXISBANK", 5, 1000.0)
    client = FakePositionsClient([])  # LOCAL_ONLY
    svc = ReconciliationService(client)
    svc.run("startup")

    db = SessionLocal()
    events = db.query(AutonomousEventDB).filter(AutonomousEventDB.ticker == "AXISBANK").all()
    db.close()
    assert len(events) == 1
    assert events[0].event_type == "reconciliation_local_only"
    assert "[startup]" in events[0].reason


def test_matched_positions_do_not_spam_the_audit_log():
    _open_local_position("MARUTI", 5, 9000.0)
    client = FakePositionsClient([_groww_position("MARUTI", 5, 9000.0)])
    svc = ReconciliationService(client)
    svc.run("periodic")

    db = SessionLocal()
    events = db.query(AutonomousEventDB).filter(AutonomousEventDB.ticker == "MARUTI").all()
    db.close()
    assert len(events) == 0
