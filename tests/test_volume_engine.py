"""Tests for the volume + VWAP intelligence engine (Step 11)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.volume.volume_engine import (
    add_volume_metrics,
    add_vwap_interactions,
    add_volume_confirmation,
    get_volume_vwap_events,
)


def _df(n: int, **cols) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    base = {
        "close": [100.0] * n,
        "volume": [1000.0] * n,
    }
    base.update(cols)
    return pd.DataFrame(base, index=idx)


# --- Volume metrics ---

def test_volume_spike_detected():
    df = _df(21, volume=[1000.0] * 20 + [3000.0])
    df = add_volume_metrics(df)
    assert df["volume_state"].iloc[-1] == "SPIKE"
    assert df["volume_ratio"].iloc[-1] > 1.5


def test_volume_normal_when_flat():
    df = _df(21, volume=[1000.0] * 21)
    df = add_volume_metrics(df)
    assert df["volume_state"].iloc[-1] == "NORMAL"


def test_volume_contraction_detected():
    df = _df(21, volume=[1000.0] * 20 + [500.0])
    df = add_volume_metrics(df)
    assert df["volume_state"].iloc[-1] == "CONTRACTION"


def test_volume_trend_rising():
    # Last 5 candles' volume clearly higher than the 5 candles before them.
    volume = [1000.0] * 10 + [2000.0] * 5
    df = _df(15, volume=volume)
    df = add_volume_metrics(df)
    assert df["volume_trend"].iloc[-1] == "RISING"


def test_volume_ratio_nan_when_no_history_gracefully_unknown():
    df = _df(1, volume=[1000.0])
    df = add_volume_metrics(df)
    # A single row still produces a ratio (mean of itself), not a crash.
    assert not pd.isna(df["volume_ratio"].iloc[0])


# --- VWAP interactions ---

def test_vwap_reclaim_detected():
    df = _df(3, close=[95.0, 96.0, 105.0], vwap=[100.0, 100.0, 100.0], atr=[1.0, 1.0, 1.0])
    df = add_vwap_interactions(df)
    assert df["vwap_interaction"].iloc[2] == "VWAP_RECLAIM"
    assert df["price_vs_vwap"].iloc[2] == "ABOVE"


def test_vwap_loss_detected():
    df = _df(3, close=[105.0, 104.0, 95.0], vwap=[100.0, 100.0, 100.0], atr=[1.0, 1.0, 1.0])
    df = add_vwap_interactions(df)
    assert df["vwap_interaction"].iloc[2] == "VWAP_LOSS"
    assert df["price_vs_vwap"].iloc[2] == "BELOW"


def test_vwap_rejection_above():
    # Price stays above VWAP but a candle pokes down near it then closes
    # lower than the prior close without actually losing VWAP.
    df = _df(3, close=[110.0, 101.5, 100.2], vwap=[100.0, 100.0, 100.0], atr=[1.0, 1.0, 1.0])
    df = add_vwap_interactions(df)
    assert df["vwap_interaction"].iloc[2] == "VWAP_REJECTION_ABOVE"


def test_vwap_interaction_debounced():
    # Reclaim once, then stay above VWAP for several rows -- should not
    # re-fire VWAP_RECLAIM every row.
    df = _df(5, close=[95.0, 105.0, 106.0, 107.0, 108.0], vwap=[100.0] * 5, atr=[1.0] * 5)
    df = add_vwap_interactions(df)
    events = [e for e in df["vwap_interaction"].tolist() if e]
    assert events.count("VWAP_RECLAIM") == 1


def test_vwap_slope_rising():
    df = _df(10, vwap=[100 + i for i in range(10)])
    df = add_vwap_interactions(df)
    assert df["vwap_slope"].iloc[-1] == "RISING"


def test_no_vwap_column_does_not_crash():
    df = _df(3)
    result = add_vwap_interactions(df)
    assert result["vwap_interaction"].isna().all()


# --- Volume confirmation annotation ---

def test_volume_confirmation_true_on_spike_with_event():
    df = _df(21, volume=[1000.0] * 20 + [3000.0])
    df["breakout_event"] = [None] * 20 + ["CONFIRMED_BREAKOUT_BULLISH"]
    df["pullback_event"] = [None] * 21
    df["reversal_event"] = [None] * 21
    df = add_volume_metrics(df)
    df = add_volume_confirmation(df)
    assert df["volume_confirms_event"].iloc[-1] is True


def test_volume_confirmation_false_without_spike():
    df = _df(21, volume=[1000.0] * 21)
    df["breakout_event"] = [None] * 20 + ["CONFIRMED_BREAKOUT_BULLISH"]
    df["pullback_event"] = [None] * 21
    df["reversal_event"] = [None] * 21
    df = add_volume_metrics(df)
    df = add_volume_confirmation(df)
    assert df["volume_confirms_event"].iloc[-1] is False


def test_volume_confirmation_none_when_no_event():
    df = _df(21, volume=[1000.0] * 20 + [3000.0])
    df["breakout_event"] = [None] * 21
    df["pullback_event"] = [None] * 21
    df["reversal_event"] = [None] * 21
    df = add_volume_metrics(df)
    df = add_volume_confirmation(df)
    assert df["volume_confirms_event"].iloc[-1] is None


# --- Explainable events ---

def test_get_volume_vwap_events_shape():
    df = _df(21, volume=[1000.0] * 20 + [3000.0], close=[100.0] * 20 + [105.0], vwap=[95.0] * 21, atr=[1.0] * 21)
    df = add_volume_metrics(df)
    df = add_vwap_interactions(df)
    events = get_volume_vwap_events(df, timeframe="1d")
    categories = {e["category"] for e in events}
    assert "volume" in categories
    volume_events = [e for e in events if e["category"] == "volume"]
    assert volume_events[0]["event"] == "VOLUME_SPIKE"


def test_full_pipeline_integration():
    from backend.data_engine.loader import generate_synthetic
    from backend.pipeline import run_pipeline

    df = generate_synthetic(n_candles=300, seed=17)
    df = run_pipeline(df, timeframe="1d")
    for col in ("volume_ratio", "volume_state", "volume_trend", "price_vs_vwap", "vwap_slope", "vwap_interaction", "volume_confirms_event"):
        assert col in df.columns
    # Earlier steps' columns untouched.
    for col in ("pattern", "trend", "support_zone", "breakout_event", "pullback_event", "reversal_event"):
        assert col in df.columns
    assert df["volume_state"].isin(["SPIKE", "EXPANSION", "NORMAL", "CONTRACTION", "UNKNOWN"]).all()
