"""Tests for the unified setup confluence engine (Step 15)."""

from __future__ import annotations

import pandas as pd

from backend.setup.setup_engine import add_setup_confluence, get_setup_events


def _minimal_df(n: int, **cols) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    base = {
        "close": [100.0] * n,
        "status": ["NO TRADE"] * n,
        "direction": [None] * n,
        "structure_state": [None] * n,
        "sr_interaction": [None] * n,
        "breakout_event": [None] * n,
        "pullback_event": [None] * n,
        "reversal_event": [None] * n,
        "reversal_confidence": [None] * n,
        "volume_confirms_event": [None] * n,
        "vwap_interaction": [None] * n,
    }
    base.update(cols)
    return pd.DataFrame(base, index=idx)


def test_no_direction_stays_no_trade():
    df = _minimal_df(3, status=["ENTRY"] * 3, direction=[None] * 3)
    df = add_setup_confluence(df)
    assert df["confluence_status"].iloc[1] == "NO TRADE"
    assert df["confluence_direction"].iloc[1] is None


def test_entry_confirmed_with_strong_corroboration():
    df = _minimal_df(
        1,
        status=["ENTRY"],
        direction=["bullish"],
        structure_state=["bullish"],
        sr_interaction=["SUPPORT_RECLAIM"],
        breakout_event=["CONFIRMED_BREAKOUT_BULLISH"],
    )
    df = add_setup_confluence(df)
    assert df["confluence_status"].iloc[0] == "ENTRY"
    assert df["confluence_direction"].iloc[0] == "bullish"
    assert df["confluence_score"].iloc[0] > 0
    assert len(df["confluence_reasons"].iloc[0]) >= 2


def test_entry_without_corroboration_downgraded_to_watch():
    df = _minimal_df(1, status=["ENTRY"], direction=["bullish"])
    df = add_setup_confluence(df)
    assert df["confluence_status"].iloc[0] == "WATCH"


def test_entry_with_strong_contradiction_downgraded_to_no_trade():
    df = _minimal_df(
        1,
        status=["ENTRY"],
        direction=["bullish"],
        structure_state=["bearish"],
        sr_interaction=["SUPPORT_BREAK"],
    )
    df = add_setup_confluence(df)
    assert df["confluence_status"].iloc[0] == "NO TRADE"
    assert df["confluence_direction"].iloc[0] is None


def test_watch_with_contradiction_becomes_no_trade():
    df = _minimal_df(
        1,
        status=["WATCH"],
        direction=["bearish"],
        structure_state=["bullish"],
    )
    df = add_setup_confluence(df)
    assert df["confluence_status"].iloc[0] == "NO TRADE"


def test_watch_without_contradiction_stays_watch():
    df = _minimal_df(1, status=["WATCH"], direction=["bullish"])
    df = add_setup_confluence(df)
    assert df["confluence_status"].iloc[0] == "WATCH"
    assert df["confluence_direction"].iloc[0] == "bullish"


def test_base_no_trade_stays_no_trade_even_with_corroboration():
    df = _minimal_df(
        1,
        status=["NO TRADE"],
        direction=["bullish"],
        structure_state=["bullish"],
    )
    df = add_setup_confluence(df)
    assert df["confluence_status"].iloc[0] == "NO TRADE"


def test_stale_breakout_outside_lookback_window_does_not_count():
    n = 10
    breakout = [None] * n
    breakout[0] = "CONFIRMED_BREAKOUT_BULLISH"
    df = _minimal_df(
        n,
        status=["NO TRADE"] * (n - 1) + ["ENTRY"],
        direction=[None] * (n - 1) + ["bullish"],
        breakout_event=breakout,
    )
    df = add_setup_confluence(df, lookback=3)
    # Breakout at row 0 is far outside a 3-row lookback from the last row.
    assert "a confirmed breakout/retest supports this direction" not in df["confluence_reasons"].iloc[-1]


def test_get_setup_events_shape():
    df = _minimal_df(
        3,
        status=["NO TRADE", "ENTRY", "ENTRY"],
        direction=[None, "bullish", "bullish"],
        structure_state=[None, "bullish", "bullish"],
        sr_interaction=[None, "SUPPORT_RECLAIM", None],
        breakout_event=[None, "CONFIRMED_BREAKOUT_BULLISH", None],
    )
    df = add_setup_confluence(df)
    events = get_setup_events(df, timeframe="1d")
    assert events
    assert events[0]["category"] == "setup"
    assert "note" in events[0] and "reasons" in events[0]


def test_full_pipeline_integration():
    from backend.data_engine.loader import generate_synthetic
    from backend.pipeline import run_pipeline

    df = generate_synthetic(n_candles=300, seed=21)
    df = run_pipeline(df, timeframe="1d")
    for col in ("confluence_score", "confluence_status", "confluence_direction", "confluence_reasons"):
        assert col in df.columns
    for col in ("status", "direction", "score"):
        assert col in df.columns
    assert df["confluence_status"].isin(["ENTRY", "WATCH", "NO TRADE"]).all()
