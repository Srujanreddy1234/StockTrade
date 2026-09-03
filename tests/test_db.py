"""Tests for Step 2: database and persistence layer."""

from __future__ import annotations

import pytest

from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db import models
from backend.db.models import PositionDB
from backend.db.repository import PositionRepository, BacktestRunRepository, ScanHistoryRepository
from backend.positions.position_store import position_store


@pytest.fixture(autouse=True)
def _init_db():
    init_db()
    db = SessionLocal()
    db.query(PositionDB).delete()
    db.query(models.BacktestRunDB).delete()
    db.query(models.ScanHistoryDB).delete()
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(PositionDB).delete()
    db.query(models.BacktestRunDB).delete()
    db.query(models.ScanHistoryDB).delete()
    db.commit()
    db.close()


def test_position_create_and_list():
    pos = position_store.create({
        "ticker": "TCS.NS",
        "interval": "1d",
        "direction": "bullish",
        "entry_price": 100.0,
        "target1": 110.0,
        "invalidation": 90.0,
    })
    assert pos["id"]
    assert pos["status"] == "open"

    all_pos = position_store.list_all()
    assert len(all_pos) == 1
    assert all_pos[0]["ticker"] == "TCS.NS"


def test_position_update_unrealized():
    pos = position_store.create({
        "ticker": "INFY.NS",
        "interval": "1d",
        "direction": "bearish",
        "entry_price": 200.0,
        "target1": 180.0,
        "invalidation": 220.0,
    })
    updated = position_store.update_unrealized(pos["id"], 190.0)
    assert updated["unrealized_return_pct"] == pytest.approx(5.0)


def test_position_close():
    pos = position_store.create({
        "ticker": "RELIANCE.NS",
        "interval": "1d",
        "direction": "bullish",
        "entry_price": 100.0,
        "target1": 110.0,
        "invalidation": 90.0,
    })
    closed = position_store.close(pos["id"], 112.0, "target_hit")
    assert closed["status"] == "closed"
    assert closed["return_pct"] == pytest.approx(12.0)
    assert closed["exit_reason"] == "target_hit"


def test_position_get_missing():
    assert position_store.get("nonexistent") is None


def test_backtest_run_persistence():
    db = SessionLocal()
    repo = BacktestRunRepository(db)
    run = repo.create({
        "id": "run-1",
        "ticker": "basket",
        "interval": "1d",
        "parameters": {"tickers": ["A", "B"]},
        "metrics": {"win_rate": 50.0},
    })
    assert run["id"] == "run-1"
    recent = repo.list_recent(limit=10)
    assert len(recent) == 1
    assert recent[0]["metrics"]["win_rate"] == 50.0
    db.close()


def test_scan_history_persistence():
    db = SessionLocal()
    repo = ScanHistoryRepository(db)
    scan = repo.create({
        "scanned_at": "2026-09-02T00:00:00+00:00",
        "results": [{"ticker": "X", "score": 10}],
        "skipped": [],
    })
    assert scan["count"] == 1
    recent = repo.list_recent(limit=10)
    assert len(recent) == 1
    assert recent[0]["results"][0]["ticker"] == "X"
    db.close()
