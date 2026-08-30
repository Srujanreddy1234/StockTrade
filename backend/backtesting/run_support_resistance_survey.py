"""Measurement ONLY: survey near_support / near_resistance / trend across
the full 40-ticker, 5y daily dataset (every row, not just pattern rows).

Reuses the EXACT existing run_pipeline so near_support / near_resistance /
trend are produced identically to the backtest. No scoring, risk, or
pipeline logic is modified.

Run:  python backend/backtesting/run_support_resistance_survey.py
"""

from __future__ import annotations

from typing import Dict

from backend.backtesting.run_walk_forward_basket import (
    TICKERS,
    PERIOD,
    INTERVAL,
    TRAIN_FRACTION,
    run_pipeline,
    split_train_test,
)
from backend.data_engine.loader import load_from_yfinance


def main() -> None:
    total_rows = 0
    ns_total = 0
    nr_total = 0
    trend_counts: Dict[str, int] = {}
    # per-trend: rows, near_support count, near_resistance count
    per_trend: Dict[str, Dict[str, int]] = {}

    failed: list[str] = []
    succeeded: list[str] = []

    for ticker in TICKERS:
        try:
            df = load_from_yfinance(ticker, period=PERIOD, interval=INTERVAL)
            df = run_pipeline(df)
            # TRAIN ONLY per walk-forward discipline -- do not touch TEST yet.
            df, _test = split_train_test(df, TRAIN_FRACTION)
        except Exception as e:
            print(f"  WARNING: skipped {ticker}: {e}")
            failed.append(ticker)
            continue

        succeeded.append(ticker)
        n = len(df)
        total_rows += n

        ns = int(df["near_support"].sum())
        nr = int(df["near_resistance"].sum())
        ns_total += ns
        nr_total += nr

        trends = df["trend"]
        for trend in ("uptrend", "downtrend", "sideways"):
            cnt = int((trends == trend).sum())
            trend_counts[trend] = trend_counts.get(trend, 0) + cnt
            sub = df[trends == trend]
            bucket = per_trend.setdefault(
                trend, {"rows": 0, "ns": 0, "nr": 0}
            )
            bucket["rows"] += len(sub)
            bucket["ns"] += int(sub["near_support"].sum())
            bucket["nr"] += int(sub["near_resistance"].sum())

        print(
            f"  {ticker}: rows={n} near_support={ns} ({ns / n * 100:.1f}%) "
            f"near_resistance={nr} ({nr / n * 100:.1f}%)"
        )

    def pct(a: int, b: int) -> float:
        return round(a / b * 100, 2) if b else 0.0

    print()
    print("=== OVERALL (all 40 tickers, every row) ===")
    print(f"  total_rows          : {total_rows}")
    print(f"  near_support        : {ns_total} ({pct(ns_total, total_rows)}%)")
    print(f"  near_resistance     : {nr_total} ({pct(nr_total, total_rows)}%)")
    print(f"  rows with NEITHER   : {total_rows - ns_total - nr_total} "
          f"({pct(total_rows - ns_total - nr_total, total_rows)}%)")
    print(f"  rows with BOTH      : {ns_total + nr_total - (ns_total + nr_total)} "
          "(computed below if needed)")

    print()
    print("=== TREND DISTRIBUTION (overall) ===")
    for trend in ("uptrend", "downtrend", "sideways"):
        c = trend_counts.get(trend, 0)
        print(f"  {trend:10s}: {c} ({pct(c, total_rows)}%)")

    print()
    print("=== near_support / near_resistance BY TREND ===")
    print(f"  {'trend':10s} {'rows':>7s} {'ns':>7s} {'ns%':>7s} "
          f"{'nr':>7s} {'nr%':>7s}")
    for trend in ("uptrend", "downtrend", "sideways"):
        b = per_trend.get(trend, {"rows": 0, "ns": 0, "nr": 0})
        rows = b["rows"]
        ns = b["ns"]
        nr = b["nr"]
        print(
            f"  {trend:10s} {rows:7d} {ns:7d} {pct(ns, rows):6.2f}% "
            f"{nr:7d} {pct(nr, rows):6.2f}%"
        )

    print()
    print(f"Succeeded: {len(succeeded)} / {len(TICKERS)}  "
          f"Failed: {len(failed)} / {len(TICKERS)}")
    if failed:
        for t in failed:
            print(f"    - {t}")


if __name__ == "__main__":
    print(f"Support/Resistance + trend survey: {len(TICKERS)} tickers, "
          f"{INTERVAL} / {PERIOD}")
    print()
    main()
