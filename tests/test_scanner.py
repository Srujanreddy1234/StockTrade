"""Tests for the universe scanner (backend/autonomous/scanner.py) and its
wiring into the trader's active watchlist.
"""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest

from backend.autonomous.config import AutonomousConfig
from backend.autonomous.scanner import scan_universe
from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import AutonomousEventDB, OrderDB, PositionDB
from backend.positions.position_store import position_store
from tests.test_order_manager import FakeGrowwClient
from tests.test_trader_execution import _config, _trader


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


def _fake_loader_for(rows: dict[str, dict]):
    def _load(ticker: str, period: str, interval: str) -> pd.DataFrame:
        base = ticker.split(".")[0]
        if base not in rows:
            return pd.DataFrame()
        return pd.DataFrame({"close": [100.0]})

    return _load


def _fake_pipeline_for(rows: dict[str, dict]):
    calls = {"i": 0}
    tickers = list(rows.keys())

    def _run(df: pd.DataFrame) -> pd.DataFrame:
        # scan_universe calls load then pipeline once per ticker in order,
        # so correlate by call count rather than df contents (the fake
        # loader above returns an identical-looking frame for every hit).
        ticker = tickers[calls["i"]]
        calls["i"] += 1
        row = rows[ticker]
        return pd.DataFrame(
            {
                "close": [row.get("last_price", 100.0)],
                "confluence_score": [row["score"]],
                "confluence_status": [row["status"]],
                "confluence_direction": [row["direction"]],
            }
        )

    return _run


def test_scan_universe_ranks_bullish_entry_watch_by_score(monkeypatch):
    rows = {
        "AAA": {"score": 80.0, "status": "ENTRY", "direction": "bullish"},
        "BBB": {"score": 95.0, "status": "WATCH", "direction": "bullish"},
        "CCC": {"score": 99.0, "status": "ENTRY", "direction": "bearish"},  # wrong direction
        "DDD": {"score": 70.0, "status": "NO TRADE", "direction": None},  # no trade
    }
    monkeypatch.setattr("backend.autonomous.scanner.load_from_yfinance", _fake_loader_for(rows))
    monkeypatch.setattr("backend.autonomous.scanner.run_pipeline", _fake_pipeline_for(rows))

    config = AutonomousConfig(universe=list(rows.keys()), scan_top_n=5)
    results = scan_universe(config)

    assert [r.ticker for r in results] == ["BBB", "AAA"]
    assert results[0].confluence_score == 95.0


def test_scan_universe_respects_top_n(monkeypatch):
    rows = {
        f"T{i}": {"score": float(i), "status": "ENTRY", "direction": "bullish"}
        for i in range(10)
    }
    monkeypatch.setattr("backend.autonomous.scanner.load_from_yfinance", _fake_loader_for(rows))
    monkeypatch.setattr("backend.autonomous.scanner.run_pipeline", _fake_pipeline_for(rows))

    config = AutonomousConfig(universe=list(rows.keys()), scan_top_n=3)
    results = scan_universe(config)

    assert len(results) == 3
    assert [r.ticker for r in results] == ["T9", "T8", "T7"]


def test_rescan_keeps_open_position_tickers_even_if_not_top_ranked(tmp_path, monkeypatch):
    rows = {
        "OPEN1": {"score": 10.0, "status": "WATCH", "direction": "bullish"},  # low rank
        "WINNER": {"score": 90.0, "status": "ENTRY", "direction": "bullish"},
    }
    monkeypatch.setattr("backend.autonomous.scanner.load_from_yfinance", _fake_loader_for(rows))
    monkeypatch.setattr("backend.autonomous.scanner.run_pipeline", _fake_pipeline_for(rows))

    client = FakeGrowwClient()
    config = _config(
        tmp_path,
        universe=list(rows.keys()),
        scan_top_n=1,  # only WINNER would normally qualify
        watchlist_state_path=str(tmp_path / "watchlist.json"),
    )
    trader = _trader(config, client=client)

    position_store.create(
        {
            "ticker": "OPEN1",
            "interval": "5m",
            "direction": "bullish",
            "entry_price": 100.0,
            "target1": 120.0,
            "invalidation": 90.0,
            "source": "autonomous",
            "quantity": 5,
            "initial_quantity": 5,
            "order_id": "test-order-1",
        }
    )
    assert position_store.count_open(source="autonomous") == 1

    trader._rescan_universe_sync()

    assert "OPEN1" in trader.active_watchlist
    assert "WINNER" in trader.active_watchlist
    assert "OPEN1" in trader.tick_states and "WINNER" in trader.tick_states

    with open(config.watchlist_state_path) as f:
        published = json.load(f)
    assert "OPEN1" in published["active_watchlist"]
    assert "WINNER" in published["active_watchlist"]
    assert published["open_position_tickers"] == ["OPEN1"]
