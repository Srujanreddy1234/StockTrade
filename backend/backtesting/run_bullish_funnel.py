"""Measurement ONLY: trace the bullish signal funnel across TRAIN (first 80%
of each ticker, 5y daily). No scoring/risk/pipeline logic is modified.

Answers:
  1. How many rows fire ANY bullish pattern vs ANY bearish pattern.
  2. Trend distribution AT bullish-pattern rows vs bearish-pattern rows.
  3. Score distribution (buckets) at bullish-pattern rows vs bearish-pattern rows.
  (Bonus: status distribution at pattern rows, to locate the funnel cut.)

Run:  python backend/backtesting/run_bullish_funnel.py
"""

from __future__ import annotations

from collections import Counter

from backend.backtesting.run_walk_forward_basket import (
    TICKERS,
    PERIOD,
    INTERVAL,
    TRAIN_FRACTION,
    run_pipeline,
    split_train_test,
)
from backend.data_engine.loader import load_from_yfinance

BULL_PATTERNS = ("hammer", "bullish_engulfing", "morning_star")
BEAR_PATTERNS = ("shooting_star", "bearish_engulfing", "evening_star")


def _bucket(score: float) -> str:
    if score < 20:
        return "0-19"
    if score < 40:
        return "20-39"
    if score < 60:
        return "40-59"
    if score < 80:
        return "60-79"
    return "80-100"


def _trend_dist(patterns_df) -> dict:
    c = Counter(patterns_df["trend"].fillna("unknown"))
    n = sum(c.values()) or 1
    return {t: (c.get(t, 0), round(c.get(t, 0) / n * 100, 2)) for t in ("uptrend", "downtrend", "sideways")}


def _score_dist(patterns_df) -> dict:
    c = Counter(_bucket(float(s)) for s in patterns_df["score"])
    return {b: c.get(b, 0) for b in ("0-19", "20-39", "40-59", "60-79", "80-100")}


def _status_dist(patterns_df) -> dict:
    c = Counter(patterns_df["status"].fillna("unknown"))
    return {s: c.get(s, 0) for s in ("ENTRY", "WATCH", "NO TRADE")}


def main() -> None:
    total_rows = 0
    bull_rows = 0
    bear_rows = 0
    bull_trend = Counter()
    bear_trend = Counter()
    bull_score = Counter()
    bear_score = Counter()
    bull_status = Counter()
    bear_status = Counter()
    bull_down = 0
    bear_down = 0
    failed: list[str] = []
    succeeded: list[str] = []

    for ticker in TICKERS:
        try:
            df = load_from_yfinance(ticker, period=PERIOD, interval=INTERVAL)
            df = run_pipeline(df)
            df, _test = split_train_test(df, TRAIN_FRACTION)
        except Exception as e:
            print(f"  WARNING: skipped {ticker}: {e}")
            failed.append(ticker)
            continue

        succeeded.append(ticker)
        total_rows += len(df)

        bull = df[df["pattern"].isin(BULL_PATTERNS)]
        bear = df[df["pattern"].isin(BEAR_PATTERNS)]
        bull_rows += len(bull)
        bear_rows += len(bear)

        bull_trend.update(bull["trend"].fillna("unknown"))
        bear_trend.update(bear["trend"].fillna("unknown"))
        bull_score.update(_bucket(float(s)) for s in bull["score"])
        bear_score.update(_bucket(float(s)) for s in bear["score"])
        bull_status.update(bull["status"].fillna("unknown"))
        bear_status.update(bear["status"].fillna("unknown"))
        bull_down += int(bull["status_reason"].notna().sum())
        bear_down += int(bear["status_reason"].notna().sum())

        print(
            f"  {ticker}: rows={len(df)} bull_pat={len(bull)} bear_pat={len(bear)} "
            f"| bull_status={dict(_status_dist(bull))} bear_status={dict(_status_dist(bear))}"
        )

    def pct(a, b):
        return round(a / b * 100, 2) if b else 0.0

    print()
    print("=== 1. PATTERN ROW COUNTS (TRAIN) ===")
    print(f"  total_train_rows : {total_rows}")
    print(f"  bullish patterns : {bull_rows} ({pct(bull_rows, total_rows)}% of rows)")
    print(f"  bearish patterns : {bear_rows} ({pct(bear_rows, total_rows)}% of rows)")
    print(f"  bull:bear ratio  : {round(bull_rows / bear_rows, 3) if bear_rows else 'n/a'}")

    print()
    print("=== 2. TREND AT PATTERN ROWS (TRAIN) ===")
    for label, tcounter in (("BULLISH", bull_trend), ("BEARISH", bear_trend)):
        n = sum(tcounter.values()) or 1
        print(
            f"  {label:8s}: uptrend={tcounter.get('uptrend',0)} ({pct(tcounter.get('uptrend',0),n)}%) "
            f"downtrend={tcounter.get('downtrend',0)} ({pct(tcounter.get('downtrend',0),n)}%) "
            f"sideways={tcounter.get('sideways',0)} ({pct(tcounter.get('sideways',0),n)}%)"
        )

    print()
    print("=== 3. SCORE DISTRIBUTION AT PATTERN ROWS (TRAIN) ===")
    print(f"  {'bucket':8s} {'BULLISH':>8s} {'BEARISH':>8s}")
    for b in ("0-19", "20-39", "40-59", "60-79", "80-100"):
        print(f"  {b:8s} {bull_score.get(b,0):8d} {bear_score.get(b,0):8d}")

    print()
    print("=== BONUS: STATUS AT PATTERN ROWS (TRAIN) ===")
    for label, scounter in (("BULLISH", bull_status), ("BEARISH", bear_status)):
        n = sum(scounter.values()) or 1
        print(
            f"  {label:8s}: ENTRY={scounter.get('ENTRY',0)} ({pct(scounter.get('ENTRY',0),n)}%) "
            f"WATCH={scounter.get('WATCH',0)} ({pct(scounter.get('WATCH',0),n)}%) "
            f"NO_TRADE={scounter.get('NO TRADE',0)} ({pct(scounter.get('NO TRADE',0),n)}%)"
        )

    print()
    print("=== FUNNEL OUTCOME (TRAIN) ===")
    bull_surv = bull_status.get("ENTRY", 0) + bull_status.get("WATCH", 0)
    bear_surv = bear_status.get("ENTRY", 0) + bear_status.get("WATCH", 0)
    print(f"  BULLISH: pattern_rows={bull_rows}  downgraded_R:R={bull_down}  survived_WATCH/ENTRY={bull_surv}")
    print(f"  BEARISH: pattern_rows={bear_rows}  downgraded_R:R={bear_down}  survived_WATCH/ENTRY={bear_surv}")

    print()
    print(f"Succeeded: {len(succeeded)} / {len(TICKERS)}  Failed: {len(failed)} / {len(TICKERS)}")
    if failed:
        for t in failed:
            print(f"    - {t}")


if __name__ == "__main__":
    print(f"Bullish funnel trace (TRAIN only): {len(TICKERS)} tickers, {INTERVAL} / {PERIOD}")
    print()
    main()
