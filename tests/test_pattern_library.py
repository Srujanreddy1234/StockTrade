"""Tests for the extended candlestick pattern library."""

from __future__ import annotations

import pandas as pd

from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.candles.pattern_library import (
    add_extended_candle_metrics,
    detect_extended_patterns,
    get_pattern_events,
)


def _df(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    base = {"volume": 1000.0}
    data = [{**base, **r} for r in rows]
    return pd.DataFrame(data, index=idx)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_extended_candle_metrics(df)
    df = detect_extended_patterns(df)
    return df


def test_marubozu_detected():
    df = _prepare(_df([
        {"open": 100, "high": 101, "low": 99, "close": 100.5},
        {"open": 100, "high": 110, "low": 100, "close": 110},  # full-body bull candle
    ]))
    assert bool(df["is_marubozu"].iloc[-1])


def test_dragonfly_doji_detected():
    df = _prepare(_df([
        {"open": 100, "high": 101, "low": 99, "close": 100.5},
        {"open": 100, "high": 100.2, "low": 95, "close": 100.1},  # open~close~high, long lower wick
    ]))
    assert bool(df["is_dragonfly_doji"].iloc[-1])
    assert not bool(df["is_gravestone_doji"].iloc[-1])


def test_gravestone_doji_detected():
    df = _prepare(_df([
        {"open": 100, "high": 101, "low": 99, "close": 100.5},
        {"open": 100, "high": 105, "low": 99.9, "close": 100.1},  # open~close~low, long upper wick
    ]))
    assert bool(df["is_gravestone_doji"].iloc[-1])
    assert not bool(df["is_dragonfly_doji"].iloc[-1])


def test_bullish_harami_detected():
    df = _prepare(_df([
        {"open": 110, "high": 111, "low": 95, "close": 96},   # big bearish candle
        {"open": 100, "high": 103, "low": 99, "close": 102},  # small bullish candle inside it
    ]))
    assert bool(df["is_bullish_harami"].iloc[-1])


def test_bearish_harami_detected():
    df = _prepare(_df([
        {"open": 90, "high": 110, "low": 89, "close": 108},   # big bullish candle
        {"open": 100, "high": 102, "low": 98, "close": 99},   # small bearish candle inside it
    ]))
    assert bool(df["is_bearish_harami"].iloc[-1])


def test_piercing_pattern_detected():
    df = _prepare(_df([
        {"open": 110, "high": 111, "low": 99, "close": 100},   # bearish candle, body 100-110
        {"open": 98, "high": 108, "low": 97, "close": 107},    # gaps below prior close, closes above midpoint (105) but below prior open (110)
    ]))
    assert bool(df["is_piercing"].iloc[-1])


def test_dark_cloud_cover_detected():
    df = _prepare(_df([
        {"open": 100, "high": 111, "low": 99, "close": 110},   # bullish candle, body 100-110
        {"open": 112, "high": 113, "low": 102, "close": 103},  # gaps above prior close, closes below midpoint (105) but above prior open (100)
    ]))
    assert bool(df["is_dark_cloud_cover"].iloc[-1])


def test_tweezer_top_and_bottom():
    top = _prepare(_df([
        {"open": 100, "high": 120, "low": 99, "close": 118},   # bullish, high=120
        {"open": 118, "high": 120.05, "low": 110, "close": 111},  # bearish, matching high
    ]))
    assert bool(top["is_tweezer_top"].iloc[-1])

    bottom = _prepare(_df([
        {"open": 120, "high": 121, "low": 100, "close": 101},  # bearish, low=100
        {"open": 101, "high": 110, "low": 99.95, "close": 109},  # bullish, matching low
    ]))
    assert bool(bottom["is_tweezer_bottom"].iloc[-1])


def test_three_white_soldiers_and_black_crows():
    soldiers = _prepare(_df([
        {"open": 100, "high": 105, "low": 99.5, "close": 104.5},
        {"open": 104, "high": 109, "low": 103.5, "close": 108.5},
        {"open": 108, "high": 113, "low": 107.5, "close": 112.5},
    ]))
    assert bool(soldiers["is_three_white_soldiers"].iloc[-1])

    crows = _prepare(_df([
        {"open": 112, "high": 112.5, "low": 108, "close": 108.5},
        {"open": 108, "high": 108.5, "low": 104, "close": 104.5},
        {"open": 104, "high": 104.5, "low": 100, "close": 100.5},
    ]))
    assert bool(crows["is_three_black_crows"].iloc[-1])


def test_three_inside_up():
    df = _prepare(_df([
        {"open": 110, "high": 111, "low": 95, "close": 96},   # big bearish
        {"open": 100, "high": 103, "low": 99, "close": 102},  # bullish harami inside it
        {"open": 102, "high": 115, "low": 101, "close": 113},  # closes above first candle's open (110)
    ]))
    assert bool(df["is_three_inside_up"].iloc[-1])


def test_three_inside_down():
    df = _prepare(_df([
        {"open": 90, "high": 110, "low": 89, "close": 108},   # big bullish
        {"open": 100, "high": 102, "low": 98, "close": 99},   # bearish harami inside it
        {"open": 99, "high": 99.5, "low": 85, "close": 87},   # closes below first candle's open (90)
    ]))
    assert bool(df["is_three_inside_down"].iloc[-1])


def test_priority_pattern_columns_populated_and_backward_compatible():
    df = _prepare(_df([
        {"open": 100, "high": 101, "low": 99, "close": 100.5},
        {"open": 100, "high": 110, "low": 100, "close": 110},
    ]))
    # candle_engine's original columns are untouched.
    assert "pattern" in df.columns
    assert "pattern_direction" not in df.columns  # that column lives in scoring_engine's output, not candle_engine
    # the new additive columns exist alongside it.
    assert "extended_pattern" in df.columns
    assert "extended_pattern_direction" in df.columns
    assert "patterns_detected" in df.columns
    assert df["extended_pattern"].iloc[-1] == "marubozu"


def test_get_pattern_events_structure_and_confirmation():
    df = _prepare(_df([
        {"open": 110, "high": 111, "low": 95, "close": 96},
        {"open": 100, "high": 103, "low": 99, "close": 102},   # bullish harami on row 1
        {"open": 102, "high": 106, "low": 101, "close": 105},  # confirms (close > row1 close)
    ]))
    events = get_pattern_events(df, timeframe="1d")
    assert len(events) > 0
    harami_events = [e for e in events if e["name"] == "bullish_harami"]
    assert len(harami_events) == 1
    event = harami_events[0]
    assert event["direction"] == "bullish"
    assert event["confirmation"] == "confirmed"
    assert "Invalidated if price closes back below" in event["invalidation_note"]
    assert event["context"] == "unscored"  # no trend/S-R columns in this minimal df
    assert 0 <= event["quality"] <= 100


def test_get_pattern_events_last_row_is_pending():
    df = _prepare(_df([
        {"open": 100, "high": 101, "low": 99, "close": 100.5},
        {"open": 100, "high": 110, "low": 100, "close": 110},  # marubozu, last row -> no next candle yet
    ]))
    events = get_pattern_events(df, timeframe="1d")
    last_row_events = [e for e in events if e["timestamp"] == str(df.index[-1])]
    assert all(e["confirmation"] == "pending" for e in last_row_events)
