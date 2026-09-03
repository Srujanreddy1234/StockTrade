"""Tests for Step 3: backtest realism."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.backtesting.backtest_engine import run_backtest


def _make_entry_df():
    idx = pd.date_range("2026-01-01", periods=20, freq="D")
    data = {
        "open": [100.0] * 20,
        "high": [105.0] * 20,
        "low": [95.0] * 20,
        "close": [102.0] * 20,
        "volume": [1000.0] * 20,
        "status": ["NO TRADE"] * 20,
        "direction": ["neutral"] * 20,
        "entry_zone_low": [None] * 20,
        "entry_zone_high": [None] * 20,
        "target1": [None] * 20,
        "invalidation": [None] * 20,
        "atr": [5.0] * 20,
    }
    df = pd.DataFrame(data, index=idx)
    df.loc[df.index[5], "status"] = "ENTRY"
    df.loc[df.index[5], "direction"] = "bullish"
    df.loc[df.index[5], "entry_zone_high"] = 103.0
    df.loc[df.index[5], "entry_zone_low"] = 99.0
    df.loc[df.index[5], "target1"] = 110.0
    df.loc[df.index[5], "invalidation"] = 95.0

    for i in range(6, 12):
        df.loc[df.index[i], "high"] = 115.0
        df.loc[df.index[i], "low"] = 98.0
    return df


def test_backtest_defaults_preserve_behavior():
    df = _make_entry_df()
    metrics = run_backtest(df, brokerage_per_order=0, stt_percent=0, other_charges_percent=0)
    assert metrics["total_trades"] == 1
    assert metrics["wins"] == 1
    assert metrics["average_cost_adjusted_return"] == metrics["average_return"]


def test_slippage_worsens_returns():
    df = _make_entry_df()
    metrics_slip = run_backtest(df, slippage_bps=50)
    metrics_no_slip = run_backtest(df, slippage_bps=0)
    assert len(metrics_slip["trades"]) == 1
    assert metrics_slip["trades"][0]["return_pct"] < metrics_no_slip["trades"][0]["return_pct"]


def test_brokerage_reduces_net_return():
    df = _make_entry_df()
    metrics_no_cost = run_backtest(df, brokerage_per_order=0)
    metrics_cost = run_backtest(df, brokerage_per_order=100, stt_percent=0.1, other_charges_percent=0.05)
    assert len(metrics_cost["trades"]) == 1
    assert metrics_cost["average_cost_adjusted_return"] < metrics_no_cost["average_return"]


def test_position_sizing_changes_position_size():
    df = _make_entry_df()
    metrics = run_backtest(df, capital=100000, risk_per_trade_pct=1.0)
    trade = metrics["trades"][0]
    assert trade["position_size"] > 0
    assert trade["return_abs"] == pytest.approx(
        trade["return_pct"] / 100.0 * trade["entry_price"] * trade["position_size"],
        abs=1e-2,
    )


def test_trailing_stop_locks_profit():
    idx = pd.date_range("2026-01-01", periods=20, freq="D")
    data = {
        "open": [100.0] * 20,
        "high": [105.0] * 20,
        "low": [98.0] * 20,
        "close": [102.0] * 20,
        "volume": [1000.0] * 20,
        "status": ["NO TRADE"] * 20,
        "direction": ["neutral"] * 20,
        "entry_zone_low": [None] * 20,
        "entry_zone_high": [None] * 20,
        "target1": [None] * 20,
        "invalidation": [None] * 20,
        "atr": [5.0] * 20,
    }
    df = pd.DataFrame(data, index=idx)
    df.loc[df.index[5], "status"] = "ENTRY"
    df.loc[df.index[5], "direction"] = "bullish"
    df.loc[df.index[5], "entry_zone_high"] = 103.0
    df.loc[df.index[5], "entry_zone_low"] = 99.0
    df.loc[df.index[5], "target1"] = 110.0
    df.loc[df.index[5], "invalidation"] = 95.0

    df.loc[df.index[10], "high"] = 108.0
    df.loc[df.index[10], "low"] = 100.0
    df.loc[df.index[11], "high"] = 108.0
    df.loc[df.index[11], "low"] = 100.0
    df.loc[df.index[11], "close"] = 100.0

    for i in range(6, 10):
        df.loc[df.index[i], "high"] = 106.0
        df.loc[df.index[i], "low"] = 102.0

    metrics = run_backtest(df, trailing_stop_bps=200)
    assert metrics["total_trades"] == 1
    assert metrics["trades"][0]["exit_reason"] == "invalid_hit"
    assert metrics["trades"][0]["exit_price"] > 103.0
