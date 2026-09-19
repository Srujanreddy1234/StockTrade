"""Integration tests for the autonomous trader's order-confirmation flow.

Verifies the invariant from the execution-readiness audit: a real position
must never exist without a broker-confirmed fill, and the setup/risk gates
must be provably impossible to bypass on the way to an order. Uses a fake
Groww client throughout -- GROWW_ALLOW_REAL_ORDERS stays false and no real
network call is ever made.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.autonomous.config import AutonomousConfig
from backend.autonomous.execution import ExecutionEngine
from backend.autonomous.probability_engine import ProbabilitySignal
from backend.autonomous.risk_manager import RiskManager
from backend.autonomous.trader import AutonomousTrader, PipelineSnapshot
from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import OrderDB, PositionDB
from backend.positions.position_store import position_store
from tests.test_order_manager import FakeGrowwClient


def _clean():
    init_db()
    db = SessionLocal()
    db.query(OrderDB).delete()
    db.query(PositionDB).delete()
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def _clean_db():
    _clean()
    yield
    _clean()


@pytest.fixture(autouse=True)
def _event_loop():
    # AutonomousTrader.__init__ creates an asyncio.Event(), which on this
    # Python version needs a current event loop even though it's never
    # awaited outside of run_forever(); pytest doesn't set one by default.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


def _config(tmp_path, **overrides) -> AutonomousConfig:
    defaults = dict(
        watchlist=["TESTCO"],
        state_path=str(tmp_path / "state.json"),
        order_confirmation_timeout_seconds=0.05,
        order_poll_interval_seconds=0.01,
        max_open_positions=3,
        daily_loss_limit_pct=0.03,
        cooldown_minutes=0.0,
        buy_probability_threshold=0.5,
        sell_probability_threshold=0.5,
    )
    defaults.update(overrides)
    return AutonomousConfig(**defaults)


def _trader(config: AutonomousConfig, client=None, live_price: float = 100.0) -> AutonomousTrader:
    trader = AutonomousTrader(config)
    # Tests must be deterministic regardless of the developer's own local
    # .env -- GROWW_ALLOW_REAL_ORDERS is a real, operator-controlled setting
    # (not something these tests should ever read), so mode is always
    # forced explicitly here rather than left to whatever ExecutionEngine
    # detected from the ambient environment at construction time.
    trader.execution.mode = "live" if client is not None else "paper"
    if client is not None:
        trader.execution.order_manager.client = client
        trader.execution.order_manager.config = config
        trader.reconciliation.client = client
        # Live mode now runs pre-submission freshness/deviation/margin checks
        # (_pre_live_submission_checks) that normally rely on _tick_one
        # having populated _last_price_meta and on a real LTP fetch; these
        # tests call _maybe_enter/_maybe_exit directly, so fake both here
        # rather than hitting the network for a non-existent test symbol.
        for ticker in config.watchlist:
            trader._last_price_meta[ticker] = (time.time(), "groww_ltp")
        trader._fetch_ltp_sync = lambda t: (live_price, "groww_ltp")
    return trader


def _ready_signal(buy_p=0.9, sell_p=0.1) -> ProbabilitySignal:
    return ProbabilitySignal(
        buy_probability=buy_p, sell_probability=sell_p, range_position=0.1,
        z_score=-1.0, rsi=30.0, momentum=0.001, ready=True,
    )


def _bullish_snapshot(status="ENTRY") -> PipelineSnapshot:
    return PipelineSnapshot(
        status=status, direction="bullish" if status != "NO TRADE" else None,
        target1=120.0, invalidation=90.0,
        confluence_score=80.0, confluence_reasons=["market structure regime is bullish"],
        refreshed_at=1234.0,
    )


# --- Setup engine downgrade prevents order ---

def test_setup_downgrade_to_no_trade_prevents_order(tmp_path):
    trader = _trader(_config(tmp_path))
    snapshot = _bullish_snapshot(status="NO TRADE")  # confluence engine downgraded this
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()
    assert position_store.count_open() == 0


# --- Risk failure (kill switch) prevents order ---

def test_kill_switch_prevents_order(tmp_path):
    trader = _trader(_config(tmp_path))
    trader.risk.state.day_start_margin = 100000.0
    trader.risk.state.kill_switch_active = True
    trader.risk.state.kill_switch_reason = "test kill switch"

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()
    assert position_store.count_open() == 0


# --- Live flag off: paper mode never touches the fake "network" client ---

def test_paper_mode_never_calls_broker_client(tmp_path):
    class ExplodingClient:
        def place_order(self, *a, **k):
            raise AssertionError("paper mode must never call place_order")

    trader = _trader(_config(tmp_path))
    trader.execution.order_manager.client = ExplodingClient()
    assert trader.execution.mode == "paper"

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    # Paper fill still happened (through the same state machine), just with
    # no broker call.
    assert position_store.count_open(source="autonomous") == 1


# --- Full paper entry: confirmed fill only, then creates position ---

def test_full_paper_entry_creates_position_with_confirmed_fill(tmp_path):
    trader = _trader(_config(tmp_path))
    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    positions = position_store.list_all()
    assert len(positions) == 1
    assert positions[0]["source"] == "autonomous"
    assert positions[0]["entry_price"] == 100.0
    assert positions[0]["quantity"] > 0

    db = SessionLocal()
    order = db.query(OrderDB).first()
    db.close()
    assert order.status == "FILLED"
    assert order.position_id == positions[0]["id"]


# --- Full paper exit: partial then final fill via reduce_quantity ---

def test_paper_exit_closes_position_and_records_realized_pnl(tmp_path):
    trader = _trader(_config(tmp_path))
    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))
    position = position_store.list_all()[0]

    asyncio.run(trader._maybe_exit("TESTCO", 121.0, _ready_signal(buy_p=0.1, sell_p=0.9), position))

    closed = position_store.get(position["id"])
    assert closed["status"] == "closed"
    assert closed["exit_reason"] == "target_hit"
    assert trader.risk.state.realized_pnl_today > 0


# --- Duplicate-order guard blocks a second live BUY while one is in flight ---

def test_in_flight_order_blocks_duplicate_live_entry(tmp_path):
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW1", "order_status": "PENDING"}]
    trader = _trader(_config(tmp_path), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 1
    db.close()
    assert position_store.count_open() == 0  # still PENDING, not filled

    # A second tick arrives before the first order resolved.
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 1  # no duplicate order submitted
    db.close()
    assert len(client.placed_payloads) == 1


# --- Reconciliation later confirms a fill and finalizes the position ---

def test_reconciliation_finalizes_position_after_pending_order_fills(tmp_path):
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW2", "order_status": "PENDING"}]
    client.status_by_id["GRW2"] = [{"order_status": "PENDING"}]  # never resolves within the bounded poll
    trader = _trader(_config(tmp_path), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))
    assert position_store.count_open() == 0

    # Now the broker confirms the fill on a later reconciliation pass.
    client.status_by_id["GRW2"] = [
        {"order_status": "EXECUTED", "filled_quantity": 5, "remaining_quantity": 0, "average_fill_price": 101.0}
    ]
    advanced = trader.execution.reconcile_pending_orders()
    for order in advanced:
        trader._finalize_order(order)

    positions = position_store.list_all()
    assert len(positions) == 1
    assert positions[0]["entry_price"] == 101.0  # broker-confirmed price, not the 100.0 signal price


# --- Reconciliation mismatch blocks new entries on that ticker ---

def test_critical_reconciliation_mismatch_blocks_new_entry(tmp_path):
    client = FakeGrowwClient()
    trader = _trader(_config(tmp_path), client=client)
    trader._critical_mismatch_tickers = {"TESTCO"}

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()
    assert position_store.count_open() == 0


# --- Stale market data blocks a live entry ---

def test_stale_market_data_blocks_live_entry(tmp_path):
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW8", "order_status": "PENDING"}]
    trader = _trader(_config(tmp_path, max_market_data_age_seconds=5.0), client=client)
    # Simulate a quote observed well outside the freshness window.
    trader._last_price_meta["TESTCO"] = (time.time() - 60.0, "groww_ltp")

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()


def test_yfinance_fallback_source_blocks_live_entry(tmp_path):
    client = FakeGrowwClient()
    trader = _trader(_config(tmp_path), client=client)
    # The last observed quote came from the delayed fallback, not a live
    # broker quote -- must never be trusted for a real order.
    trader._last_price_meta["TESTCO"] = (time.time(), "yfinance_fallback")

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()


# --- Price deviation blocks a live entry ---

def test_price_deviation_beyond_tolerance_blocks_live_entry(tmp_path):
    client = FakeGrowwClient()
    trader = _trader(
        _config(tmp_path, max_entry_price_deviation_pct=0.5), client=client, live_price=103.0,
    )  # price moved 3% since the signal was evaluated

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()


def test_price_within_deviation_tolerance_allows_live_entry(tmp_path):
    client = FakeGrowwClient()
    client.place_order_queue = [{"groww_order_id": "GRW9", "order_status": "PENDING"}]
    trader = _trader(
        _config(tmp_path, max_entry_price_deviation_pct=1.0), client=client, live_price=100.2,
    )  # 0.2% move, within tolerance

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 1
    db.close()


# --- Insufficient margin on re-check blocks a live entry ---

def test_insufficient_margin_on_recheck_blocks_live_entry(tmp_path):
    class LowMarginClient(FakeGrowwClient):
        def get_margin(self):
            return {"available_margin": 1.0}  # not enough for even 1 share

    client = LowMarginClient()
    trader = _trader(_config(tmp_path), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()
