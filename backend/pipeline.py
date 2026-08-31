"""Shared pipeline runner so both main.py and multi-timeframe code can
reuse the exact same logic without duplicating it or creating circular
imports.
"""

from __future__ import annotations

import pandas as pd

from backend.data_engine.loader import generate_synthetic, load_from_yfinance
from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.indicators.indicator_engine import (
    add_indicators,
    find_swing_points,
    add_trend_read,
    add_support_resistance,
)
from backend.signals.scoring_engine import add_scores
from backend.risk.risk_engine import add_risk_levels


def run_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full existing pipeline on a standard OHLCV DataFrame."""
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_indicators(df)
    df = find_swing_points(df, lookback=5)
    df = add_trend_read(df)
    df = add_support_resistance(df)
    df = add_scores(df)
    df = add_risk_levels(df)
    return df


def load_and_run(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """Load data for a ticker/interval/period and run the full pipeline."""
    if interval == "synthetic":
        df = generate_synthetic(n_candles=600, seed=42)
    else:
        df = load_from_yfinance(ticker=ticker, period=period, interval=interval)
    return run_pipeline(df)
