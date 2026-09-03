"""Walk-forward basket backtest + WATCH direction check across a large
NSE ticker basket.

For each ticker:
- Loads daily data via yfinance
- Splits chronologically into TRAIN (first 80%) and TEST (last 20%)
- Runs the full pipeline + backtest on each split
- Also evaluates every WATCH row: look 10 candles forward and check
  whether price moved in the signaled direction by > 1x that row's ATR.

No parameters are tuned between train and test.
"""

from __future__ import annotations

from typing import List

import numpy as np
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
from backend.db.init_db import init_db
from backend.db.repository import BacktestRunRepository
from backend.db.engine import SessionLocal

init_db()


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
    "SUNPHARMA.NS",
    "BAJAJFINSV.NS",
    "HINDUNILVR.NS",
    "POWERGRID.NS",
    "NESTLEIND.NS",
    "TATASTEEL.NS",
    "GRASIM.NS",
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "COALINDIA.NS",
    "M&M.NS",
    "HCLTECH.NS",
    "TECHM.NS",
    "BPCL.NS",
    "IOC.NS",
    "ONGC.NS",
    "CIPLA.NS",
    "DRREDDY.NS",
    "EICHERMOT.NS",
    "HEROMOTOCO.NS",
    "SHREECEM.NS",
    "UPL.NS",
    "DIVISLAB.NS",
    "APOLLOHOSP.NS",
    "BAJAJ-AUTO.NS",
    "TATACONSUM.NS",
]

INTERVAL = "1d"
PERIOD = "5y"
TRAIN_FRACTION = 0.8
WATCH_FORWARD_CANDLES = 10


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
    df = df.sort_index()
    split_idx = int(len(df) * train_fraction)
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def analyze_bearish_watch_by_trend(bearish_rows: list[dict]) -> dict:
    trend_groups: dict[str, dict] = {}
    for row in bearish_rows:
        trend = row.get("trend") or "unknown"
        if trend not in trend_groups:
            trend_groups[trend] = {"total": 0, "correct": 0}
        trend_groups[trend]["total"] += 1
        if row.get("move", 0) < -row.get("atr", 0):
            trend_groups[trend]["correct"] += 1

    def pct(c, t):
        return round(c / t * 100, 2) if t else 0.0

    return {
        trend: {
            "total": stats["total"],
            "correct": stats["correct"],
            "pct": pct(stats["correct"], stats["total"]),
        }
        for trend, stats in trend_groups.items()
    }


def evaluate_watch_direction(df: pd.DataFrame) -> dict:
    """For every WATCH row, check if price moved in the signaled direction
    by more than 1x ATR within the next WATCH_FORWARD_CANDLES candles.

    Neutral rows have no signaled direction and are reported separately;
    they are not included in correct/overall counts.
    """
    df = df.sort_index().reset_index()
    bullish_correct = 0
    bearish_correct = 0
    bullish_total = 0
    bearish_total = 0
    neutral_total = 0
    bullish_rows = []
    bearish_rows = []
    neutral_rows = []

    date_col = "Date" if "Date" in df.columns else df.columns[0]

    for i in range(len(df)):
        if df.loc[i, "status"] != "WATCH":
            continue
        direction = df.loc[i, "direction"]
        atr = df.loc[i, "atr"]
        if pd.isna(atr) or atr <= 0:
            continue
        if i + WATCH_FORWARD_CANDLES >= len(df):
            continue

        start_close = df.loc[i, "close"]
        future_close = df.loc[i + WATCH_FORWARD_CANDLES, "close"]
        move = future_close - start_close
        threshold = atr
        row_info = {
            "idx": i,
            "date": df.loc[i, date_col],
            "direction": direction,
            "trend": df.loc[i, "trend"] if "trend" in df.columns else None,
            "near_resistance": bool(df.loc[i, "near_resistance"]) if "near_resistance" in df.columns else None,
            "pattern": df.loc[i, "pattern"] if "pattern" in df.columns else None,
            "score": round(float(df.loc[i, "score"]), 1) if "score" in df.columns else None,
            "start_close": start_close,
            "future_close": future_close,
            "move": move,
            "atr": atr,
        }

        if direction == "bullish":
            bullish_total += 1
            if move > threshold:
                bullish_correct += 1
            bullish_rows.append(row_info)
        elif direction == "bearish":
            bearish_total += 1
            if move < -threshold:
                bearish_correct += 1
            bearish_rows.append(row_info)
        else:
            neutral_total += 1
            neutral_rows.append(row_info)

    def pct(correct, total):
        return round(correct / total * 100, 2) if total else 0.0

    directional_total = bullish_total + bearish_total
    directional_correct = bullish_correct + bearish_correct

    return {
        "bullish_total": bullish_total,
        "bullish_correct": bullish_correct,
        "bullish_pct": pct(bullish_correct, bullish_total),
        "bearish_total": bearish_total,
        "bearish_correct": bearish_correct,
        "bearish_pct": pct(bearish_correct, bearish_total),
        "neutral_total": neutral_total,
        "neutral_correct": None,
        "neutral_pct": None,
        "overall_directional_total": directional_total,
        "overall_directional_correct": directional_correct,
        "overall_directional_pct": pct(directional_correct, directional_total),
        "bullish_rows": bullish_rows,
        "bearish_rows": bearish_rows,
        "neutral_rows": neutral_rows,
    }


def analyze_losing_trade_gap(df: pd.DataFrame, trade: dict) -> dict:
    """For a losing trade, examine candles 1, 2, and 3 after entry
    to see if the loss happened in one large gap or gradually.
    """
    entry_date = trade["entry_date"]
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    invalidation = trade["invalidation"]

    try:
        entry_idx = df.index.get_loc(entry_date)
    except KeyError:
        return {"error": "entry_date not in df index"}

    candles = []
    for offset in range(1, 4):
        idx = entry_idx + offset
        if idx >= len(df):
            break
        row = df.iloc[idx]
        date = df.index[idx]
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if direction == "bullish":
            ret_pct = (close - entry_price) / entry_price * 100.0
            max_adverse = low - entry_price
            invalidation_breach = low <= invalidation
        else:
            ret_pct = (entry_price - close) / entry_price * 100.0
            max_adverse = entry_price - high
            invalidation_breach = high >= invalidation

        candles.append({
            "offset": offset,
            "date": str(date),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "return_pct": round(ret_pct, 4),
            "max_adverse_move": round(max_adverse, 4),
            "invalidation_breach": invalidation_breach,
        })

    return {
        "entry_date": str(entry_date),
        "direction": direction,
        "entry_price": entry_price,
        "invalidation": invalidation,
        "candles": candles,
    }


def run_walk_forward_basket(tickers: List[str]) -> dict:
    train_trades: list[dict] = []
    test_trades: list[dict] = []
    train_watch: list[dict] = []
    test_watch: list[dict] = []
    per_ticker: list[dict] = []
    failed: list[str] = []
    succeeded: list[str] = []
    train_bearish_watch_details: list[dict] = []
    test_bearish_watch_details: list[dict] = []
    train_losing_trades: list[dict] = []
    test_losing_trades: list[dict] = []

    for ticker in tickers:
        try:
            df = load_from_yfinance(ticker, period=PERIOD, interval=INTERVAL)
            df = run_pipeline(df)
        except Exception as e:
            print(f"  WARNING: skipped {ticker} (load/pipeline failed): {e}")
            failed.append(ticker)
            continue

        succeeded.append(ticker)
        train_df, test_df = split_train_test(df, TRAIN_FRACTION)

        try:
            train_metrics = run_backtest(train_df, capital=100000, risk_per_trade_pct=1.0, slippage_bps=10, brokerage_per_order=20, stt_percent=0.1, other_charges_percent=0.05)
            test_metrics = run_backtest(test_df, capital=100000, risk_per_trade_pct=1.0, slippage_bps=10, brokerage_per_order=20, stt_percent=0.1, other_charges_percent=0.05)
        except Exception as e:
            print(f"  WARNING: skipped {ticker} (backtest failed): {e}")
            failed.append(ticker)
            continue

        train_trades_list = train_metrics.pop("trades", [])
        test_trades_list = test_metrics.pop("trades", [])
        train_trades.extend(train_trades_list)
        test_trades.extend(test_trades_list)

        train_watch_result = evaluate_watch_direction(train_df)
        test_watch_result = evaluate_watch_direction(test_df)
        train_watch.append(train_watch_result)
        test_watch.append(test_watch_result)

        for row in train_watch_result.get("bearish_rows", []):
            row["ticker"] = ticker
            row["split"] = "train"
            train_bearish_watch_details.append(row)
        for row in test_watch_result.get("bearish_rows", []):
            row["ticker"] = ticker
            row["split"] = "test"
            test_bearish_watch_details.append(row)

        for t in train_trades_list:
            if t.get("exit_reason") == "invalid_hit":
                t["ticker"] = ticker
                t["split"] = "train"
                train_losing_trades.append(t)
                gap_info = analyze_losing_trade_gap(train_df, t)
                gap_info["ticker"] = ticker
                gap_info["split"] = "train"
                train_losing_trades[-1]["gap_analysis"] = gap_info
        for t in test_trades_list:
            if t.get("exit_reason") == "invalid_hit":
                t["ticker"] = ticker
                t["split"] = "test"
                test_losing_trades.append(t)
                gap_info = analyze_losing_trade_gap(test_df, t)
                gap_info["ticker"] = ticker
                gap_info["split"] = "test"
                test_losing_trades[-1]["gap_analysis"] = gap_info

        per_ticker.append({
            "ticker": ticker,
            "rows": len(df),
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
            "train_watch_total": train_watch_result["bullish_total"] + train_watch_result["bearish_total"] + train_watch_result["neutral_total"],
            "test_watch_total": test_watch_result["bullish_total"] + test_watch_result["bearish_total"] + test_watch_result["neutral_total"],
        })

        print(
            f"  {ticker}: rows={len(df)} "
            f"train={train_metrics['total_trades']} trades "
            f"(w={train_metrics['wins']} l={train_metrics['losses']} u={train_metrics['unresolved']}) "
            f"win_rate={train_metrics['win_rate']}% avg_return={train_metrics['average_return']}% | "
            f"test={test_metrics['total_trades']} trades "
            f"(w={test_metrics['wins']} l={test_metrics['losses']} u={test_metrics['unresolved']}) "
            f"win_rate={test_metrics['win_rate']}% avg_return={test_metrics['average_return']}%"
        )

    combined_train = _compute_metrics(train_trades)
    combined_test = _compute_metrics(test_trades)

    def aggregate_watch(watch_list):
        bullish_total = sum(w["bullish_total"] for w in watch_list)
        bullish_correct = sum(w["bullish_correct"] for w in watch_list)
        bearish_total = sum(w["bearish_total"] for w in watch_list)
        bearish_correct = sum(w["bearish_correct"] for w in watch_list)
        neutral_total = sum(w["neutral_total"] for w in watch_list)
        directional_total = bullish_total + bearish_total
        directional_correct = bullish_correct + bearish_correct

        def pct(c, t):
            return round(c / t * 100, 2) if t else 0.0

        return {
            "bullish_total": bullish_total,
            "bullish_correct": bullish_correct,
            "bullish_pct": pct(bullish_correct, bullish_total),
            "bearish_total": bearish_total,
            "bearish_correct": bearish_correct,
            "bearish_pct": pct(bearish_correct, bearish_total),
            "neutral_total": neutral_total,
            "neutral_correct": None,
            "neutral_pct": None,
            "overall_directional_total": directional_total,
            "overall_directional_correct": directional_correct,
            "overall_directional_pct": pct(directional_correct, directional_total),
        }

    return {
        "combined_train": combined_train,
        "combined_test": combined_test,
        "combined_train_watch": aggregate_watch(train_watch),
        "combined_test_watch": aggregate_watch(test_watch),
        "per_ticker": per_ticker,
        "succeeded": succeeded,
        "failed": failed,
        "train_bearish_watch_details": train_bearish_watch_details,
        "test_bearish_watch_details": test_bearish_watch_details,
        "train_losing_trades": train_losing_trades,
        "test_losing_trades": test_losing_trades,
        "train_bearish_watch_trend_breakdown": analyze_bearish_watch_by_trend(train_bearish_watch_details),
        "test_bearish_watch_trend_breakdown": analyze_bearish_watch_by_trend(test_bearish_watch_details),
    }


def save_backtest_run(ticker: str, interval: str, parameters: dict, metrics: dict) -> None:
    import uuid
    db = SessionLocal()
    try:
        BacktestRunRepository(db).create({
            "id": str(uuid.uuid4()),
            "ticker": ticker,
            "interval": interval,
            "parameters": _sanitize_for_json(parameters),
            "metrics": _sanitize_for_json(metrics),
        })
    finally:
        db.close()


def _sanitize_for_json(obj):
    import pandas as pd
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    return obj


def _compute_metrics(trades: list[dict]) -> dict:
    total = len(trades)
    wins = [t for t in trades if t["exit_reason"] == "target_hit"]
    losses = [t for t in trades if t["exit_reason"] == "invalid_hit"]
    unresolved = [t for t in trades if t["exit_reason"] == "unresolved"]
    resolved = wins + losses

    win_rate = len(wins) / len(resolved) * 100.0 if resolved else 0.0

    gross_returns = [t["gross_return_pct"] for t in resolved if t.get("gross_return_pct") is not None]
    average_return = float(pd.Series(gross_returns).mean()) if gross_returns else 0.0
    average_win = float(pd.Series([t["gross_return_pct"] for t in wins]).mean()) if wins else 0.0
    average_loss = float(pd.Series([t["gross_return_pct"] for t in losses]).mean()) if losses else 0.0

    win_returns = [t["gross_return_pct"] for t in wins]
    loss_returns = [t["gross_return_pct"] for t in losses]
    largest_win = max(win_returns) if win_returns else 0.0
    largest_loss = min(loss_returns) if loss_returns else 0.0

    net_returns = [t["return_pct"] for t in resolved if t.get("return_pct") is not None]
    average_cost_adjusted_return = float(pd.Series(net_returns).mean()) if net_returns else 0.0

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


if __name__ == "__main__":
    print(f"Running walk-forward basket backtest: {len(TICKERS)} tickers, {INTERVAL} / {PERIOD}")
    print(f"Train/test split: {int((1-TRAIN_FRACTION)*100)}% test")
    print()
    results = run_walk_forward_basket(TICKERS)

    save_backtest_run(
        ticker="basket",
        interval=INTERVAL,
        parameters={
            "tickers": TICKERS,
            "period": PERIOD,
            "train_fraction": TRAIN_FRACTION,
            "watch_forward_candles": WATCH_FORWARD_CANDLES,
        },
        metrics={
            "combined_train": results["combined_train"],
            "combined_test": results["combined_test"],
            "combined_train_watch": results["combined_train_watch"],
            "combined_test_watch": results["combined_test_watch"],
        },
    )

    print()
    print("=== TICKER SUCCESS/FAILURE ===")
    print(f"  Succeeded: {len(results['succeeded'])} / {len(TICKERS)}")
    print(f"  Failed:    {len(results['failed'])} / {len(TICKERS)}")
    if results["failed"]:
        for t in results["failed"]:
            print(f"    - {t}")

    print()
    print("=== COMBINED TRAIN METRICS (trades) ===")
    for k, v in results["combined_train"].items():
        if k == "trades":
            continue
        if k == "average_cost_adjusted_return":
            print(f"  {k}: {v}  (cost-adjusted)")
        else:
            print(f"  {k}: {v}")

    print()
    print("=== COMBINED TEST METRICS (trades) ===")
    for k, v in results["combined_test"].items():
        if k == "trades":
            continue
        if k == "average_cost_adjusted_return":
            print(f"  {k}: {v}  (cost-adjusted)")
        else:
            print(f"  {k}: {v}")

    print()
    print("=== COMBINED TRAIN WATCH DIRECTION CHECK ===")
    for k, v in results["combined_train_watch"].items():
        if k not in ("neutral_correct", "neutral_pct"):
            print(f"  {k}: {v}")

    print()
    print("=== COMBINED TEST WATCH DIRECTION CHECK ===")
    for k, v in results["combined_test_watch"].items():
        if k not in ("neutral_correct", "neutral_pct"):
            print(f"  {k}: {v}")

    print()
    print("=== PER-TICKER BREAKDOWN ===")
    for row in results["per_ticker"]:
        print(
            f"  {row['ticker']}: rows={row['rows']} "
            f"train={row['train_trades']} trades (w={row['train_wins']} l={row['train_losses']} u={row['train_unresolved']}) "
            f"win_rate={row['train_win_rate']}% avg_return={row['train_avg_return']}% | "
            f"test={row['test_trades']} trades (w={row['test_wins']} l={row['test_losses']} u={row['test_unresolved']}) "
            f"win_rate={row['test_win_rate']}% avg_return={row['test_avg_return']}% | "
            f"train_watch={row['train_watch_total']} test_watch={row['test_watch_total']}"
        )

    total_trades = results["combined_train"]["total_trades"] + results["combined_test"]["total_trades"]
    total_watch_directional = results["combined_train_watch"]["overall_directional_total"] + results["combined_test_watch"]["overall_directional_total"]
    total_watch_neutral = results["combined_train_watch"]["neutral_total"] + results["combined_test_watch"]["neutral_total"]
    print()
    print(f"Total trades across train+test: {total_trades}")
    print(f"Total directional WATCH (bullish+bearish) across train+test: {total_watch_directional}")
    print(f"Total neutral WATCH across train+test: {total_watch_neutral}")
    if total_trades < 30:
        print(
            "WARNING: total trades is under 30. "
            "Trade-based metrics are not statistically reliable at this sample size."
        )
    if total_watch_directional < 100:
        print(
            "WARNING: total directional WATCH samples is under 100. "
            "Direction-check metrics are suggestive but not conclusive."
        )

    print()
    print("=== TRAIN BEARISH WATCH DETAILS (sample) ===")
    bearish_train = [r for r in results["train_bearish_watch_details"] if r.get("direction") == "bearish"]
    for row in bearish_train[:20]:
        print(
            f"  {row['ticker']} | {row['date']} | trend={row['trend']} near_res={row['near_resistance']} "
            f"pattern={row['pattern']} score={row['score']} | close={row['start_close']:.2f} -> {row['future_close']:.2f} "
            f"| move={row['move']:.2f} | atr={row['atr']:.2f}"
        )

    print()
    print("=== TRAIN BEARISH WATCH BY TREND ===")
    for trend, stats in results["train_bearish_watch_trend_breakdown"].items():
        print(
            f"  trend={trend}: total={stats['total']} correct={stats['correct']} pct={stats['pct']}%"
        )

    print()
    print("=== TEST BEARISH WATCH BY TREND ===")
    for trend, stats in results["test_bearish_watch_trend_breakdown"].items():
        print(
            f"  trend={trend}: total={stats['total']} correct={stats['correct']} pct={stats['pct']}%"
        )

    print()
    print("=== TEST BEARISH WATCH DETAILS (sample) ===")
    bearish_test = [r for r in results["test_bearish_watch_details"] if r.get("direction") == "bearish"]
    for row in bearish_test[:20]:
        print(
            f"  {row['ticker']} | {row['date']} | trend={row['trend']} near_res={row['near_resistance']} "
            f"pattern={row['pattern']} score={row['score']} | close={row['start_close']:.2f} -> {row['future_close']:.2f} "
            f"| move={row['move']:.2f} | atr={row['atr']:.2f}"
        )

    print()
    print("=== TRAIN LOSING TRADES ===")
    for t in results["train_losing_trades"]:
        gap = t.get("gap_analysis", {})
        candles = gap.get("candles", [])
        candle_strs = []
        for c in candles:
            candle_strs.append(
                f"candle{c['offset']}: {c['date']} O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f} "
                f"ret={c['return_pct']:.2f}% max_adv={c['max_adverse_move']:.2f} breach={c['invalidation_breach']}"
            )
        print(
            f"  {t['ticker']} | {t['entry_date']} -> {t['exit_date']} | "
            f"dir={t['direction']} | entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} | ret={t['return_pct']:.2f}%"
        )
        for cs in candle_strs:
            print(f"    {cs}")

    print()
    print("=== TEST LOSING TRADES ===")
    for t in results["test_losing_trades"]:
        gap = t.get("gap_analysis", {})
        candles = gap.get("candles", [])
        candle_strs = []
        for c in candles:
            candle_strs.append(
                f"candle{c['offset']}: {c['date']} O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f} "
                f"ret={c['return_pct']:.2f}% max_adv={c['max_adverse_move']:.2f} breach={c['invalidation_breach']}"
            )
        print(
            f"  {t['ticker']} | {t['entry_date']} -> {t['exit_date']} | "
            f"dir={t['direction']} | entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} | ret={t['return_pct']:.2f}%"
        )
        for cs in candle_strs:
            print(f"    {cs}")
