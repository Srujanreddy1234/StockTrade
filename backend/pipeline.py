"""Shared pipeline runner so both main.py and multi-timeframe code can
reuse the exact same logic without duplicating it or creating circular
imports.
"""

from __future__ import annotations

import pandas as pd

from backend.data_engine.loader import generate_synthetic, load_from_yfinance
from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.candles.pattern_library import add_extended_candle_metrics, detect_extended_patterns
from backend.indicators.indicator_engine import (
    add_indicators,
    find_swing_points,
    add_trend_read,
    add_support_resistance,
)
from backend.signals.scoring_engine import add_scores
from backend.risk.risk_engine import add_risk_levels
from backend.market_structure.structure_engine import add_structure_events
from backend.zones.zone_engine import add_reference_levels, add_zone_columns
from backend.breakouts.breakout_engine import add_breakout_events
from backend.pullback.pullback_engine import add_pullback_reversal_events


def run_pipeline(df: pd.DataFrame, timeframe: str = "unknown") -> pd.DataFrame:
    """Run the full existing pipeline on a standard OHLCV DataFrame.

    `timeframe` is an optional label (e.g. "1d", "15m") attached to the
    zone-engine output for downstream multi-timeframe consumers; it does
    not affect any calculation and defaults to "unknown" so existing
    callers that don't pass it keep working unchanged.
    """
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_extended_candle_metrics(df)
    df = detect_extended_patterns(df)
    df = add_reference_levels(df)
    df = add_indicators(df)
    df = find_swing_points(df, lookback=5)
    df = add_trend_read(df)
    df = add_support_resistance(df)
    df = add_structure_events(df)
    df = add_zone_columns(df, timeframe=timeframe)
    df = add_breakout_events(df, timeframe=timeframe)
    df = add_pullback_reversal_events(df)
    df = add_scores(df)
    df = add_risk_levels(df)
    return df


def load_and_run(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """Load data for a ticker/interval/period and run the full pipeline."""
    if interval == "synthetic":
        df = generate_synthetic(n_candles=600, seed=42)
    else:
        df = load_from_yfinance(ticker=ticker, period=period, interval=interval)
    return run_pipeline(df, timeframe=interval)
