"""Tests for the backtesting engine (backend/backtest/engine.py) and its
metrics (backend/backtest/metrics.py).

Monkeypatches load_from_yfinance + run_pipeline inside the engine module so
these tests exercise the replay/risk/cost logic against crafted bars,
without any real network call or real pipeline computation.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.autonomous.config import AutonomousConfig
from backend.backtest.costs import CostModel, SlippageModel
from backend.backtest.engine import run_backtest
from backend.backtest.metrics import compute_metrics

IST = ZoneInfo("Asia/Kolkata")

_NO_SLIPPAGE = SlippageModel(entry_bps=0.0, exit_bps=0.0)
_NO_COST = CostModel(
    brokerage_flat=0.0, brokerage_pct=0.0, stt_sell_pct=0.0,
    exchange_txn_pct=0.0, sebi_pct=0.0, stamp_duty_buy_pct=0.0, gst_pct=0.0,
)


def _bar(ts_str, o, h, l, c, status="ENTRY", direction="bullish", target1=110.0, invalidation=95.0,
         score=80.0, reasons=None, breakout_event=None, pullback_event=None):
    return {
        "timestamp": pd.Timestamp(ts_str, tz=IST),
        "open": o, "high": h, "low": l, "close": c,
        "confluence_status": status, "confluence_direction": direction,
        "confluence_score": score, "confluence_reasons": reasons or [],
        "target1": target1, "invalidation": invalidation,
        "breakout_event": breakout_event, "pullback_event": pullback_event,
    }


def _patch_single_ticker(monkeypatch, ticker: str, bars: list[dict]):
    df = pd.DataFrame(bars).set_index("timestamp")

    def _fake_load(ticker_arg, config, period, interval):
        base = ticker_arg if "." not in ticker_arg else ticker_arg.split(".")[0]
        if base != ticker:
            return None
        out = df[["open", "high", "low", "close", "confluence_status", "confluence_direction",
                  "confluence_score", "confluence_reasons", "target1", "invalidation",
                  "breakout_event", "pullback_event"]].copy()
        out["ticker"] = ticker
        out["timestamp"] = out.index
        return out.reset_index(drop=True)

    monkeypatch.setattr("backend.backtest.engine._load_and_annotate", _fake_load)


def test_entry_fills_next_bar_open_and_exits_on_target(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100),  # signal bar
        _bar("2026-01-05 09:25", 101, 102, 100, 101),  # entry fills here at open=101
        _bar("2026-01-05 09:30", 108, 111, 107, 110),  # high crosses target1=110 -> exit
    ]
    _patch_single_ticker(monkeypatch, "AAA", bars)

    config = AutonomousConfig(
        allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0,
        max_open_positions=2, cooldown_minutes=0,
    )
    result = run_backtest(config, ["AAA"], period="5d", interval="5m",
                           cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == 101.0
    assert trade.exit_price == 110.0
    assert trade.exit_reason == "target_hit"
    # Sized off the signal bar's close (100), per config.allocated_capital --
    # not the (slightly higher) fill price on the next bar's open.
    assert trade.quantity == 20000 // 100
    assert trade.net_pnl > 0


def test_exit_on_invalidation(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100, target1=110.0, invalidation=95.0),
        _bar("2026-01-05 09:25", 101, 101, 101, 101),
        _bar("2026-01-05 09:30", 97, 98, 94, 96),  # low crosses invalidation=95 -> exit
    ]
    _patch_single_ticker(monkeypatch, "BBB", bars)

    config = AutonomousConfig(
        allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0,
    )
    result = run_backtest(config, ["BBB"], period="5d", interval="5m",
                           cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE)

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "invalidated"
    assert result.trades[0].exit_price == 95.0
    assert result.trades[0].net_pnl < 0


def test_square_off_forces_exit_regardless_of_target(monkeypatch):
    bars = [
        _bar("2026-01-05 14:50", 100, 100, 100, 100, target1=200.0, invalidation=10.0),
        _bar("2026-01-05 14:55", 101, 102, 100, 101),
        _bar("2026-01-05 15:15", 103, 104, 102, 103),  # past square-off time
    ]
    _patch_single_ticker(monkeypatch, "CCC", bars)

    config = AutonomousConfig(allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0)
    result = run_backtest(config, ["CCC"], period="5d", interval="5m",
                           cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE)

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "square_off"


def test_capital_ceiling_caps_position_size_regardless_of_price(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 1000, 1000, 1000, 1000, target1=1100.0, invalidation=900.0),
        _bar("2026-01-05 09:25", 1000, 1000, 1000, 1000),
        _bar("2026-01-05 09:30", 1100, 1101, 1099, 1100),
    ]
    _patch_single_ticker(monkeypatch, "DDD", bars)

    config = AutonomousConfig(
        allocated_capital=5000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0,
    )
    result = run_backtest(config, ["DDD"], period="5d", interval="5m",
                           cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE)

    assert len(result.trades) == 1
    # 5000 capital / 1000 price = 5 shares max, never more even though the
    # trade would have been profitable at any size.
    assert result.trades[0].quantity == 5


def test_no_trades_when_universe_has_no_eligible_tickers(monkeypatch):
    monkeypatch.setattr("backend.backtest.engine._load_and_annotate", lambda *a, **k: None)
    config = AutonomousConfig(allocated_capital=20000.0)
    result = run_backtest(config, ["ZZZ"], period="5d", interval="5m")
    assert result.trades == []
    assert "ZZZ" in result.skipped_tickers


def test_compute_metrics_basic_shape(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100),
        _bar("2026-01-05 09:25", 101, 102, 100, 101),
        _bar("2026-01-05 09:30", 108, 111, 107, 110),
    ]
    _patch_single_ticker(monkeypatch, "FFF", bars)
    config = AutonomousConfig(allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0)
    result = run_backtest(config, ["FFF"], period="5d", interval="5m",
                           cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE)
    metrics = compute_metrics(result)
    assert metrics["total_trades"] == 1
    assert metrics["winning_trades"] == 1
    assert metrics["win_rate"] == 1.0
    assert metrics["net_profit"] > 0


def test_allowed_statuses_excludes_watch_when_entry_only(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100, status="WATCH"),
        _bar("2026-01-05 09:25", 101, 102, 100, 101, status="WATCH"),
        _bar("2026-01-05 09:30", 108, 111, 107, 110, status="WATCH"),
    ]
    _patch_single_ticker(monkeypatch, "GGG", bars)
    config = AutonomousConfig(allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0)

    default_result = run_backtest(config, ["GGG"], period="5d", interval="5m",
                                   cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE)
    assert len(default_result.trades) == 1  # default gate accepts WATCH

    entry_only_result = run_backtest(config, ["GGG"], period="5d", interval="5m",
                                      cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE,
                                      allowed_statuses=("ENTRY",))
    assert len(entry_only_result.trades) == 0  # WATCH-only bars excluded


def test_min_score_gate_filters_low_score_signals(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100, score=16.7),
        _bar("2026-01-05 09:25", 101, 102, 100, 101, score=16.7),
        _bar("2026-01-05 09:30", 108, 111, 107, 110, score=16.7),
    ]
    _patch_single_ticker(monkeypatch, "HHH", bars)
    config = AutonomousConfig(allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0)

    result = run_backtest(config, ["HHH"], period="5d", interval="5m",
                           cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE, min_score=50.0)
    assert len(result.trades) == 0
    assert len(result.raw_signals) == 0


def test_confirmation_bars_requires_signal_to_persist(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100),  # signal bar 1
        _bar("2026-01-05 09:25", 101, 101, 101, 101),  # signal still true -> confirms
        _bar("2026-01-05 09:30", 102, 102, 102, 102),  # entry fills here at open=102
        _bar("2026-01-05 09:35", 108, 111, 107, 110),  # target hit
    ]
    _patch_single_ticker(monkeypatch, "III", bars)
    config = AutonomousConfig(allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0)

    result = run_backtest(config, ["III"], period="5d", interval="5m",
                           cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE, confirmation_bars=1)
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 102.0


def test_breakout_confirmation_entry_ignores_confluence_status(monkeypatch):
    # confluence_status is NO TRADE throughout -- the D4 strategy must
    # trigger purely off breakout_event, never off confluence at all.
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100, status="NO TRADE", direction=None,
             breakout_event="CONFIRMED_BREAKOUT_BULLISH"),
        _bar("2026-01-05 09:25", 101, 102, 100, 101, status="NO TRADE", direction=None),
        _bar("2026-01-05 09:30", 108, 111, 107, 110, status="NO TRADE", direction=None),
    ]
    _patch_single_ticker(monkeypatch, "JJJ", bars)
    config = AutonomousConfig(allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0)

    confluence_result = run_backtest(config, ["JJJ"], period="5d", interval="5m",
                                      cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE)
    assert len(confluence_result.trades) == 0  # the current/default strategy never fires here

    d4_result = run_backtest(config, ["JJJ"], period="5d", interval="5m",
                              cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE,
                              entry_strategy="breakout_confirmation")
    assert len(d4_result.trades) == 1
    assert d4_result.trades[0].entry_price == 101.0
    assert d4_result.trades[0].strategy_version == "d4-breakout-confirmation"


def test_pullback_retest_entry_ignores_confluence_status(monkeypatch):
    bars = [
        _bar("2026-01-05 09:20", 100, 100, 100, 100, status="NO TRADE", direction=None,
             pullback_event="PULLBACK_CONTINUATION_BULLISH"),
        _bar("2026-01-05 09:25", 101, 102, 100, 101, status="NO TRADE", direction=None),
        _bar("2026-01-05 09:30", 108, 111, 107, 110, status="NO TRADE", direction=None),
    ]
    _patch_single_ticker(monkeypatch, "KKK", bars)
    config = AutonomousConfig(allocated_capital=20000.0, max_capital_per_trade_pct=1.0, max_capital_per_trade_abs=0)

    d5_result = run_backtest(config, ["KKK"], period="5d", interval="5m",
                              cost_model=_NO_COST, slippage_model=_NO_SLIPPAGE,
                              entry_strategy="pullback_retest")
    assert len(d5_result.trades) == 1
    assert d5_result.trades[0].entry_price == 101.0
    assert d5_result.trades[0].strategy_version == "d5-pullback-retest"


def test_unknown_entry_strategy_raises(monkeypatch):
    config = AutonomousConfig(allocated_capital=20000.0)
    with pytest.raises(ValueError):
        run_backtest(config, [], entry_strategy="not-a-real-strategy")
