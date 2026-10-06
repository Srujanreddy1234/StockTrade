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
        **holding_time_stats(result),
        **capital_utilization_stats(result),
        "average_drawdown": round(_average_drawdown(result), 2),
    }


def _average_drawdown(result: "BacktestResult") -> float:
    if not result.equity_curve:
        return 0.0
    peak = result.starting_capital
    drawdowns = []
    for _, eq in result.equity_curve:
        peak = max(peak, eq)
        drawdowns.append(peak - eq)
    return float(np.mean(drawdowns))


def holding_time_stats(result: "BacktestResult") -> dict:
    if not result.trades:
        return {"avg_holding_minutes": None, "median_holding_minutes": None, "max_holding_minutes": None}
    holds = [(t.exit_time - t.entry_time).total_seconds() / 60.0 for t in result.trades]
    return {
        "avg_holding_minutes": round(float(np.mean(holds)), 1),
        "median_holding_minutes": round(float(np.median(holds)), 1),
        "max_holding_minutes": round(float(np.max(holds)), 1),
    }


def capital_utilization_stats(result: "BacktestResult") -> dict:
    """Time-weighted deployed capital, from a sweep over each trade's
    [entry_time, exit_time) interval. An approximation (treats a trade's
    notional as constant and instantaneous at its boundaries) but gives a
    reasonable read on how much of the capital ceiling was actually in use.
    """
    trades = result.trades
    if not trades:
        return {"max_capital_deployed": None, "capital_utilization_pct": None}

    events = []
    for t in trades:
        notional = t.entry_price * t.quantity
        events.append((t.entry_time, notional))
        events.append((t.exit_time, -notional))
    events.sort(key=lambda e: e[0])

    deployed = 0.0
    max_deployed = 0.0
    weighted_sum = 0.0
    prev_time = events[0][0]
    for ts, delta in events:
        weighted_sum += deployed * (ts - prev_time).total_seconds()
        deployed += delta
        max_deployed = max(max_deployed, deployed)
        prev_time = ts
    total_seconds = (events[-1][0] - events[0][0]).total_seconds()
    avg_deployed = weighted_sum / total_seconds if total_seconds > 0 else max_deployed

    return {
        "max_capital_deployed": round(max_deployed, 2),
        "capital_utilization_pct": round(avg_deployed / result.starting_capital * 100, 2),
    }


def breakdown_by_ticker(result: "BacktestResult") -> list[dict]:
    by_ticker: dict = defaultdict(list)
    for t in result.trades:
        by_ticker[t.ticker].append(t.net_pnl)
    rows = []
    for ticker, pnls in sorted(by_ticker.items(), key=lambda kv: -sum(kv[1])):
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins)
        gross_loss = -sum(losses)
        rows.append({
            "ticker": ticker,
            "trades": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 3),
            "net_pnl": round(sum(pnls), 2),
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
            "avg_trade": round(sum(pnls) / len(pnls), 2),
        })
    return rows


def breakdown_by_exit_reason(result: "BacktestResult") -> list[dict]:
    by_reason: dict = defaultdict(list)
    for t in result.trades:
        by_reason[t.exit_reason].append(t.net_pnl)
    rows = []
    for reason, pnls in sorted(by_reason.items(), key=lambda kv: -sum(kv[1])):
        rows.append({
            "exit_reason": reason,
            "trades": len(pnls),
            "net_pnl": round(sum(pnls), 2),
            "avg_trade": round(sum(pnls) / len(pnls), 2),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
        })
    return rows


_SCORE_BUCKETS = [(90, 101), (80, 90), (70, 80), (60, 70), (50, 60), (0, 50)]


def breakdown_by_score_bucket(result: "BacktestResult") -> list[dict]:
    rows = []
    for lo, hi in _SCORE_BUCKETS:
        bucket_trades = [
            t for t in result.trades
            if t.signal_score is not None and lo <= t.signal_score < hi
        ]
        if not bucket_trades:
            continue
        pnls = [t.net_pnl for t in bucket_trades]
        wins = [p for p in pnls if p > 0]
        rows.append({
            "score_range": f"{lo}-{hi - 1}",
            "trades": len(bucket_trades),
            "win_rate": round(len(wins) / len(bucket_trades), 3),
            "net_pnl": round(sum(pnls), 2),
            "avg_trade": round(sum(pnls) / len(bucket_trades), 2),
        })
    missing_score = sum(1 for t in result.trades if t.signal_score is None)
    if missing_score:
        rows.append({"score_range": "unknown", "trades": missing_score, "note": "signal_score not recorded"})
    return rows
