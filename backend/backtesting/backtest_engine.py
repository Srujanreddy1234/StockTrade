"""Realistic backtesting engine for the trading assistant pipeline.

Extends the simple engine with:
- Slippage (configurable basis points)
- Brokerage / turnover charges
- Fixed-fractional position sizing
- Optional trailing stops
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def run_backtest(
    df: pd.DataFrame,
    *,
    capital: float = 100_000.0,
    risk_per_trade_pct: float = 1.0,
    slippage_bps: float = 0.0,
    brokerage_per_order: float = 0.0,
    stt_percent: float = 0.1,
    other_charges_percent: float = 0.05,
    trailing_stop_bps: float = 0.0,
) -> dict:
    """Simulate trades from ENTRY signals and return performance metrics.

    Parameters
    ----------
    df : DataFrame with pipeline columns.
    capital : Starting capital in currency units.
    risk_per_trade_pct : Fraction of capital to risk per trade (e.g. 1.0 = 1%).
    slippage_bps : Slippage applied to entry and exit prices, in basis points.
    brokerage_per_order : Flat brokerage fee per executed order.
    stt_percent : STT / turnover tax as a fraction of trade value.
    other_charges_percent : Exchange, GST, stamp duty combined as fraction.
    trailing_stop_bps : If >0, trail the stop at this distance (bps) from the
        best favorable price reached so far.

    Returns metrics dict with aggregate stats and per-trade records.
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

    df = df.sort_index()

    trades: list[dict] = []
    open_trade: dict | None = None
    running_capital = capital

    for i in range(len(df)):
        row = df.iloc[i]

        if open_trade is not None:
            closed = _try_close_trade(
                open_trade, row, df.index[i],
                trailing_stop_bps=trailing_stop_bps,
            )
            if closed:
                closed = _apply_costs(
                    closed, brokerage_per_order, stt_percent, other_charges_percent
                )
                running_capital += closed.get("return_abs", 0.0)
                trades.append(closed)
                open_trade = None
            continue

        if row["status"] != "ENTRY":
            continue
        if row["direction"] not in ("bullish", "bearish"):
            continue
        if pd.isna(row["target1"]) or pd.isna(row["invalidation"]):
            continue

        open_trade = _open_trade(row, df.index[i], slippage_bps)

        stop_distance = abs(open_trade["entry_price"] - open_trade["invalidation"])
        if stop_distance <= 0:
            open_trade["position_size"] = 1.0
        else:
            risk_amount = running_capital * (risk_per_trade_pct / 100.0)
            raw_size = risk_amount / stop_distance
            open_trade["position_size"] = float(np.floor(raw_size))

        if open_trade["position_size"] <= 0:
            open_trade["position_size"] = 1.0

    if open_trade is not None:
        unresolved = _unresolved_trade(open_trade, df.index[-1])
        trades.append(unresolved)

    metrics = _compute_metrics(trades)
    metrics["initial_capital"] = round(capital, 2)
    metrics["final_capital"] = round(running_capital, 2)
    metrics["total_net_return"] = round(
        (running_capital - capital) / capital * 100.0, 4
    ) if capital else 0.0
    return metrics


def _apply_slippage(price: float, direction: str, slippage_bps: float) -> float:
    if slippage_bps <= 0:
        return price
    factor = slippage_bps / 10000.0
    if direction == "bullish":
        return price * (1 + factor)
    return price * (1 - factor)


def _apply_costs(trade: dict, brokerage_per_order: float, stt_percent: float, other_charges_percent: float) -> dict:
    entry_price = trade["entry_price"]
    exit_price = trade.get("exit_price")
    position_size = trade.get("position_size", 1.0)

    if exit_price is None or position_size is None or position_size <= 0:
        return trade

    entry_turnover = entry_price * position_size
    exit_turnover = exit_price * position_size

    total_brokerage = 2 * brokerage_per_order
    total_charges = (
        total_brokerage
        + (stt_percent / 100.0) * (entry_turnover + exit_turnover)
        + (other_charges_percent / 100.0) * (entry_turnover + exit_turnover)
    )

    gross_return_pct = trade.get("gross_return_pct", trade.get("return_pct", 0.0))
    gross_return_abs = (gross_return_pct / 100.0) * entry_turnover

    net_return_abs = gross_return_abs - total_charges
    net_return_pct = (net_return_abs / entry_turnover) * 100.0 if entry_turnover else 0.0

    trade["return_pct"] = round(net_return_pct, 4)
    trade["gross_return_pct"] = round(gross_return_pct, 4)
    trade["return_abs"] = round(net_return_abs, 4)
    trade["gross_return_abs"] = round(gross_return_abs, 4)
    trade["costs"] = round(total_charges, 4)
    trade["position_size"] = position_size
    return trade


def _open_trade(row: pd.Series, entry_date: pd.Timestamp, slippage_bps: float) -> dict:
    direction = row["direction"]
    raw_entry = row["entry_zone_high"] if direction == "bullish" else row["entry_zone_low"]
    entry_price = _apply_slippage(float(raw_entry), direction, slippage_bps)
    return {
        "direction": direction,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "target1": float(row["target1"]),
        "invalidation": float(row["invalidation"]),
        "position_size": 0.0,
        "best_price": entry_price,
    }


def _try_close_trade(
    trade: dict,
    row: pd.Series,
    current_date: pd.Timestamp,
    trailing_stop_bps: float = 0.0,
) -> dict | None:
    direction = trade["direction"]
    high = float(row["high"])
    low = float(row["low"])
    target = trade["target1"]
    invalid = trade["invalidation"]
    entry_price = trade["entry_price"]
    best_price = trade.get("best_price", entry_price)

    if direction == "bullish":
        best_price = max(best_price, high)
        target_hit = high >= target
        invalid_hit = low <= invalid
        if trailing_stop_bps > 0:
            trailing_stop = best_price * (1 - trailing_stop_bps / 10000.0)
            invalid = max(invalid, trailing_stop)
            invalid_hit = low <= invalid
    else:
        best_price = min(best_price, low)
        target_hit = low <= target
        invalid_hit = high >= invalid
        if trailing_stop_bps > 0:
            trailing_stop = best_price * (1 + trailing_stop_bps / 10000.0)
            invalid = min(invalid, trailing_stop)
            invalid_hit = high >= invalid

    trade["best_price"] = best_price

    if target_hit and invalid_hit:
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
        "gross_return_pct": round(return_pct, 4),
        "target1": trade.get("target1"),
        "invalidation": trade.get("invalidation"),
        "position_size": trade.get("position_size", 1.0),
        "best_price": trade.get("best_price", entry_price),
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
        "gross_return_pct": None,
        "target1": trade.get("target1"),
        "invalidation": trade.get("invalidation"),
        "position_size": trade.get("position_size", 1.0),
        "best_price": trade.get("best_price", trade["entry_price"]),
    }


def _compute_metrics(trades: list[dict]) -> dict:
    total = len(trades)
    wins = [t for t in trades if t["exit_reason"] == "target_hit"]
    losses = [t for t in trades if t["exit_reason"] == "invalid_hit"]
    unresolved = [t for t in trades if t["exit_reason"] == "unresolved"]
    resolved = wins + losses

    win_rate = len(wins) / len(resolved) * 100.0 if resolved else 0.0

    gross_returns = [t["gross_return_pct"] for t in resolved if t.get("gross_return_pct") is not None]
    average_return = float(np.mean(gross_returns)) if gross_returns else 0.0
    average_win = float(np.mean([t["gross_return_pct"] for t in wins])) if wins else 0.0
    average_loss = float(np.mean([t["gross_return_pct"] for t in losses])) if losses else 0.0

    win_returns = [t["gross_return_pct"] for t in wins]
    loss_returns = [t["gross_return_pct"] for t in losses]
    largest_win = max(win_returns) if win_returns else 0.0
    largest_loss = min(loss_returns) if loss_returns else 0.0

    net_returns = [t["return_pct"] for t in resolved if t.get("return_pct") is not None]
    average_cost_adjusted_return = float(np.mean(net_returns)) if net_returns else 0.0

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
        "average_cost_adjusted_return": round(average_cost_adjusted_return, 4),
        "trades": trades,
    }
