"""Tests for the two guarantees the persistent-paper-trading setup
(ops/launchd/) depends on:

1. An explicitly-set GROWW_ALLOW_REAL_ORDERS environment variable wins
   over whatever .env says -- this is what lets
   scripts/run_paper_trader.sh force paper mode without ever touching
   the user's real .env file.
2. Candidate/trade data written to the database is visible from a brand
   new session/connection -- i.e. it survives a "process restart" (a new
   Python process opening the same sqlite file sees everything a
   previous process wrote), which is what Task 10 requires.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import AutonomousEventDB
from backend.db.repository import AutonomousEventRepository
from backend.groww.auth import is_real_trading_enabled


def test_env_override_wins_over_dotenv_value(tmp_path, monkeypatch):
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("GROWW_ALLOW_REAL_ORDERS=true\n")

    # Simulate run_paper_trader.sh's `export GROWW_ALLOW_REAL_ORDERS=false`
    # happening BEFORE backend/__init__.py's load_dotenv() runs.
    monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", "false")
    load_dotenv(dotenv_path=str(dotenv_file))  # override=False by default

    assert os.environ["GROWW_ALLOW_REAL_ORDERS"] == "false"
    assert is_real_trading_enabled() is False


def test_dotenv_without_prior_override_would_have_enabled_live(tmp_path, monkeypatch):
    """Negative control: proves the previous test isn't just vacuously
    true because load_dotenv() never applies .env's value at all."""
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("GROWW_ALLOW_REAL_ORDERS=true\n")

    monkeypatch.delenv("GROWW_ALLOW_REAL_ORDERS", raising=False)
    load_dotenv(dotenv_path=str(dotenv_file))

    assert os.environ["GROWW_ALLOW_REAL_ORDERS"] == "true"
    assert is_real_trading_enabled() is True


def _clean():
    init_db()
    db = SessionLocal()
    db.query(AutonomousEventDB).delete()
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def _clean_db():
    _clean()
    yield
    _clean()


def test_candidate_event_survives_a_new_session_restart_simulation():
    db1 = SessionLocal()
    try:
        AutonomousEventRepository(db1).create({
            "ticker": "RESTARTCO", "event_type": "candidate", "mode": "paper",
            "decision": "rejected", "rejection_reason": "probability_below_threshold",
        })
    finally:
        db1.close()

    # A brand new session (what a freshly-started process would open) must
    # see the row a previous "process" wrote -- this is the whole point of
    # using a file-backed database rather than in-memory state.
    db2 = SessionLocal()
    try:
        rows = AutonomousEventRepository(db2).list_recent(limit=10, ticker="RESTARTCO")
    finally:
        db2.close()

    assert len(rows) == 1
    assert rows[0]["decision"] == "rejected"
    assert rows[0]["mode"] == "paper"
