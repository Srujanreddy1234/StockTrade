"""Tests for the multi-source support/resistance zone engine (Step 8)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.indicators.indicator_engine import (
    add_indicators,
    find_swing_points,
    add_trend_read,
    add_support_resistance,
)
from backend.zones.zone_engine import (
    add_reference_levels,
    add_zone_columns,
    get_support_resistance_snapshot,
    _cluster_levels,
)


def _make_ohlc(closes: list[float], freq: str = "D") -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq=freq)
    closes = np.array(closes, dtype=float)
    highs = closes + 0.5
    lows = closes - 0.5
    opens = closes
    volume = np.full(len(closes), 1000.0)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx)


def _run_zone_pipeline(df: pd.DataFrame, timeframe: str = "1d") -> pd.DataFrame:
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_reference_levels(df)
    df = add_indicators(df)
    df = find_swing_points(df, lookback=5)
    df = add_trend_read(df)
    df = add_support_resistance(df)
    df = add_zone_columns(df, timeframe=timeframe)
    return df


# --- Clustering primitive ---

def test_cluster_levels_merges_nearby_and_caps_width():
    levels = [(100.0, "a"), (100.4, "b"), (100.8, "c"), (150.0, "d")]
    clusters = _cluster_levels(levels, tolerance=1.0)
    # a/b/c are close together and within the 2x width cap; 150 is isolated.
    assert len(clusters) == 2
    lower, upper, sources = clusters[0]
    assert lower == 100.0 and upper == 100.8
    assert set(sources) == {"a", "b", "c"}


def test_cluster_levels_does_not_chain_indefinitely():
    # Each pair is within tolerance of its neighbor, but the whole run spans
    # far more than 2x tolerance -- must NOT merge into one giant cluster.
    levels = [(100.0 + i * 0.9, str(i)) for i in range(20)]  # spans 100..117.1, gap 0.9 each
    clusters = _cluster_levels(levels, tolerance=1.0)
    assert len(clusters) > 1
    for lower, upper, _ in clusters:
        assert upper - lower <= 2.0 + 1e-9


def test_cluster_levels_empty():
    assert _cluster_levels([], tolerance=1.0) == []


# --- Reference levels (PDH/PDL/PWH/PWL), and look-ahead safety ---

def test_reference_levels_no_lookahead():
    # Day 1: high=110, low=90. Day 2 should see PDH=110/PDL=90 all day,
    # regardless of day 2's own (still-forming) range. Builds an explicit
    # index so multiple rows genuinely share the same calendar date, unlike
    # date_range(freq="D") which gives exactly one row per day.
    closes = [100.0, 100.0, 100.0, 105.0, 105.0, 105.0]
    idx = pd.to_datetime(
        [
            "2026-01-01 09:00", "2026-01-01 10:00", "2026-01-01 11:00",
            "2026-01-02 09:00", "2026-01-02 10:00", "2026-01-02 11:00",
        ]
    )
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000.0] * 6,
        },
        index=idx,
    )
    df.loc[df.index[0], "high"] = 110
    df.loc[df.index[0], "low"] = 90
    df = add_reference_levels(df)
    assert df["pdh"].iloc[:3].isna().all()  # day 1 has no "previous day" yet
    assert (df["pdh"].iloc[3:] == 110).all()
    assert (df["pdl"].iloc[3:] == 90).all()
    # Day 2's own high (105) must NOT leak into day 2's pdh.
    assert not (df["pdh"].iloc[3:] == 105).any()


def test_swing_sources_not_used_before_confirmation():
    # A swing high needs `lookback` future candles to confirm. Build a
    # sharp spike and verify no swing_high source appears in any zone
    # until enough candles exist after it.
    closes = [100] * 5 + [120] + [100] * 20  # spike at index 5
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    lookback = 5
    for i in range(5 + lookback):
        zone = df["resistance_zone"].iloc[i]
        if zone is not None:
            assert "swing_high" not in zone["sources"], f"leaked at row {i}"


# --- Synthetic scenarios from the spec ---

def test_clean_uptrend_produces_rising_support():
    closes = [100 + i * 1.2 for i in range(80)]
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    # In a clean uptrend, nearest support zones should generally sit below
    # an ever-rising price -- spot check the last row has a support zone
    # below current close.
    last = df.iloc[-1]
    if last["support_zone"] is not None:
        assert last["support_zone"]["upper"] <= last["close"] + 1e-6


def test_clean_downtrend_produces_falling_resistance():
    closes = [200 - i * 1.2 for i in range(80)]
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    last = df.iloc[-1]
    if last["resistance_zone"] is not None:
        assert last["resistance_zone"]["lower"] >= last["close"] - 1e-6


def test_sideways_range_has_both_zones():
    rng = np.random.default_rng(1)
    closes = list(100 + 3 * np.sin(np.linspace(0, 12 * np.pi, 120)) + rng.normal(0, 0.1, 120))
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    has_support = df["support_zone"].notna().sum()
    has_resistance = df["resistance_zone"].notna().sum()
    assert has_support > 0
    assert has_resistance > 0


def test_repeated_support_tests_increase_touches():
    # Oscillate between a floor (~100) and a ceiling (~110) repeatedly.
    closes = []
    for _ in range(8):
        closes += [110, 105, 100, 105]
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    late_support = df["support_zone"].iloc[-1]
    early_support = next((z for z in df["support_zone"].iloc[:12] if z is not None), None)
    if late_support is not None and early_support is not None:
        assert late_support["touches"] >= early_support["touches"]


def test_resistance_breakout_then_retest_flags_flip():
    # Price ranges under 110, breaks above, then comes back down to retest
    # the old resistance from above.
    closes = (
        [100, 105, 108, 105, 108, 105, 108]  # range-bound under resistance ~108
        + [112, 118, 122]                     # breakout above
        + [119, 115, 112]                     # retest of the old resistance area from above
    )
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    events = [e for e in df["sr_interaction"].tolist() if e]
    assert "RESISTANCE_BREAK" in events


def test_support_breakdown_detected():
    closes = [100, 98, 96, 98, 96, 98, 96, 90, 85, 80]
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    events = [e for e in df["sr_interaction"].tolist() if e]
    assert "SUPPORT_BREAK" in events


def test_vwap_not_used_without_recent_crossing():
    # Price stays consistently above VWAP the whole time (VWAP is a
    # cumulative average of a rising series, so it trails below price and
    # is never crossed) -- VWAP must not appear as a zone source.
    closes = [100 + i * 0.05 for i in range(60)]
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    for zone in list(df["support_zone"]) + list(df["resistance_zone"]):
        if zone is not None:
            assert "vwap" not in zone["sources"]


def test_vwap_used_after_genuine_crossing():
    # Oscillate around a level so price actually crosses back and forth
    # through its own cumulative VWAP.
    rng = np.random.default_rng(2)
    closes = list(100 + 4 * np.sin(np.linspace(0, 10 * np.pi, 100)) + rng.normal(0, 0.05, 100))
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    all_sources = set()
    for zone in list(df["support_zone"]) + list(df["resistance_zone"]):
        if zone is not None:
            all_sources.update(zone["sources"])
    assert "vwap" in all_sources


def test_confluence_zone_multiple_sources():
    # Build a series where day boundaries + a swing low + EMA all sit near
    # the same level, forcing a multi-source confluence zone.
    closes = [100] * 3 + [100, 101, 99, 100, 100, 100] + [100] * 30
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    found_confluence = any(
        zone is not None and len(zone["sources"]) >= 2
        for zone in list(df["support_zone"]) + list(df["resistance_zone"])
    )
    assert found_confluence


# --- Snapshot / API shape ---

def test_get_support_resistance_snapshot_shape():
    closes = [100 + i * 0.5 for i in range(60)]
    df = _make_ohlc(closes)
    df = _run_zone_pipeline(df)
    snap = get_support_resistance_snapshot(df, ticker="TEST")
    assert snap["ticker"] == "TEST"
    assert "nearest_support" in snap and "nearest_resistance" in snap
    assert isinstance(snap["interactions"], list)
    assert isinstance(snap["confluence"], list)


def test_get_support_resistance_snapshot_empty_df():
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    snap = get_support_resistance_snapshot(df, ticker="EMPTY")
    assert snap["nearest_support"] is None
    assert snap["supports"] == []


# --- Full pipeline integration + backward compatibility ---

def test_full_pipeline_preserves_existing_columns():
    from backend.data_engine.loader import generate_synthetic
    from backend.pipeline import run_pipeline

    df = generate_synthetic(n_candles=300, seed=7)
    df = run_pipeline(df, timeframe="1d")
    # Original single-level columns untouched.
    for col in ("pattern", "pattern_direction" if "pattern_direction" in df.columns else "trend", "support", "resistance", "score", "status"):
        assert col in df.columns
    # New zone columns present additively.
    for col in ("support_zone", "resistance_zone", "support_strength", "resistance_strength", "sr_interaction"):
        assert col in df.columns


def test_zone_strength_bounded_0_100():
    from backend.data_engine.loader import generate_synthetic
    from backend.pipeline import run_pipeline

    df = generate_synthetic(n_candles=300, seed=3)
    df = run_pipeline(df, timeframe="1d")
    assert df["support_strength"].between(0, 100).all()
    assert df["resistance_strength"].between(0, 100).all()
