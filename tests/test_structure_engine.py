"""Tests for the market structure (BOS/CHoCH) engine."""

from __future__ import annotations

import pandas as pd

from backend.market_structure.structure_engine import add_structure_events, get_structure_events


def _minimal_df(closes: list[float], trends: list[str], supports: list[float], resistances: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "close": closes,
            "trend": trends,
            "support": supports,
            "resistance": resistances,
        },
        index=idx,
    )


def test_bos_bullish_on_uptrend_resistance_break():
    # Uptrend, price sits below resistance (100) then closes above it.
    df = _minimal_df(
        closes=[95, 98, 101],
        trends=["uptrend", "uptrend", "uptrend"],
        supports=[90, 90, 90],
        resistances=[100, 100, 100],
    )
    df = add_structure_events(df)
    assert df["structure_event"].iloc[-1] == "BOS_bullish"
    assert df["structure_state"].iloc[-1] == "bullish"


def test_choch_bearish_on_uptrend_support_break():
    # Uptrend, price sits above support (90) then closes below it -- structure breaks.
    df = _minimal_df(
        closes=[95, 92, 88],
        trends=["uptrend", "uptrend", "uptrend"],
        supports=[90, 90, 90],
        resistances=[110, 110, 110],
    )
    df = add_structure_events(df)
    assert df["structure_event"].iloc[-1] == "CHoCH_bearish"
    assert df["structure_state"].iloc[-1] == "bearish"


def test_bos_bearish_on_downtrend_support_break():
    df = _minimal_df(
        closes=[95, 92, 88],
        trends=["downtrend", "downtrend", "downtrend"],
        supports=[90, 90, 90],
        resistances=[110, 110, 110],
    )
    df = add_structure_events(df)
    assert df["structure_event"].iloc[-1] == "BOS_bearish"
    assert df["structure_state"].iloc[-1] == "bearish"


def test_choch_bullish_on_downtrend_resistance_break():
    df = _minimal_df(
        closes=[95, 98, 101],
        trends=["downtrend", "downtrend", "downtrend"],
        supports=[80, 80, 80],
        resistances=[100, 100, 100],
    )
    df = add_structure_events(df)
    assert df["structure_event"].iloc[-1] == "CHoCH_bullish"
    assert df["structure_state"].iloc[-1] == "bullish"


def test_range_breakout_when_sideways():
    df = _minimal_df(
        closes=[95, 98, 101],
        trends=["sideways", "sideways", "sideways"],
        supports=[90, 90, 90],
        resistances=[100, 100, 100],
    )
    df = add_structure_events(df)
    assert df["structure_event"].iloc[-1] == "range_breakout_bullish"


def test_no_event_when_no_level_crossed():
    df = _minimal_df(
        closes=[95, 96, 97],
        trends=["uptrend", "uptrend", "uptrend"],
        supports=[90, 90, 90],
        resistances=[100, 100, 100],
    )
    df = add_structure_events(df)
    assert df["structure_event"].isna().all()
    assert (df["structure_state"] == "neutral").all()


def test_state_persists_until_next_event():
    # BOS_bullish on row 2, then a quiet row 3 that shouldn't reset state.
    df = _minimal_df(
        closes=[95, 101, 102],
        trends=["uptrend", "uptrend", "uptrend"],
        supports=[90, 90, 90],
        resistances=[100, 100, 105],
    )
    df = add_structure_events(df)
    assert df["structure_event"].iloc[1] == "BOS_bullish"
    assert df["structure_event"].iloc[2] is None
    assert df["structure_state"].iloc[2] == "bullish"


def test_get_structure_events_produces_readable_notes():
    df = _minimal_df(
        closes=[95, 98, 101],
        trends=["uptrend", "uptrend", "uptrend"],
        supports=[90, 90, 90],
        resistances=[100, 100, 100],
    )
    df = add_structure_events(df)
    events = get_structure_events(df, timeframe="1d")
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "BOS_bullish"
    assert e["direction"] == "bullish"
    assert e["level"] == 100.0
    assert "continuing the uptrend" in e["note"]


def test_full_pipeline_produces_structure_columns():
    from backend.data_engine.loader import generate_synthetic
    from backend.pipeline import run_pipeline

    df = generate_synthetic(n_candles=600, seed=42)
    df = run_pipeline(df)
    assert "structure_event" in df.columns
    assert "structure_state" in df.columns
    assert set(df["structure_state"].unique()) <= {"neutral", "bullish", "bearish"}
    # The synthetic generator's long final uptrend leg should produce at
    # least one bullish structure event somewhere in the series.
    assert (df["structure_event"] == "BOS_bullish").any() or (df["structure_event"] == "range_breakout_bullish").any()
