"""Explicit safety tests for the PAPER/LIVE boundary.

Mirrors the invariant the whole execution-readiness design rests on:
backend.groww.auth.is_real_trading_enabled() is the ONLY thing that
decides whether an order reaches the real broker (OrderManager.submit()
branches on order['mode'], which is set from ExecutionEngine.mode, which
is set from this one function at construction time). If this function is
ever wrong, every downstream safety check is moot -- so it gets tested
exhaustively here, not just incidentally via higher-level tests.
"""

from __future__ import annotations

import pytest

from backend.autonomous.config import AutonomousConfig
from backend.autonomous.execution import ExecutionEngine
from backend.groww.auth import is_real_trading_enabled
from backend.orders.order_manager import FILLED, OrderManager
from tests.test_order_manager import FakeGrowwClient


class _ExplodingClient(FakeGrowwClient):
    """Raises on every network-shaped broker call -- used to prove paper
    mode genuinely cannot reach the broker, not just that it happens not
    to in the current test data."""

    def place_order(self, *a, **k):
        raise AssertionError("paper mode must never call place_order")

    def get_order_status(self, *a, **k):
        raise AssertionError("paper mode must never call get_order_status")

    def cancel_order(self, *a, **k):
        raise AssertionError("paper mode must never call cancel_order")

    def get_margin(self, *a, **k):
        raise AssertionError("paper mode must never call get_margin")


# --- is_real_trading_enabled(): the single source of truth ---

@pytest.mark.parametrize("value", [None, "", "false", "False", "FALSE", "0", "no", "off", "garbage", "enabled-ish"])
def test_missing_or_falsy_or_unrecognized_values_default_to_safe(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("GROWW_ALLOW_REAL_ORDERS", raising=False)
    else:
        monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", value)
    assert is_real_trading_enabled() is False


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES", "y"])
def test_only_explicit_recognized_true_values_enable_live(monkeypatch, value):
    monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", value)
    assert is_real_trading_enabled() is True


def test_restart_with_no_env_var_set_defaults_to_paper(monkeypatch):
    """Simulates a fresh process start where the env var was never
    exported at all (e.g. a deployment that forgot it, or a shell that
    didn't source .env) -- must default to the safe side, never LIVE."""
    monkeypatch.delenv("GROWW_ALLOW_REAL_ORDERS", raising=False)
    config = AutonomousConfig(watchlist=["TESTCO"])
    engine = ExecutionEngine(config)
    assert engine.mode == "paper"


def test_explicit_false_produces_paper_mode_engine(monkeypatch):
    monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", "false")
    config = AutonomousConfig(watchlist=["TESTCO"])
    engine = ExecutionEngine(config)
    assert engine.mode == "paper"


def test_explicit_true_produces_live_mode_engine(monkeypatch):
    monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", "true")
    config = AutonomousConfig(watchlist=["TESTCO"])
    engine = ExecutionEngine(config)
    assert engine.mode == "live"


# --- OrderManager.submit(): paper mode cannot reach the broker, period ---

def test_paper_order_never_touches_the_broker_client():
    client = _ExplodingClient()
    config = AutonomousConfig(watchlist=["TESTCO"])
    manager = OrderManager(client, config)
    order = manager.create_order(
        decision_id="TESTCO:BUY:1", ticker="TESTCO", exchange="NSE", side="BUY",
        order_type="MARKET", product="MIS", quantity=10, price=None, mode="paper",
    )
    result = manager.submit(order, reference_price=100.0)
    assert result["status"] == FILLED
    assert result["broker_order_id"].startswith("PAPER-")
    # If _ExplodingClient's place_order had been called, it would have
    # raised and this test would have failed with that AssertionError
    # instead of reaching here.


def test_live_order_does_reach_the_broker_client():
    """Sanity check for the other side: a LIVE-mode order DOES reach
    place_order (proving the paper test above is actually discriminating
    between the two paths, not just always skipping the broker call)."""
    client = FakeGrowwClient()
    client.place_order_queue = [
        {"groww_order_id": "GRW1", "order_status": "EXECUTED", "filled_quantity": 10,
         "remaining_quantity": 0, "average_fill_price": 100.0},
    ]
    config = AutonomousConfig(watchlist=["TESTCO"])
    manager = OrderManager(client, config)
    order = manager.create_order(
        decision_id="TESTCO:BUY:2", ticker="TESTCO", exchange="NSE", side="BUY",
        order_type="MARKET", product="MIS", quantity=10, price=None, mode="live",
    )
    result = manager.submit(order, reference_price=100.0)
    assert result["status"] == FILLED
    assert result["broker_order_id"] == "GRW1"
