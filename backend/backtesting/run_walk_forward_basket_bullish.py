"""Bullish-only walk-forward validation (measurement ONLY).

Reuses the exact same 40-ticker basket, daily interval, 5y history, and
80/20 chronological train/test split as run_walk_forward_basket.py. It does
NOT modify any scoring, risk, or pipeline logic -- it only measures:

  1. WATCH-direction check restricted to BULLISH rows:
     does price move > 1 ATR upward within 10 candles of a bullish WATCH?
     Reported as bullish_pct for TRAIN (and TEST if train_only=False).

  2. Trade-based backtest restricted to BULLISH-direction ENTRY trades:
     total_trades, win_rate, average_return, average_win, average_loss.

Run with:  python backend/backtesting/run_walk_forward_basket_bullish.py
"""

from __future__ import annotations

from typing import List

from backend.backtesting.run_walk_forward_basket import (
    TICKERS,
    PERIOD,
    INTERVAL,
    TRAIN_FRACTION,
    run_pipeline,
    split_train_test,
    evaluate_watch_direction,
    _compute_metrics,
)
from backend.backtesting.backtest_engine import run_backtest
from backend.data_engine.loader import load_from_yfinance


def _aggregate_watch_bullish(watch_list: List[dict]) -> dict:
    total = sum(w["bullish_total"] for w in watch_list)
    correct = sum(w["bullish_correct"] for w in watch_list)

    def pct(c, t):
        return round(c / t * 100, 2) if t else 0.0

    return {
        "bullish_total": total,
        "bullish_correct": correct,
        "bullish_pct": pct(correct, total),
    }


def run(tickers: List[str], train_only: bool = True) -> dict:
    train_trades: list[dict] = []
    test_trades: list[dict] = []
    train_watch: list[dict] = []
    test_watch: list[dict] = []
    per_ticker: list[dict] = []
    failed: list[str] = []
    succeeded: list[str] = []

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

        train_metrics = run_backtest(train_df, capital=100000, risk_per_trade_pct=1.0, slippage_bps=10, brokerage_per_order=20, stt_percent=0.1, other_charges_percent=0.05)
        train_trades_list = train_metrics.pop("trades", [])
        train_trades.extend(train_trades_list)
        train_watch.append(evaluate_watch_direction(train_df))

        # TRAIN-ONLY discipline: do not compute or look at the TEST split.
        if not train_only:
            test_metrics = run_backtest(test_df, capital=100000, risk_per_trade_pct=1.0, slippage_bps=10, brokerage_per_order=20, stt_percent=0.1, other_charges_percent=0.05)
            test_trades_list = test_metrics.pop("trades", [])
            test_trades.extend(test_trades_list)
            test_watch.append(evaluate_watch_direction(test_df))

        bt = _compute_metrics([t for t in train_trades_list if t.get("direction") == "bullish"])
        wt = evaluate_watch_direction(train_df)
        bte = _compute_metrics([])
        we = {"bullish_total": 0, "bullish_correct": 0, "bullish_pct": 0.0}

        if not train_only:
            bte = _compute_metrics([t for t in test_trades_list if t.get("direction") == "bullish"])
            we = evaluate_watch_direction(test_df)

        per_ticker.append({
            "ticker": ticker,
            "rows": len(df),
            "bull_train_trades": bt["total_trades"],
            "bull_train_win_rate": bt["win_rate"],
            "bull_train_avg_return": bt["average_return"],
            "bull_test_trades": bte["total_trades"],
            "bull_test_win_rate": bte["win_rate"],
            "bull_test_avg_return": bte["average_return"],
            "bull_train_watch_total": wt["bullish_total"],
            "bull_train_watch_correct": wt["bullish_correct"],
            "bull_test_watch_total": we["bullish_total"],
            "bull_test_watch_correct": we["bullish_correct"],
        })

        print(
            f"  {ticker}: rows={len(df)} "
            f"bull_train_trades={bt['total_trades']} (wr={bt['win_rate']}% ret={bt['average_return']}%) "
            f"| bull_watch train={wt['bullish_total']}"
        )

    bull_train_trades = [t for t in train_trades if t.get("direction") == "bullish"]
    bull_test_trades = [t for t in test_trades if t.get("direction") == "bullish"]

    return {
        "bull_train_trade_metrics": _compute_metrics(bull_train_trades),
        "bull_test_trade_metrics": _compute_metrics(bull_test_trades),
        "bull_train_watch": _aggregate_watch_bullish(train_watch),
        "bull_test_watch": _aggregate_watch_bullish(test_watch),
        "per_ticker": per_ticker,
        "succeeded": succeeded,
        "failed": failed,
    }


if __name__ == "__main__":
    print(f"BULLISH walk-forward validation: {len(TICKERS)} tickers, {INTERVAL} / {PERIOD}")
    print(f"TRAIN-ONLY run (first {int(TRAIN_FRACTION * 100)}% of each ticker). "
          f"TEST split NOT computed or examined.")
    print()

    results = run(TICKERS, train_only=True)

    print()
    print("=== TICKER SUCCESS/FAILURE ===")
    print(f"  Succeeded: {len(results['succeeded'])} / {len(TICKERS)}")
    print(f"  Failed:    {len(results['failed'])} / {len(TICKERS)}")
    if results["failed"]:
        for t in results["failed"]:
            print(f"    - {t}")

    print()
    print("=== BULLISH WATCH DIRECTION CHECK (TRAIN only; price moves >1 ATR UP within 10 candles) ===")
    tr = results["bull_train_watch"]
    print(f"  TRAIN: total={tr['bullish_total']} correct={tr['bullish_correct']} bullish_pct={tr['bullish_pct']}%")

    print()
    print("=== BULLISH TRADE-BASED BACKTEST (TRAIN only; bullish-direction ENTRY trades only) ===")
    tm_tr = results["bull_train_trade_metrics"]
    for k in ("total_trades", "wins", "losses", "unresolved", "win_rate",
              "average_return", "average_win", "average_loss", "largest_win", "largest_loss"):
        print(f"  TRAIN {k}: {tm_tr[k]}")

    print()
    print("=== PER-TICKER BULLISH BREAKDOWN (TRAIN only) ===")
    for row in results["per_ticker"]:
        print(
            f"  {row['ticker']}: rows={row['rows']} "
            f"bull_train_trades={row['bull_train_trades']} (wr={row['bull_train_win_rate']}% ret={row['bull_train_avg_return']}%) "
            f"| bull_watch train={row['bull_train_watch_total']}"
        )

    total_watch = tr["bullish_total"]
    total_trades = tm_tr["total_trades"]
    print()
    print(f"Total BULLISH directional WATCH in TRAIN: {total_watch}")
    print(f"Total BULLISH ENTRY trades in TRAIN: {total_trades}")
    if total_trades < 30:
        print(
            "WARNING: total BULLISH trades is under 30. "
            "Trade-based metrics are NOT statistically reliable at this sample size."
        )
    if total_watch < 100:
        print(
            "WARNING: total BULLISH directional WATCH samples is under 100. "
            "Direction-check metrics are suggestive but not conclusive."
        )
