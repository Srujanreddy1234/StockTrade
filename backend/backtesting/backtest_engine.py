"""Simple backtesting engine for the trading assistant pipeline.

Consumes a DataFrame that has already run through the full pipeline
(candle metrics -> patterns -> indicators -> trend/SR -> scoring -> risk).
Simulates trades from ENTRY signals and computes performance metrics.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def run_backtest(df: pd.DataFrame) -> dict:
    """Simulate trades from ENTRY signals and return performance metrics.

    For each ENTRY row:
    - Opens a simulated trade at entry_zone_high (bullish) or entry_zone_low
      (bearish).
    - Walks forward candle-by-candle until target1 or invalidation is hit,
      or the data runs out.
    - Only one trade per direction at a time (no overlapping entries).

    Returns a metrics dict with aggregate stats and per-trade records.
    """
    df = df.copy()

    required = {
        "status",
        "direction",
        "entry_zone_low",
        "entry_zone_high",
        "target1",
        "invalidation",
        "high",
        "low",
        "close",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")

    # Ensure chronological order.
    df = df.sort_index()

    trades: list[dict] = []
    open_trade: dict | None = None

    for i in range(len(df)):
        row = df.iloc[i]

        # If a trade is already open, check if it should close on this candle.
        if open_trade is not None:
            closed = _try_close_trade(open_trade, row, df.index[i])
            if closed:
                trades.append(closed)
                open_trade = None
            continue

        # No open trade -- consider entering on ENTRY rows only.
        if row["status"] != "ENTRY":
            continue
        if row["direction"] not in ("bullish", "bearish"):
            continue
        if pd.isna(row["target1"]) or pd.isna(row["invalidation"]):
            continue

        open_trade = _open_trade(row, df.index[i])

    # Any trade still open at the end counts as unresolved.
    if open_trade is not None:
        trades.append(_unresolved_trade(open_trade, df.index[-1]))

    return _compute_metrics(trades)


def _open_trade(row: pd.Series, entry_date: pd.Timestamp) -> dict:
    direction = row["direction"]
    entry_price = (
        row["entry_zone_high"] if direction == "bullish" else row["entry_zone_low"]
    )
    return {
        "direction": direction,
        "entry_date": entry_date,
        "entry_price": float(entry_price),
        "target1": float(row["target1"]),
        "invalidation": float(row["invalidation"]),
    }


def _try_close_trade(trade: dict, row: pd.Series, current_date: pd.Timestamp) -> dict | None:
    direction = trade["direction"]
    high = float(row["high"])
    low = float(row["low"])
    target = trade["target1"]
    invalid = trade["invalidation"]

    if direction == "bullish":
        target_hit = high >= target
        invalid_hit = low <= invalid
    else:
        target_hit = low <= target
        invalid_hit = high >= invalid

    if target_hit and invalid_hit:
        # Both levels touched this candle. Use the one that would have
        # been hit first intra-candle. We can't know the true order, so
        # treat this as unresolved.
        return None

    if target_hit:
        exit_price = target
        exit_reason = "target_hit"
    elif invalid_hit:
        exit_price = invalid
        exit_reason = "invalid_hit"
    else:
        return None

    return _close_trade(trade, current_date, exit_price, exit_reason)


def _close_trade(trade: dict, exit_date: pd.Timestamp, exit_price: float, exit_reason: str) -> dict:
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    if direction == "bullish":
        return_pct = (exit_price - entry_price) / entry_price * 100.0
    else:
        return_pct = (entry_price - exit_price) / entry_price * 100.0

    return {
        "entry_date": trade["entry_date"],
        "exit_date": exit_date,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "return_pct": round(return_pct, 4),
        "target1": trade.get("target1"),
        "invalidation": trade.get("invalidation"),
    }


def _unresolved_trade(trade: dict, last_date: pd.Timestamp) -> dict:
    return {
        "entry_date": trade["entry_date"],
        "exit_date": last_date,
        "direction": trade["direction"],
        "entry_price": trade["entry_price"],
        "exit_price": None,
        "exit_reason": "unresolved",
        "return_pct": None,
        "target1": trade.get("target1"),
        "invalidation": trade.get("invalidation"),
    }


def _compute_metrics(trades: list[dict]) -> dict:
    total = len(trades)
    wins = [t for t in trades if t["exit_reason"] == "target_hit"]
    losses = [t for t in trades if t["exit_reason"] == "invalid_hit"]
    unresolved = [t for t in trades if t["exit_reason"] == "unresolved"]
    resolved = wins + losses

    win_rate = len(wins) / len(resolved) * 100.0 if resolved else 0.0

    returns = [t["return_pct"] for t in resolved if t["return_pct"] is not None]
    average_return = float(np.mean(returns)) if returns else 0.0
    average_win = float(np.mean([t["return_pct"] for t in wins])) if wins else 0.0
    average_loss = float(np.mean([t["return_pct"] for t in losses])) if losses else 0.0

    win_returns = [t["return_pct"] for t in wins]
    loss_returns = [t["return_pct"] for t in losses]
    largest_win = max(win_returns) if win_returns else 0.0
    largest_loss = min(loss_returns) if loss_returns else 0.0

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "unresolved": len(unresolved),
        "win_rate": round(win_rate, 2),
        "average_return": round(average_return, 4),
        "average_win": round(average_win, 4),
        "average_loss": round(average_loss, 4),
        "largest_win": round(largest_win, 4),
        "largest_loss": round(largest_loss, 4),
        "trades": trades,
    }
