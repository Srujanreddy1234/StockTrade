"""Performance metrics computed from a BacktestResult.

Covers the core subset of Section 4 of the specification -- win/loss
statistics, profit factor, drawdown, consecutive streaks, expectancy, and
risk-adjusted ratios computed from daily returns. Walk-forward validation
and Monte Carlo analysis are separate, larger pieces of work (see
backend/backtest/engine.py's module docstring and the project roadmap) and
are intentionally NOT bundled into this first pass.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from backend.backtest.engine import BacktestResult


def compute_metrics(result: BacktestResult) -> dict:
    trades = result.trades
    if not trades:
        return {
            "total_trades": 0,
            "skipped_tickers": result.skipped_tickers,
            "note": "No trades were generated for this period/universe/config.",
        }

    net_pnls = [t.net_pnl for t in trades]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]

    gross_profit = sum(wins)
    gross_loss = -sum(losses)  # positive number
    net_profit = sum(net_pnls)
    total_costs = sum(t.costs for t in trades)

    # Max drawdown over the trade-by-trade equity curve.
    peak = result.starting_capital
    max_drawdown = 0.0
    equity = result.starting_capital
    for _, eq in result.equity_curve:
        equity = eq
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)

    # Consecutive win/loss streaks, in trade-close order.
    max_consec_wins = max_consec_losses = cur_wins = cur_losses = 0
    for p in net_pnls:
        if p > 0:
            cur_wins += 1
            cur_losses = 0
        else:
            cur_losses += 1
            cur_wins = 0
        max_consec_wins = max(max_consec_wins, cur_wins)
        max_consec_losses = max(max_consec_losses, cur_losses)

    # Daily P&L buckets, from trade CLOSE dates.
    daily_pnl: dict = defaultdict(float)
    for t in trades:
        daily_pnl[t.exit_time.date()] += t.net_pnl
    daily_returns = np.array(
        [pnl / result.starting_capital for pnl in daily_pnl.values()]
    ) if daily_pnl else np.array([])

    profitable_days = sum(1 for v in daily_pnl.values() if v > 0)
    losing_days = sum(1 for v in daily_pnl.values() if v < 0)
    breakeven_days = sum(1 for v in daily_pnl.values() if v == 0)

    sharpe = sortino = None
    if daily_returns.size > 1 and daily_returns.std(ddof=1) > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std(ddof=1) * math.sqrt(252))
    downside = daily_returns[daily_returns < 0]
    if daily_returns.size > 1 and downside.size > 0 and downside.std(ddof=1) > 0:
        sortino = float(daily_returns.mean() / downside.std(ddof=1) * math.sqrt(252))

    calmar = None
    if max_drawdown > 0 and daily_returns.size > 0:
        annualized_return = daily_returns.mean() * 252
        calmar = float(annualized_return / (max_drawdown / result.starting_capital))

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(trades), 4),
        "avg_win": round(float(np.mean(wins)), 2) if wins else 0.0,
        "avg_loss": round(float(np.mean(losses)), 2) if losses else 0.0,
        "largest_win": round(max(net_pnls), 2),
        "largest_loss": round(min(net_pnls), 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_profit": round(net_profit, 2),
        "total_trading_costs": round(total_costs, 2),
        "expectancy_per_trade": round(net_profit / len(trades), 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct_of_capital": round(max_drawdown / result.starting_capital * 100, 2),
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "trading_days": len(daily_pnl),
        "profitable_days": profitable_days,
        "losing_days": losing_days,
        "breakeven_days": breakeven_days,
        "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
        "sortino_ratio": round(sortino, 3) if sortino is not None else None,
        "calmar_ratio": round(calmar, 3) if calmar is not None else None,
        "worst_day": round(min(daily_pnl.values()), 2) if daily_pnl else None,
        "best_day": round(max(daily_pnl.values()), 2) if daily_pnl else None,
        "percentile_5_daily_return_pct": (
            round(float(np.percentile(daily_returns, 5)) * 100, 3) if daily_returns.size else None
        ),
        "percentile_1_daily_return_pct": (
            round(float(np.percentile(daily_returns, 1)) * 100, 3) if daily_returns.size else None
        ),
        "skipped_tickers": result.skipped_tickers,
    }
