"""Tests for the breakout/breakdown/retest intelligence engine (Step 9)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.indicators.indicator_engine import (
    add_indicators,
    find_swing_points,
    add_trend_read,
    add_support_resistance,
)
from backend.zones.zone_engine import add_reference_levels, add_zone_columns
from backend.breakouts.breakout_engine import add_breakout_events, get_breakout_events


def _make_ohlc(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    closes = np.array(closes, dtype=float)
    highs = closes + 0.5
    lows = closes - 0.5
    volume = np.array(volumes, dtype=float) if volumes is not None else np.full(len(closes), 1000.0)
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx
    )


def _run(df: pd.DataFrame) -> pd.DataFrame:
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_reference_levels(df)
    df = add_indicators(df)
    df = find_swing_points(df, lookback=5)
    df = add_trend_read(df)
    df = add_support_resistance(df)
    df = add_zone_columns(df, timeframe="1d")
    df = add_breakout_events(df, timeframe="1d")
    return df


def test_confirmed_breakout_requires_volume_and_distance():
    # Range-bound under ~108, then a big, high-volume breakout candle well
    # beyond the zone with price above EMA.
    closes = [100, 105, 108, 105, 108, 105, 108, 105, 108] + [120]
    volumes = [1000] * 9 + [3000]  # volume spike on the breakout candle
    df = _make_ohlc(closes, volumes)
    df = _run(df)
    events = [e for e in df["breakout_event"].tolist() if e]
    assert "CONFIRMED_BREAKOUT_BULLISH" in events


def test_weak_breakout_without_volume_stays_possible():
    # Same price structure, but NO volume expansion on the breakout candle.
    closes = [100, 105, 108, 105, 108, 105, 108, 105, 108] + [109]  # barely beyond, tiny move
    volumes = [1000] * 10  # flat volume, no spike
    df = _make_ohlc(closes, volumes)
    df = _run(df)
    events = [e for e in df["breakout_event"].tolist() if e]
    assert "CONFIRMED_BREAKOUT_BULLISH" not in events


def test_false_breakout_after_reversal():
    # Breaks out, but with no confirmation, and then reverses back below
    # the broken zone within the grace window.
    closes = [100, 105, 108, 105, 108, 105, 108, 105, 108] + [109, 107, 104, 100]
    volumes = [1000] * 13  # never any volume confirmation
    df = _make_ohlc(closes, volumes)
    df = _run(df)
    events = [e for e in df["breakout_event"].tolist() if e]
    assert "FALSE_BREAKOUT_BULLISH" in events
    assert "CONFIRMED_BREAKOUT_BULLISH" not in events


def test_confirmed_breakout_then_successful_retest():
    # Isolates the retest state machine directly: a full-pipeline oscillating
    # series near a zone's own midpoint flips that zone's support/resistance
    # classification every candle (a documented limitation -- see the Step 9
    # report), which starves this scenario of a stable prev_support/
    # prev_resistance to break in the first place. Feeding add_breakout_events
    # its required inputs directly tests what this module actually owns: the
    # confirm-then-retest lifecycle, independent of that upstream quirk.
    n = 8
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    resistance_zone_dict = {"lower": 107.0, "upper": 108.0, "mid": 107.5, "sources": ["swing_high"]}
    df = pd.DataFrame(
        {
            # Row 5 is the retest candle: low dips into the broken zone
            # (107.0-108.0) but closes back above it in the same candle --
            # a classic retest-and-hold rejection.
            "close": [108.0, 120.0, 118.0, 116.0, 109.0, 108.5, 115.0, 118.0],
            "low": [107.5, 119.5, 117.5, 115.5, 108.5, 107.2, 114.5, 117.5],
            "high": [108.5, 120.5, 118.5, 116.5, 109.5, 108.6, 115.5, 118.5],
            "volume": [1000, 5000, 1200, 1200, 1200, 1200, 1200, 1200],
            "atr": [1.0] * n,
            "ema": [105.0] * n,  # comfortably below every close -> momentum always "ok" here
            "sr_interaction": [None, "RESISTANCE_BREAK", None, None, None, None, None, None],
            "support_zone": [None] * n,
            "resistance_zone": [resistance_zone_dict if i == 1 else None for i in range(n)],
        },
        index=idx,
    )
    df = add_breakout_events(df, timeframe="1d")
    events = [e for e in df["breakout_event"].tolist() if e]
    assert "CONFIRMED_BREAKOUT_BULLISH" in events
    assert "BREAKOUT_RETEST_SUCCESS_BULLISH" in events
    # The retest event must carry the ORIGINAL broken zone's bounds, not
    # whatever (unrelated or absent) zone sits on the retest row itself.
    retest_row = df[df["breakout_event"] == "BREAKOUT_RETEST_SUCCESS_BULLISH"].iloc[0]
    assert retest_row["breakout_zone_lower"] == 107.0
    assert retest_row["breakout_zone_upper"] == 108.0


def test_confirmed_breakdown_bearish():
    closes = [120, 115, 112, 115, 112, 115, 112, 115, 112] + [95]
    volumes = [1000] * 9 + [3000]
    df = _make_ohlc(closes, volumes)
    df = _run(df)
    events = [e for e in df["breakout_event"].tolist() if e]
    assert "CONFIRMED_BREAKOUT_BEARISH" in events


def test_get_breakout_events_shape_and_notes():
    closes = [100, 105, 108, 105, 108, 105, 108, 105, 108] + [120]
    volumes = [1000] * 9 + [3000]
    df = _make_ohlc(closes, volumes)
    df = _run(df)
    events = get_breakout_events(df, timeframe="1d")
    assert len(events) > 0
    confirmed = [e for e in events if e["event"] == "CONFIRMED_BREAKOUT_BULLISH"]
    assert len(confirmed) == 1
    e = confirmed[0]
    assert e["direction"] == "bullish"
    assert e["level"] is not None
    assert "Confirmed bullish breakout" in e["note"]


def test_full_pipeline_integration_preserves_existing_columns():
    from backend.data_engine.loader import generate_synthetic
    from backend.pipeline import run_pipeline

    df = generate_synthetic(n_candles=300, seed=11)
    df = run_pipeline(df, timeframe="1d")
    for col in ("pattern", "trend", "support", "resistance", "structure_event", "support_zone", "sr_interaction"):
        assert col in df.columns
    assert "breakout_event" in df.columns
    assert "breakout_direction" in df.columns


def test_empty_df_does_not_crash():
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex([])
    result = add_breakout_events(df, timeframe="1d")
    assert "breakout_event" in result.columns
    assert len(result) == 0
