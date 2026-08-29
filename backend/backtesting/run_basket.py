"""Run the backtest engine across a basket of NSE tickers.

Aggregates trades and metrics across all tickers so we can get a
statistically meaningful sample size.
"""

from __future__ import annotations

import sys
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
    "TATAMOTORS.NS",
    "MARUTI.NS",
    "NTPC.NS",
]

INTERVAL = "15m"
PERIOD = "60d"


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


def run_basket(tickers: List[str]) -> dict:
    all_trades: list[dict] = []
    per_ticker: list[dict] = []

    for ticker in tickers:
        try:
            df = load_from_yfinance(ticker, period=PERIOD, interval=INTERVAL)
            df = run_pipeline(df)
            metrics = run_backtest(df)
        except Exception as e:
            print(f"  WARNING: skipped {ticker}: {e}")
            continue

        trades = metrics.pop("trades", [])
        all_trades.extend(trades)

        per_ticker.append(
            {
                "ticker": ticker,
                "rows": len(df),
                "trades": metrics["total_trades"],
                "wins": metrics["wins"],
                "losses": metrics["losses"],
                "unresolved": metrics["unresolved"],
                "win_rate": metrics["win_rate"],
                "average_return": metrics["average_return"],
            }
        )

        print(
            f"  {ticker}: {metrics['total_trades']} trades "
            f"(w={metrics['wins']} l={metrics['losses']} u={metrics['unresolved']}) "
            f"win_rate={metrics['win_rate']}% avg_return={metrics['average_return']}%"
        )

    combined = _compute_metrics(all_trades)
    return {
        "combined": combined,
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
    print(f"Running basket backtest: {len(TICKERS)} tickers, {INTERVAL} / {PERIOD}")
    print()
    results = run_basket(TICKERS)

    combined = results["combined"]
    print()
    print("=== COMBINED METRICS ===")
    for k, v in combined.items():
        if k != "trades":
            print(f"  {k}: {v}")

    print()
    print("=== PER-TICKER BREAKDOWN ===")
    for row in results["per_ticker"]:
        print(
            f"  {row['ticker']}: {row['trades']} trades "
            f"(w={row['wins']} l={row['losses']} u={row['unresolved']}) "
            f"win_rate={row['win_rate']}% avg_return={row['average_return']}%"
        )

    if combined["total_trades"] < 20:
        print()
        print(
            "WARNING: total_trades is under 20. "
            "Win rate / average return are not statistically reliable at this sample size."
        )
