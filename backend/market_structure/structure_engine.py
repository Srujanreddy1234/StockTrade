"""Explicit market-structure events: Break of Structure (BOS) and Change of
Character (CHoCH).

This does NOT recompute swings, trend, or support/resistance -- those
already exist in backend/indicators/indicator_engine.py (swing_high/
swing_low, trend, support, resistance) and are reused as-is, per the
project's no-duplicated-business-logic rule. This module only adds the
event layer on top: the moment price actually crosses one of those
already-computed levels, and what that crossing means for the prevailing
structure.

Definitions used here (standard price-action terminology):

- BOS (Break of Structure): price closes beyond the structural level in the
  direction that AGREES with the current trend -- an uptrend closing above
  resistance (a new higher high), or a downtrend closing below support (a
  new lower low). This confirms trend continuation.
- CHoCH (Change of Character): price closes beyond the structural level
  AGAINST the current trend -- an uptrend closing below its support (the
  higher-low that was propping it up), or a downtrend closing above its
  resistance. This is the first sign the prevailing structure may be
  breaking down, not a confirmed reversal by itself.
- Range breakout: the sideways-trend equivalent -- price closing beyond
  support or resistance while no trend was established yet.

`trend`/`support`/`resistance` at row i are computed by indicator_engine
using only data available up to i (see find_swing_points' *_confirmed_at
columns), so reading them at the breakout candle itself does not leak
future information -- the trend label lags a new swing by `lookback`
candles precisely because it isn't confirmed yet.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_EVENT_NOTE = {
    "BOS_bullish": "Break of Structure (bullish): price closed above resistance at {level:.2f}, continuing the uptrend.",
    "BOS_bearish": "Break of Structure (bearish): price closed below support at {level:.2f}, continuing the downtrend.",
    "CHoCH_bullish": "Change of Character (bullish): price closed above resistance at {level:.2f}, breaking the prior downtrend structure.",
    "CHoCH_bearish": "Change of Character (bearish): price closed below support at {level:.2f}, breaking the prior uptrend structure.",
    "range_breakout_bullish": "Range breakout (bullish): price closed above resistance at {level:.2f} with no established trend beforehand.",
    "range_breakout_bearish": "Range breakout (bearish): price closed below support at {level:.2f} with no established trend beforehand.",
}

_DIRECTION = {
    "BOS_bullish": "bullish",
    "BOS_bearish": "bearish",
    "CHoCH_bullish": "bullish",
    "CHoCH_bearish": "bearish",
    "range_breakout_bullish": "bullish",
    "range_breakout_bearish": "bearish",
}


def add_structure_events(df: pd.DataFrame) -> pd.DataFrame:
    """Add `structure_event` (None or one of the event names above) and
    `structure_state` (running "bullish"/"bearish"/"neutral" regime, updated
    whenever a BOS or CHoCH fires, held otherwise).

    Requires trend, support, and resistance columns to already exist
    (i.e. must run after indicator_engine.add_support_resistance).
    """
    df = df.copy()
    n = len(df)
    events = np.full(n, None, dtype=object)
    states = np.full(n, None, dtype=object)

    trend = df["trend"].to_numpy()
    close = df["close"].to_numpy(dtype=float)
    support = df["support"].to_numpy(dtype=float)
    resistance = df["resistance"].to_numpy(dtype=float)

    state = "neutral"
    prev_close = close[0] if n else np.nan

    for i in range(n):
        c = close[i]
        pc = prev_close
        t = trend[i]
        event = None

        # support/resistance at row i are recomputed relative to row i's own
        # close (support is always <= price, resistance always >= price, by
        # construction in indicator_engine) -- so they can never show a
        # "crossing" against themselves. The level that mattered for THIS
        # candle's move is the one still in effect at the previous row,
        # before this candle's own close could have invalidated it.
        s, r = support[i - 1] if i > 0 else support[i], resistance[i - 1] if i > 0 else resistance[i]

        crossed_above_r = not np.isnan(r) and pc <= r < c
        crossed_below_s = not np.isnan(s) and pc >= s > c

        if t == "uptrend":
            if crossed_above_r:
                event = "BOS_bullish"
            elif crossed_below_s:
                event = "CHoCH_bearish"
        elif t == "downtrend":
            if crossed_below_s:
                event = "BOS_bearish"
            elif crossed_above_r:
                event = "CHoCH_bullish"
        else:  # sideways / no established trend yet
            if crossed_above_r:
                event = "range_breakout_bullish"
            elif crossed_below_s:
                event = "range_breakout_bearish"

        if event is not None:
            state = _DIRECTION[event]

        events[i] = event
        states[i] = state
        prev_close = c

    df["structure_event"] = pd.Series(events, index=df.index)
    df["structure_state"] = pd.Series(states, index=df.index)
    return df


def get_structure_events(df: pd.DataFrame, timeframe: str = "unknown") -> list[dict[str, Any]]:
    """Flatten every structure_event row into an explainable event dict."""
    events: list[dict[str, Any]] = []
    # The level that was actually broken is the one in effect BEFORE this
    # candle (see add_structure_events' comment on why current-row support/
    # resistance can't be used for this).
    prev_support = df["support"].shift(1)
    prev_resistance = df["resistance"].shift(1)

    for pos, (idx, row) in enumerate(df.iterrows()):
        event = row.get("structure_event")
        if not event:
            continue
        # BOS_bullish/CHoCH_bullish/range_breakout_bullish break resistance;
        # the bearish variants break support.
        if event in ("BOS_bullish", "CHoCH_bullish", "range_breakout_bullish"):
            level = prev_resistance.iloc[pos]
        else:
            level = prev_support.iloc[pos]
        events.append(
            {
                "event": event,
                "direction": _DIRECTION[event],
                "timestamp": str(idx),
                "timeframe": timeframe,
                "price": round(float(row["close"]), 2),
                "level": None if pd.isna(level) else round(float(level), 2),
                "structure_state": row.get("structure_state"),
                "note": _EVENT_NOTE[event].format(level=float(level)) if not pd.isna(level) else _EVENT_NOTE[event].split(":")[0] + ".",
            }
        )
    return events
