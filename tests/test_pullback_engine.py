"""Tests for the pullback/reversal intelligence engine (Step 10)."""

from __future__ import annotations

import pandas as pd

from backend.pullback.pullback_engine import add_pullback_reversal_events, get_pullback_reversal_events


def _minimal_df(n: int, **cols) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    base = {
        "close": [100.0] * n,
        "trend": ["sideways"] * n,
        "rsi": [50.0] * n,
        "structure_event": [None] * n,
        "sr_interaction": [None] * n,
        "is_bullish": [True] * n,
        "extended_pattern_direction": [None] * n,
        "support_zone": [None] * n,
        "resistance_zone": [None] * n,
        "breakout_event": [None] * n,
    }
    base.update(cols)
    return pd.DataFrame(base, index=idx)


def test_pullback_continuation_bullish():
    df = _minimal_df(
        3,
        trend=["uptrend"] * 3,
        sr_interaction=[None, "SUPPORT_REJECTION", None],
        is_bullish=[True, True, True],
        support_zone=[None, {"lower": 98.0, "upper": 99.0}, None],
    )
    df = add_pullback_reversal_events(df)
    assert df["pullback_event"].iloc[1] == "PULLBACK_CONTINUATION_BULLISH"


def test_pullback_continuation_bearish():
    df = _minimal_df(
        3,
        trend=["downtrend"] * 3,
        sr_interaction=[None, "RESISTANCE_REJECTION", None],
        is_bullish=[False, False, False],
        resistance_zone=[None, {"lower": 101.0, "upper": 102.0}, None],
    )
    df = add_pullback_reversal_events(df)
    assert df["pullback_event"].iloc[1] == "PULLBACK_CONTINUATION_BEARISH"


def test_pullback_continuation_requires_matching_candle_color():
    # Support held (rejection), but the candle itself is bearish -- not a
    # confirmed continuation signal.
    df = _minimal_df(
        3,
        trend=["uptrend"] * 3,
        sr_interaction=[None, "SUPPORT_REJECTION", None],
        is_bullish=[True, False, True],
        support_zone=[None, {"lower": 98.0, "upper": 99.0}, None],
    )
    df = add_pullback_reversal_events(df)
    assert df["pullback_event"].iloc[1] is None


def test_pullback_exhaustion_bullish_on_oversold_approach():
    df = _minimal_df(
        3,
        trend=["uptrend"] * 3,
        sr_interaction=[None, "APPROACHING_SUPPORT", None],
        rsi=[50.0, 35.0, 50.0],
    )
    df = add_pullback_reversal_events(df)
    assert df["pullback_event"].iloc[1] == "PULLBACK_EXHAUSTION_BULLISH"


def test_pullback_exhaustion_not_flagged_when_rsi_not_stretched():
    df = _minimal_df(
        3,
        trend=["uptrend"] * 3,
        sr_interaction=[None, "APPROACHING_SUPPORT", None],
        rsi=[50.0, 55.0, 50.0],  # not oversold
    )
    df = add_pullback_reversal_events(df)
    assert df["pullback_event"].iloc[1] is None


def test_reversal_bearish_on_choch_with_full_corroboration():
    df = _minimal_df(
        5,
        structure_event=[None, None, None, "CHoCH_bearish", None],
        is_bullish=[True, True, True, False, True],
        extended_pattern_direction=[None, None, None, "bearish", None],
        rsi=[60.0, 58.0, 55.0, 50.0, 50.0],  # falling into the CHoCH row
        breakout_event=[None, None, None, "FALSE_BREAKOUT_BULLISH", None],
    )
    df = add_pullback_reversal_events(df)
    assert df["reversal_event"].iloc[3] == "POTENTIAL_REVERSAL_BEARISH"
    assert df["reversal_confidence"].iloc[3] == "high"


def test_reversal_bearish_with_no_corroboration_is_low_confidence():
    df = _minimal_df(
        5,
        structure_event=[None, None, None, "CHoCH_bearish", None],
        is_bullish=[True, True, True, True, True],  # candle still bullish, contradicts the CHoCH
        rsi=[50.0, 50.0, 50.0, 55.0, 50.0],  # rising, not falling
    )
    df = add_pullback_reversal_events(df)
    assert df["reversal_event"].iloc[3] == "POTENTIAL_REVERSAL_BEARISH"
    assert df["reversal_confidence"].iloc[3] == "low"


def test_reversal_bullish_on_choch():
    df = _minimal_df(
        5,
        structure_event=[None, None, None, "CHoCH_bullish", None],
        is_bullish=[False, False, False, True, False],
        extended_pattern_direction=[None, None, None, "bullish", None],
        rsi=[40.0, 42.0, 44.0, 50.0, 50.0],
    )
    df = add_pullback_reversal_events(df)
    assert df["reversal_event"].iloc[3] == "POTENTIAL_REVERSAL_BULLISH"
    assert df["reversal_confidence"].iloc[3] in ("medium", "high")


def test_get_pullback_reversal_events_shape():
    df = _minimal_df(
        3,
        trend=["uptrend"] * 3,
        sr_interaction=[None, "SUPPORT_REJECTION", None],
        is_bullish=[True, True, True],
        support_zone=[None, {"lower": 98.0, "upper": 99.0}, None],
        structure_event=[None, "CHoCH_bearish", None],
    )
    df = add_pullback_reversal_events(df)
    events = get_pullback_reversal_events(df, timeframe="1d")
    categories = {e["category"] for e in events}
    assert categories == {"pullback", "reversal"}
    reversal = next(e for e in events if e["category"] == "reversal")
    assert "confidence" in reversal
    assert "not a confirmed one" in reversal["note"]


def test_empty_df_does_not_crash():
    df = pd.DataFrame(columns=["close"])
    df.index = pd.DatetimeIndex([])
    result = add_pullback_reversal_events(df)
    assert "pullback_event" in result.columns
    assert "reversal_event" in result.columns
    assert len(result) == 0


def test_full_pipeline_integration():
    from backend.data_engine.loader import generate_synthetic
    from backend.pipeline import run_pipeline

    df = generate_synthetic(n_candles=300, seed=13)
    df = run_pipeline(df, timeframe="1d")
    for col in ("pullback_event", "reversal_event", "reversal_confidence"):
        assert col in df.columns
    # Existing columns from earlier steps must be untouched.
    for col in ("pattern", "trend", "support_zone", "breakout_event"):
        assert col in df.columns
    assert df["reversal_confidence"].dropna().isin(["low", "medium", "high"]).all()
