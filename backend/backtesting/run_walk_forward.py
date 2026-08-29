"""Walk-forward backtest across a basket of NSE tickers.

For each ticker:
- Loads 5 years of daily data via yfinance
- Splits chronologically into TRAIN (first 80%) and TEST (last 20%)
- Runs the full pipeline + backtest on each split independently
- Aggregates combined metrics across all tickers for each period

No parameters are tuned between train and test -- test is look-only.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from backend.data_engine.loader import load_from_yfinance
from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.indicators.indicator_engine import (
    add_indicators,
    find_swing_points,
    add_trend_read,
    add_support_resistance,
)
from backend.signals.scoring_engine import add_scores
from backend.risk.risk_engine import add_risk_levels
from backend.backtesting.backtest_engine import run_backtest


TICKERS = [
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "ITC.NS",
    "BHARTIARTL.NS",
    "LT.NS",
    "KOTAKBANK.NS",
    "AXISBANK.NS",
    "WIPRO.NS",
    "MARUTI.NS",
    "NTPC.NS",
]

INTERVAL = "1d"
PERIOD = "5y"
TRAIN_FRACTION = 0.8


def run_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_indicators(df)
    df = find_swing_points(df, lookback=5)
    df = add_trend_read(df)
    df = add_support_resistance(df)
    df = add_scores(df)
    df = add_risk_levels(df)
    return df


def split_train_test(df: pd.DataFrame, train_fraction: float = 0.8) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split DataFrame chronologically into train and test sets."""
    df = df.sort_index()
    split_idx = int(len(df) * train_fraction)
    train = df.iloc[:split_idx].copy()
    test = df.iloc[split_idx:].copy()
    return train, test


def run_walk_forward(tickers: List[str]) -> dict:
    train_trades: list[dict] = []
    test_trades: list[dict] = []
    per_ticker: list[dict] = []

    for ticker in tickers:
        try:
            df = load_from_yfinance(ticker, period=PERIOD, interval=INTERVAL)
            df = run_pipeline(df)
        except Exception as e:
            print(f"  WARNING: skipped {ticker} (load/pipeline failed): {e}")
            continue

        train_df, test_df = split_train_test(df, TRAIN_FRACTION)

        try:
            train_metrics = run_backtest(train_df)
            test_metrics = run_backtest(test_df)
        except Exception as e:
            print(f"  WARNING: skipped {ticker} (backtest failed): {e}")
            continue

        train_trades.extend(train_metrics.pop("trades", []))
        test_trades.extend(test_metrics.pop("trades", []))

        per_ticker.append({
            "ticker": ticker,
            "train_rows": len(train_df),
            "test_rows": len(test_df),
            "train_trades": train_metrics["total_trades"],
            "train_wins": train_metrics["wins"],
            "train_losses": train_metrics["losses"],
            "train_unresolved": train_metrics["unresolved"],
            "train_win_rate": train_metrics["win_rate"],
            "train_avg_return": train_metrics["average_return"],
            "test_trades": test_metrics["total_trades"],
            "test_wins": test_metrics["wins"],
            "test_losses": test_metrics["losses"],
            "test_unresolved": test_metrics["unresolved"],
            "test_win_rate": test_metrics["win_rate"],
            "test_avg_return": test_metrics["average_return"],
        })

        print(
            f"  {ticker}: train={train_metrics['total_trades']} trades "
            f"(w={train_metrics['wins']} l={train_metrics['losses']} u={train_metrics['unresolved']}) "
            f"win_rate={train_metrics['win_rate']}% avg_return={train_metrics['average_return']}% | "
            f"test={test_metrics['total_trades']} trades "
            f"(w={test_metrics['wins']} l={test_metrics['losses']} u={test_metrics['unresolved']}) "
            f"win_rate={test_metrics['win_rate']}% avg_return={test_metrics['average_return']}%"
        )

    combined_train = _compute_metrics(train_trades)
    combined_test = _compute_metrics(test_trades)

    return {
        "combined_train": combined_train,
        "combined_test": combined_test,
        "per_ticker": per_ticker,
    }


def _compute_metrics(trades: list[dict]) -> dict:
    total = len(trades)
    wins = [t for t in trades if t["exit_reason"] == "target_hit"]
    losses = [t for t in trades if t["exit_reason"] == "invalid_hit"]
    unresolved = [t for t in trades if t["exit_reason"] == "unresolved"]
    resolved = wins + losses

    win_rate = len(wins) / len(resolved) * 100.0 if resolved else 0.0

    returns = [t["return_pct"] for t in resolved if t["return_pct"] is not None]
    average_return = float(pd.Series(returns).mean()) if returns else 0.0
    average_win = float(pd.Series([t["return_pct"] for t in wins]).mean()) if wins else 0.0
    average_loss = float(pd.Series([t["return_pct"] for t in losses]).mean()) if losses else 0.0

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


if __name__ == "__main__":
    print(f"Running walk-forward basket backtest: {len(TICKERS)} tickers, {INTERVAL} / {PERIOD}")
    print(f"Train/test split: {int((1-TRAIN_FRACTION)*100)}% test")
    print()
    results = run_walk_forward(TICKERS)

    print()
    print("=== COMBINED TRAIN METRICS ===")
    for k, v in results["combined_train"].items():
        if k != "trades":
            print(f"  {k}: {v}")

    print()
    print("=== COMBINED TEST METRICS ===")
    for k, v in results["combined_test"].items():
        if k != "trades":
            print(f"  {k}: {v}")

    print()
    print("=== PER-TICKER BREAKDOWN ===")
    for row in results["per_ticker"]:
        print(
            f"  {row['ticker']}: "
            f"train={row['train_trades']} trades (w={row['train_wins']} l={row['train_losses']} u={row['train_unresolved']}) "
            f"win_rate={row['train_win_rate']}% avg_return={row['train_avg_return']}% | "
            f"test={row['test_trades']} trades (w={row['test_wins']} l={row['test_losses']} u={row['test_unresolved']}) "
            f"win_rate={row['test_win_rate']}% avg_return={row['test_avg_return']}%"
        )

    total_trades = results["combined_train"]["total_trades"] + results["combined_test"]["total_trades"]
    if total_trades < 30:
        print()
        print(
            f"WARNING: total trades across train+test is {total_trades}, "
            "which is still small for drawing firm conclusions."
        )
