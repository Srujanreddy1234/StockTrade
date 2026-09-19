"""Breakout / breakdown / retest intelligence (Step 9).

Builds on top of two existing, already-tested layers rather than
duplicating their logic:

- backend/zones/zone_engine.py's `sr_interaction` column already tells us
  the exact row a zone was broken (SUPPORT_BREAK / RESISTANCE_BREAK), and
  the broken zone's bounds are attached to that same row's support_zone /
  resistance_zone dict (state == "BROKEN").
- backend/indicators/indicator_engine.py's atr/ema/volume are reused as-is
  for confirmation evidence.

This module answers one question the zone engine deliberately does NOT:
was a break just noise, or is it actually a breakout worth trusting? A raw
`SUPPORT_BREAK`/`RESISTANCE_BREAK` interaction means "price closed beyond
the zone" -- nothing more. This module classifies that event through a
confirmation checklist and then tracks what happens next (a retest).

CONFIRMATION CHECKLIST (all three required for CONFIRMED, matching the
spec's "close beyond the zone, sufficient distance, volume, momentum,
follow-through" requirements at v1 scope -- not overengineered further):
  1. Distance: close is at least `MIN_BREAK_ATR_MULT` x ATR beyond the zone
     (a break by a few paise/cents is not a breakout).
  2. Volume: current volume > `VOLUME_CONFIRM_MULT` x its 20-period rolling
     average -- the SAME threshold and rolling window scoring_engine.py
     already uses for "volume_above_average", reused here for consistency
     rather than inventing a second volume-significance number.
  3. Momentum: close is on the correct side of EMA20 (above for a bullish
     break, below for a bearish one) -- the only trend-following average
     that already exists in this codebase (no SMA is implemented, so none
     is invented here).

If a break doesn't meet the checklist immediately, it is given a short
grace window (`CONFIRM_WINDOW_CANDLES`) to still confirm. If the window
expires WITHOUT confirmation and price has already reversed back through
the zone, it's labeled a false breakout. If the window expires and price
is still beyond the zone but simply lacks a confirming volume/momentum
reading, no verdict is forced -- per the spec, "do not label every failed
breakout as a confirmed false breakout without sufficient evidence."

RETEST tracking: once CONFIRMED, the engine watches subsequent candles
(`RETEST_WINDOW_CANDLES`) for price returning into the broken zone. If it
bounces back out in the breakout direction, that's a successful retest
(the flipped zone held). If it closes back through in the original
direction, the earlier confirmation is overturned as a false breakout.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

MIN_BREAK_ATR_MULT = 0.25
MIN_BREAK_PCT_FALLBACK = 0.005  # 0.5% of price -- same fallback pattern as zone_engine's tolerance
VOLUME_CONFIRM_MULT = 1.5  # matches scoring_engine.add_scores' volume threshold
CONFIRM_WINDOW_CANDLES = 3
RETEST_WINDOW_CANDLES = 20

_NOTES = {
    "POSSIBLE_BREAKOUT_BULLISH": "Price closed above resistance at {level:.2f}, but volume/momentum confirmation is still pending.",
    "POSSIBLE_BREAKOUT_BEARISH": "Price closed below support at {level:.2f}, but volume/momentum confirmation is still pending.",
    "CONFIRMED_BREAKOUT_BULLISH": "Confirmed bullish breakout above {level:.2f}: sufficient distance, volume expansion, and price above EMA20.",
    "CONFIRMED_BREAKOUT_BEARISH": "Confirmed bearish breakdown below {level:.2f}: sufficient distance, volume expansion, and price below EMA20.",
    "FALSE_BREAKOUT_BULLISH": "Breakout above {level:.2f} failed to hold -- price closed back below the broken zone.",
    "FALSE_BREAKOUT_BEARISH": "Breakdown below {level:.2f} failed to hold -- price closed back above the broken zone.",
    "BREAKOUT_RETEST_SUCCESS_BULLISH": "Price retested the broken resistance at {level:.2f} from above and held -- now acting as support.",
    "BREAKOUT_RETEST_SUCCESS_BEARISH": "Price retested the broken support at {level:.2f} from below and held -- now acting as resistance.",
}


def _confirmation_evidence(
    direction: str, close: float, zone_lower: float, zone_upper: float, atr: float, volume: float, vol_mean: float, ema: float
) -> bool:
    # Fails gracefully when ATR is NaN (too little history for the 14-period
    # ATR yet) by falling back to a percentage-of-price minimum distance --
    # the same two-sided tolerance pattern zone_engine's clustering already
    # uses, rather than refusing to ever confirm a breakout early in a series.
    min_distance = max(
        (atr * MIN_BREAK_ATR_MULT) if not np.isnan(atr) else 0.0,
        close * MIN_BREAK_PCT_FALLBACK,
    )
    if direction == "bullish":
        distance_ok = (close - zone_upper) >= min_distance
        momentum_ok = not np.isnan(ema) and close > ema
    else:
        distance_ok = (zone_lower - close) >= min_distance
        momentum_ok = not np.isnan(ema) and close < ema
    volume_ok = not np.isnan(vol_mean) and vol_mean > 0 and volume > VOLUME_CONFIRM_MULT * vol_mean
    return distance_ok and volume_ok and momentum_ok


def add_breakout_events(df: pd.DataFrame, timeframe: str = "unknown") -> pd.DataFrame:
    """Add `breakout_event` (None or one of the event names above) and
    `breakout_direction` ("bullish"/"bearish"/None).

    Requires sr_interaction/support_zone/resistance_zone (zone_engine) and
    atr/ema (indicator_engine) to already exist.
    """
    df = df.copy()
    n = len(df)
    events: list[str | None] = [None] * n
    directions: list[str | None] = [None] * n
    # The zone bounds tied to each event. Recorded at event-creation time
    # from the pending record (which remembers the ORIGINAL broken zone),
    # not read back from the current row's support_zone/resistance_zone --
    # those only hold that zone on the break row itself; a confirmation or
    # retest resolved several candles later would otherwise find None/an
    # unrelated zone there.
    event_lower: list[float | None] = [None] * n
    event_upper: list[float | None] = [None] * n
    if n == 0:
        df["breakout_event"] = events
        df["breakout_direction"] = directions
        df["breakout_zone_lower"] = event_lower
        df["breakout_zone_upper"] = event_upper
        return df

    close = df["close"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float) if "atr" in df.columns else np.full(n, np.nan)
    ema = df["ema"].to_numpy(dtype=float) if "ema" in df.columns else np.full(n, np.nan)
    volume = df["volume"].to_numpy(dtype=float)
    vol_mean = df["volume"].rolling(20, min_periods=1).mean().to_numpy()
    sr_interaction = df["sr_interaction"].to_numpy() if "sr_interaction" in df.columns else np.full(n, None)
    support_zone = df["support_zone"].tolist() if "support_zone" in df.columns else [None] * n
    resistance_zone = df["resistance_zone"].tolist() if "resistance_zone" in df.columns else [None] * n

    # One pending record per tracked break: {direction, lower, upper,
    # confirmed, confirm_deadline, resolved}
    pending: list[dict[str, Any]] = []

    for i in range(n):
        still_pending = []
        for rec in pending:
            if rec["resolved"]:
                continue
            lower, upper, direction = rec["lower"], rec["upper"], rec["direction"]
            if not rec["confirmed"]:
                if i <= rec["confirm_deadline"]:
                    if _confirmation_evidence(direction, close[i], lower, upper, atr[i], volume[i], vol_mean[i], ema[i]):
                        rec["confirmed"] = True
                        rec["retest_deadline"] = i + RETEST_WINDOW_CANDLES
                        key = "CONFIRMED_BREAKOUT_BULLISH" if direction == "bullish" else "CONFIRMED_BREAKOUT_BEARISH"
                        if events[i] is None:
                            events[i] = key
                            directions[i] = direction
                            event_lower[i], event_upper[i] = lower, upper
                        still_pending.append(rec)
                        continue
                    still_pending.append(rec)
                    continue
                else:
                    # Grace window expired without confirmation. Only call
                    # it false if price has actually reversed back through
                    # the zone; otherwise leave it an open, unresolved case.
                    reversed_back = (direction == "bullish" and close[i] < lower) or (
                        direction == "bearish" and close[i] > upper
                    )
                    if reversed_back:
                        key = "FALSE_BREAKOUT_BULLISH" if direction == "bullish" else "FALSE_BREAKOUT_BEARISH"
                        if events[i] is None:
                            events[i] = key
                            directions[i] = direction
                            event_lower[i], event_upper[i] = lower, upper
                        rec["resolved"] = True
                    continue  # drop from tracking either way -- window is over
            else:
                if i > rec.get("retest_deadline", i):
                    continue  # retest window expired, drop silently
                in_zone = (df["low"].iloc[i] <= upper) and (df["high"].iloc[i] >= lower)
                if not in_zone:
                    still_pending.append(rec)
                    continue
                if direction == "bullish":
                    if close[i] > upper:
                        key = "BREAKOUT_RETEST_SUCCESS_BULLISH"
                    elif close[i] < lower:
                        key = "FALSE_BREAKOUT_BULLISH"
                    else:
                        still_pending.append(rec)
                        continue
                else:
                    if close[i] < lower:
                        key = "BREAKOUT_RETEST_SUCCESS_BEARISH"
                    elif close[i] > upper:
                        key = "FALSE_BREAKOUT_BEARISH"
                    else:
                        still_pending.append(rec)
                        continue
                if events[i] is None:
                    events[i] = key
                    directions[i] = direction
                    event_lower[i], event_upper[i] = lower, upper
                rec["resolved"] = True
        pending = still_pending

        interaction = sr_interaction[i]
        if interaction == "RESISTANCE_BREAK" and resistance_zone[i] is not None:
            z = resistance_zone[i]
            lower, upper = z["lower"], z["upper"]
            confirmed_now = _confirmation_evidence("bullish", close[i], lower, upper, atr[i], volume[i], vol_mean[i], ema[i])
            key = "CONFIRMED_BREAKOUT_BULLISH" if confirmed_now else "POSSIBLE_BREAKOUT_BULLISH"
            if events[i] is None:
                events[i] = key
                directions[i] = "bullish"
                event_lower[i], event_upper[i] = lower, upper
            pending.append(
                {
                    "direction": "bullish",
                    "lower": lower,
                    "upper": upper,
                    "confirmed": confirmed_now,
                    "confirm_deadline": i + CONFIRM_WINDOW_CANDLES,
                    "retest_deadline": i + RETEST_WINDOW_CANDLES if confirmed_now else None,
                    "resolved": False,
                }
            )
        elif interaction == "SUPPORT_BREAK" and support_zone[i] is not None:
            z = support_zone[i]
            lower, upper = z["lower"], z["upper"]
            confirmed_now = _confirmation_evidence("bearish", close[i], lower, upper, atr[i], volume[i], vol_mean[i], ema[i])
            key = "CONFIRMED_BREAKOUT_BEARISH" if confirmed_now else "POSSIBLE_BREAKOUT_BEARISH"
            if events[i] is None:
                events[i] = key
                directions[i] = "bearish"
                event_lower[i], event_upper[i] = lower, upper
            pending.append(
                {
                    "direction": "bearish",
                    "lower": lower,
                    "upper": upper,
                    "confirmed": confirmed_now,
                    "confirm_deadline": i + CONFIRM_WINDOW_CANDLES,
                    "retest_deadline": i + RETEST_WINDOW_CANDLES if confirmed_now else None,
                    "resolved": False,
                }
            )

    df["breakout_event"] = events
    df["breakout_direction"] = directions
    df["breakout_zone_lower"] = event_lower
    df["breakout_zone_upper"] = event_upper
    return df


def get_breakout_events(df: pd.DataFrame, timeframe: str = "unknown") -> list[dict[str, Any]]:
    """Flatten breakout_event rows into explainable event dicts."""
    events: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        event = row.get("breakout_event")
        if not event:
            continue
        direction = row.get("breakout_direction")
        lower, upper = row.get("breakout_zone_lower"), row.get("breakout_zone_upper")
        level = upper if direction == "bullish" else lower
        note = _NOTES.get(event, event)
        if level is not None and not pd.isna(level):
            note = note.format(level=float(level))
        events.append(
            {
                "event": event,
                "direction": direction,
                "timestamp": str(idx),
                "timeframe": timeframe,
                "price": round(float(row["close"]), 2),
                "level": round(float(level), 2) if level is not None and not pd.isna(level) else None,
                "note": note,
            }
        )
    return events
