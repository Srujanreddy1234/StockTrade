"""Volume + VWAP intelligence (Step 11).

Reuses the existing `volume` and `vwap` columns (indicator_engine.py) --
no second VWAP implementation, no new indicator. This module answers two
separate questions that earlier stages only used internally:

1. Is CURRENT volume unusual, and in which direction is it trending?
   (breakout_engine already has its own inline volume-confirmation check
   for breakouts specifically, using the same 20-period/1.5x convention as
   scoring_engine -- this module generalizes that into a reusable
   volume_state so pullback/reversal events, which currently ignore volume
   entirely, can be ANNOTATED with whether volume corroborates them,
   without reopening or re-testing Step 9/10's already-passing logic.)
2. How is price behaving relative to VWAP specifically -- not just "is it
   nearby" (zone_engine already requires a genuine crossing before trusting
   VWAP as a zone source), but the moment-to-moment reclaim/loss/rejection
   dynamic VWAP is actually used for intraday.

Nothing here gates or overrides an earlier engine's verdict -- it adds an
informational `volume_confirms_event` flag next to whatever breakout/
pullback/reversal event already fired, and its own independent VWAP
interaction events.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

VOLUME_SPIKE_MULT = 1.5  # matches scoring_engine/breakout_engine's existing threshold
VOLUME_EXPANSION_MULT = 1.2
VOLUME_CONTRACTION_MULT = 0.7
VOLUME_ROLLING_WINDOW = 20
VOLUME_TREND_WINDOW = 5

VWAP_SLOPE_WINDOW = 5
VWAP_NEAR_ATR_MULT = 0.3
VWAP_NEAR_PCT_FALLBACK = 0.002


def add_volume_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add volume_ratio, volume_state, volume_trend."""
    df = df.copy()
    vol_mean = df["volume"].rolling(VOLUME_ROLLING_WINDOW, min_periods=1).mean()
    ratio = np.where(vol_mean > 0, df["volume"] / vol_mean, np.nan)
    df["volume_ratio"] = ratio

    state = np.full(len(df), "NORMAL", dtype=object)
    state[ratio >= VOLUME_SPIKE_MULT] = "SPIKE"
    state[(ratio >= VOLUME_EXPANSION_MULT) & (ratio < VOLUME_SPIKE_MULT)] = "EXPANSION"
    state[ratio <= VOLUME_CONTRACTION_MULT] = "CONTRACTION"
    state[np.isnan(ratio)] = "UNKNOWN"
    df["volume_state"] = state

    short_avg = df["volume"].rolling(VOLUME_TREND_WINDOW, min_periods=1).mean()
    prev_short_avg = short_avg.shift(VOLUME_TREND_WINDOW)
    trend = np.full(len(df), "FLAT", dtype=object)
    with np.errstate(invalid="ignore"):
        rising = (short_avg.to_numpy() > prev_short_avg.to_numpy() * 1.1)
        falling = (short_avg.to_numpy() < prev_short_avg.to_numpy() * 0.9)
    trend[np.nan_to_num(rising, nan=False)] = "RISING"
    trend[np.nan_to_num(falling, nan=False)] = "FALLING"
    trend[prev_short_avg.isna().to_numpy()] = "UNKNOWN"
    df["volume_trend"] = trend
    return df


def add_volume_confirmation(df: pd.DataFrame) -> pd.DataFrame:
    """Annotate whichever breakout/pullback/reversal event fired this row
    with whether volume corroborates it (SPIKE or EXPANSION). Purely
    informational -- does not change any earlier engine's verdict.
    """
    df = df.copy()
    has_event = pd.Series(False, index=df.index)
    for col in ("breakout_event", "pullback_event", "reversal_event"):
        if col in df.columns:
            has_event = has_event | df[col].notna()
    confirms = df["volume_state"].isin(["SPIKE", "EXPANSION"]) if "volume_state" in df.columns else pd.Series(False, index=df.index)
    df["volume_confirms_event"] = np.where(has_event, confirms, None)
    return df


def add_vwap_interactions(df: pd.DataFrame) -> pd.DataFrame:
    """Add price_vs_vwap, vwap_slope, and a debounced vwap_interaction
    (VWAP_RECLAIM / VWAP_LOSS / VWAP_REJECTION_ABOVE / VWAP_REJECTION_BELOW).
    """
    df = df.copy()
    n = len(df)
    if n == 0 or "vwap" not in df.columns:
        df["price_vs_vwap"] = None
        df["vwap_slope"] = None
        df["vwap_interaction"] = None
        return df

    close = df["close"].to_numpy(dtype=float)
    vwap = df["vwap"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float) if "atr" in df.columns else np.full(n, np.nan)

    price_vs_vwap = np.full(n, None, dtype=object)
    with np.errstate(invalid="ignore"):
        price_vs_vwap[close > vwap] = "ABOVE"
        price_vs_vwap[close < vwap] = "BELOW"
        price_vs_vwap[np.isclose(close, vwap, equal_nan=False)] = "AT"
    df["price_vs_vwap"] = price_vs_vwap

    vwap_series = pd.Series(vwap)
    prev_vwap = vwap_series.shift(VWAP_SLOPE_WINDOW).to_numpy()
    slope = np.full(n, "UNKNOWN", dtype=object)
    with np.errstate(invalid="ignore"):
        rising = vwap > prev_vwap
        falling = vwap < prev_vwap
    slope[np.nan_to_num(rising, nan=False)] = "RISING"
    slope[np.nan_to_num(falling, nan=False)] = "FALLING"
    slope[np.isnan(prev_vwap)] = "UNKNOWN"
    df["vwap_slope"] = slope

    interactions: list[str | None] = [None] * n
    last_interaction = None
    for i in range(n):
        if np.isnan(vwap[i]):
            continue
        near_tol = max(
            (atr[i] * VWAP_NEAR_ATR_MULT) if not np.isnan(atr[i]) else 0.0,
            close[i] * VWAP_NEAR_PCT_FALLBACK,
        )
        pc = close[i - 1] if i > 0 else close[i]
        pv = vwap[i - 1] if i > 0 else vwap[i]

        candidate = None
        if pc < pv and close[i] > vwap[i]:
            candidate = "VWAP_RECLAIM"
        elif pc > pv and close[i] < vwap[i]:
            candidate = "VWAP_LOSS"
        elif close[i] > vwap[i] and (close[i] - vwap[i]) <= near_tol and pc > pv:
            # Approached from above but failed to pull further away -- a
            # rejection back down toward VWAP without actually losing it.
            if i > 0 and close[i] < pc:
                candidate = "VWAP_REJECTION_ABOVE"
        elif close[i] < vwap[i] and (vwap[i] - close[i]) <= near_tol and pc < pv:
            if i > 0 and close[i] > pc:
                candidate = "VWAP_REJECTION_BELOW"

        if candidate is not None and candidate != last_interaction:
            interactions[i] = candidate
            last_interaction = candidate

    df["vwap_interaction"] = interactions
    return df


def add_volume_vwap_intelligence(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience wrapper chaining all three additions in order."""
    df = add_volume_metrics(df)
    df = add_vwap_interactions(df)
    df = add_volume_confirmation(df)
    return df


_VWAP_NOTES = {
    "VWAP_RECLAIM": "Price closed back above VWAP at {level:.2f} after trading below it.",
    "VWAP_LOSS": "Price closed back below VWAP at {level:.2f} after trading above it.",
    "VWAP_REJECTION_ABOVE": "Price approached VWAP at {level:.2f} from above and was rejected back up without losing it.",
    "VWAP_REJECTION_BELOW": "Price approached VWAP at {level:.2f} from below and was rejected back down without reclaiming it.",
}


def get_volume_vwap_events(df: pd.DataFrame, timeframe: str = "unknown") -> list[dict[str, Any]]:
    """Flatten vwap_interaction rows plus any volume SPIKE rows into
    explainable event dicts.
    """
    events: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        vwap_event = row.get("vwap_interaction")
        if vwap_event:
            level = row.get("vwap")
            note = _VWAP_NOTES.get(vwap_event, vwap_event)
            if level is not None and not pd.isna(level):
                note = note.format(level=float(level))
            events.append(
                {
                    "category": "vwap",
                    "event": vwap_event,
                    "timestamp": str(idx),
                    "timeframe": timeframe,
                    "price": round(float(row["close"]), 2),
                    "vwap": round(float(level), 2) if level is not None and not pd.isna(level) else None,
                    "note": note,
                }
            )
        if row.get("volume_state") == "SPIKE":
            events.append(
                {
                    "category": "volume",
                    "event": "VOLUME_SPIKE",
                    "timestamp": str(idx),
                    "timeframe": timeframe,
                    "price": round(float(row["close"]), 2),
                    "volume_ratio": round(float(row.get("volume_ratio", 0) or 0), 2),
                    "note": f"Volume was {row.get('volume_ratio', 0):.1f}x its {VOLUME_ROLLING_WINDOW}-period average.",
                }
            )
    return events
