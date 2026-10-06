"""Tests for intraday-only operation: allocated-capital ceiling, no-new-
entries cutoff, forced square-off, and kill-switch no longer blocking exits.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.autonomous.config import AutonomousConfig
from backend.autonomous.market_hours import new_entries_allowed, past_square_off
from backend.autonomous.risk_manager import RiskManager
from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.models import AutonomousEventDB, OrderDB, PositionDB
from backend.positions.position_store import position_store
from tests.test_order_manager import FakeGrowwClient
from tests.test_trader_execution import _bullish_snapshot, _config, _ready_signal, _trader

IST = ZoneInfo("Asia/Kolkata")


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


def _cfg(**overrides) -> AutonomousConfig:
    defaults = dict(no_new_entries_after="15:00", square_off_time="15:15")
    defaults.update(overrides)
    return AutonomousConfig(**defaults)


# --- market_hours helpers ---

def test_new_entries_allowed_before_cutoff():
    cfg = _cfg()
    now = datetime(2026, 10, 6, 14, 30, tzinfo=IST)
    assert new_entries_allowed(cfg, now) is True


def test_new_entries_blocked_at_and_after_cutoff():
    cfg = _cfg()
    assert new_entries_allowed(cfg, datetime(2026, 10, 6, 15, 0, tzinfo=IST)) is False
    assert new_entries_allowed(cfg, datetime(2026, 10, 6, 15, 30, tzinfo=IST)) is False


def test_past_square_off_before_and_after():
    cfg = _cfg()
    assert past_square_off(cfg, datetime(2026, 10, 6, 15, 14, tzinfo=IST)) is False
    assert past_square_off(cfg, datetime(2026, 10, 6, 15, 15, tzinfo=IST)) is True
    assert past_square_off(cfg, datetime(2026, 10, 6, 15, 20, tzinfo=IST)) is True


# --- allocated-capital ceiling ---

def test_position_sizing_capped_at_allocated_capital_even_with_larger_margin(tmp_path):
    cfg = AutonomousConfig(
        state_path=str(tmp_path / "state.json"),
        allocated_capital=20000.0,
        max_capital_per_trade_pct=1.0,  # would size off the full margin without the cap
        max_capital_per_trade_abs=0,  # disable the abs cap to isolate the allocated-capital cap
    )
    risk = RiskManager(cfg)
    # Real account margin is far larger than what the operator allocated.
    cap = risk.max_trade_value(available_margin=5_00_000.0)
    assert cap == 20000.0


def test_daily_loss_limit_based_on_allocated_capital_not_real_margin(tmp_path):
    cfg = AutonomousConfig(
        state_path=str(tmp_path / "state.json"),
        allocated_capital=20000.0,
        daily_loss_limit_pct=0.025,
    )
    risk = RiskManager(cfg)
    risk.ensure_day_started(available_margin=5_00_000.0)
    assert risk.state.day_start_margin == 20000.0  # capped, not the real 500000

    # A 600 loss is 3% of the 20000 ceiling -> should trip; it would be a
    # negligible 0.12% against the real 500000 margin and NOT trip if the
    # cap weren't applied.
    risk.record_realized_pnl(-600.0)
    assert risk.check_kill_switch() is True


# --- trader-level: no new entries after cutoff ---

def test_trader_refuses_new_entry_after_cutoff(tmp_path, monkeypatch):
    client = FakeGrowwClient()
    trader = _trader(_config(tmp_path, no_new_entries_after="00:00"), client=client)
    # no_new_entries_after="00:00" means the cutoff has always passed.
    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))

    db = SessionLocal()
    assert db.query(OrderDB).count() == 0
    db.close()


# --- trader-level: forced square-off ---

def test_force_square_off_closes_all_open_autonomous_positions(tmp_path):
    client = FakeGrowwClient()
    client.place_order_queue = [
        {"groww_order_id": "GRWSQ1", "order_status": "EXECUTED", "filled_quantity": 10, "remaining_quantity": 0, "average_fill_price": 100.0},
        {"groww_order_id": "GRWSQ2", "order_status": "EXECUTED", "filled_quantity": 10, "remaining_quantity": 0, "average_fill_price": 99.0},
    ]
    trader = _trader(_config(tmp_path), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))
    assert position_store.count_open(source="autonomous") == 1

    trader.tick_states["TESTCO"].last_price = 100.5
    asyncio.run(trader._force_square_off())

    assert position_store.count_open(source="autonomous") == 0
    closed = position_store.list_all()[0]
    assert closed["exit_reason"] == "square_off"


def test_maybe_exit_force_closes_past_square_off_regardless_of_target(tmp_path):
    client = FakeGrowwClient()
    client.place_order_queue = [
        {"groww_order_id": "GRWSQ3", "order_status": "EXECUTED", "filled_quantity": 10, "remaining_quantity": 0, "average_fill_price": 100.0},
        {"groww_order_id": "GRWSQ4", "order_status": "EXECUTED", "filled_quantity": 10, "remaining_quantity": 0, "average_fill_price": 101.0},
    ]
    trader = _trader(_config(tmp_path, square_off_time="00:00"), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))
    position = position_store.list_all()[0]

    # Price is nowhere near target1 (120) or invalidation (90), and the
    # sell-probability signal says hold -- only the square-off cutoff
    # (already "passed" since square_off_time="00:00") should force this closed.
    asyncio.run(trader._maybe_exit("TESTCO", 101.0, _ready_signal(buy_p=0.9, sell_p=0.1), position))

    closed = position_store.get(position["id"])
    assert closed["status"] == "closed"
    assert closed["exit_reason"] == "square_off"


# --- kill switch no longer blocks exits ---

def test_kill_switch_active_still_allows_exit(tmp_path):
    client = FakeGrowwClient()
    client.place_order_queue = [
        {"groww_order_id": "GRWK1", "order_status": "EXECUTED", "filled_quantity": 10, "remaining_quantity": 0, "average_fill_price": 100.0},
        {"groww_order_id": "GRWK2", "order_status": "EXECUTED", "filled_quantity": 10, "remaining_quantity": 0, "average_fill_price": 121.0},
    ]
    trader = _trader(_config(tmp_path), client=client)

    snapshot = _bullish_snapshot(status="ENTRY")
    asyncio.run(trader._maybe_enter("TESTCO", 100.0, _ready_signal(), snapshot, available_margin=100000.0))
    position = position_store.list_all()[0]

    # Trip the kill switch directly.
    trader.risk.state.day_start_margin = 100000.0
    trader.risk.state.kill_switch_active = True
    trader.risk.state.kill_switch_reason = "test kill switch"

    # Price hits target1 (120) -- exit must still fire even though the
    # kill switch is active, since it only blocks NEW entries.
    asyncio.run(trader._maybe_exit("TESTCO", 121.0, _ready_signal(buy_p=0.1, sell_p=0.9), position))

    closed = position_store.get(position["id"])
    assert closed["status"] == "closed"
    assert closed["exit_reason"] == "target_hit"
