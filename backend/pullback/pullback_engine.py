"""Pullback / reversal intelligence (Step 10).

Built entirely on columns that already exist by this point in the
pipeline -- no recomputation of trend, structure, zones, or patterns:

- trend, rsi (indicator_engine)
- structure_event/structure_state (market_structure.structure_engine)
- sr_interaction (zones.zone_engine)
- is_bullish, extended_pattern_direction (candles)

PULLBACK: a temporary counter-trend move that tests a zone WITHOUT
breaking structure (no CHoCH yet). Classified as:
  - PULLBACK_CONTINUATION_*: the pullback found support/resistance and a
    same-direction-as-trend candle appeared -- the trend may be resuming.
  - PULLBACK_EXHAUSTION_*: the pullback is still intact (structure not
    broken) but momentum (RSI) is stretched, an early warning that the
    pullback may be deepening into something more than a pullback.

REVERSAL: triggered off an actual CHoCH (backend/market_structure --
"the first sign the prevailing structure may be breaking down", already a
real event, not a light touch) with a `confidence` field ("low"/"medium"/
"high") based on how much corroborating evidence exists, rather than
either always trusting a bare CHoCH or refusing to report one without
perfect confirmation. Always reported as POTENTIAL_REVERSAL_*, never a
guaranteed one -- per the project's explicit rule against overclaiming.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_RSI_OVERBOUGHT_PULLBACK_WARN = 60.0
_RSI_OVERSOLD_PULLBACK_WARN = 40.0
_MOMENTUM_LOOKBACK = 3

_PULLBACK_NOTES = {
    "PULLBACK_CONTINUATION_BULLISH": "Uptrend pulled back to support at {level:.2f} and held with a bullish candle -- possible trend resumption.",
    "PULLBACK_CONTINUATION_BEARISH": "Downtrend pulled back to resistance at {level:.2f} and held with a bearish candle -- possible trend resumption.",
    "PULLBACK_EXHAUSTION_BULLISH": "Uptrend pullback is approaching support at {level:.2f} with RSI already stretched low -- structure intact, but watch for a deeper move.",
    "PULLBACK_EXHAUSTION_BEARISH": "Downtrend pullback is approaching resistance at {level:.2f} with RSI already stretched high -- structure intact, but watch for a deeper move.",
}


def add_pullback_reversal_events(df: pd.DataFrame) -> pd.DataFrame:
    """Add `pullback_event`, `reversal_event` (None or an event name), and
    `reversal_confidence` ("low"/"medium"/"high"/None).

    Requires trend, rsi, structure_event, sr_interaction, is_bullish, and
    (if present) extended_pattern_direction to already exist.
    """
    df = df.copy()
    n = len(df)
    pullback: list[str | None] = [None] * n
    reversal: list[str | None] = [None] * n
    confidence: list[str | None] = [None] * n
    if n == 0:
        df["pullback_event"] = pullback
        df["reversal_event"] = reversal
        df["reversal_confidence"] = confidence
        return df

    trend = df["trend"].to_numpy() if "trend" in df.columns else np.full(n, "sideways")
    rsi = df["rsi"].to_numpy(dtype=float) if "rsi" in df.columns else np.full(n, 50.0)
    structure_event = df["structure_event"].to_numpy() if "structure_event" in df.columns else np.full(n, None)
    sr_interaction = df["sr_interaction"].to_numpy() if "sr_interaction" in df.columns else np.full(n, None)
    is_bullish = df["is_bullish"].to_numpy() if "is_bullish" in df.columns else np.full(n, False)
    ext_direction = (
        df["extended_pattern_direction"].to_numpy() if "extended_pattern_direction" in df.columns else np.full(n, None)
    )
    support_zone = df["support_zone"].tolist() if "support_zone" in df.columns else [None] * n
    resistance_zone = df["resistance_zone"].tolist() if "resistance_zone" in df.columns else [None] * n
    breakout_event = df["breakout_event"].to_numpy() if "breakout_event" in df.columns else np.full(n, None)

    for i in range(n):
        # --- Pullback classification ---
        interaction = sr_interaction[i]
        if trend[i] == "uptrend":
            if interaction in ("TOUCH_SUPPORT", "SUPPORT_REJECTION") and is_bullish[i]:
                pullback[i] = "PULLBACK_CONTINUATION_BULLISH"
            elif interaction == "APPROACHING_SUPPORT" and not np.isnan(rsi[i]) and rsi[i] < _RSI_OVERSOLD_PULLBACK_WARN:
                pullback[i] = "PULLBACK_EXHAUSTION_BULLISH"
        elif trend[i] == "downtrend":
            if interaction in ("TOUCH_RESISTANCE", "RESISTANCE_REJECTION") and not is_bullish[i]:
                pullback[i] = "PULLBACK_CONTINUATION_BEARISH"
            elif interaction == "APPROACHING_RESISTANCE" and not np.isnan(rsi[i]) and rsi[i] > _RSI_OVERBOUGHT_PULLBACK_WARN:
                pullback[i] = "PULLBACK_EXHAUSTION_BEARISH"

        # --- Reversal classification: triggered by an actual CHoCH ---
        event = structure_event[i]
        if event == "CHoCH_bearish":
            evidence = 0
            if not is_bullish[i]:
                evidence += 1
            if ext_direction[i] == "bearish":
                evidence += 1
            if i >= _MOMENTUM_LOOKBACK and not np.isnan(rsi[i]) and not np.isnan(rsi[i - _MOMENTUM_LOOKBACK]):
                if rsi[i] < rsi[i - _MOMENTUM_LOOKBACK]:
                    evidence += 1
            if breakout_event[i] in ("FALSE_BREAKOUT_BULLISH",):
                evidence += 1
            reversal[i] = "POTENTIAL_REVERSAL_BEARISH"
            confidence[i] = "high" if evidence >= 3 else ("medium" if evidence >= 1 else "low")
        elif event == "CHoCH_bullish":
            evidence = 0
            if is_bullish[i]:
                evidence += 1
            if ext_direction[i] == "bullish":
                evidence += 1
            if i >= _MOMENTUM_LOOKBACK and not np.isnan(rsi[i]) and not np.isnan(rsi[i - _MOMENTUM_LOOKBACK]):
                if rsi[i] > rsi[i - _MOMENTUM_LOOKBACK]:
                    evidence += 1
            if breakout_event[i] in ("FALSE_BREAKOUT_BEARISH",):
                evidence += 1
            reversal[i] = "POTENTIAL_REVERSAL_BULLISH"
            confidence[i] = "high" if evidence >= 3 else ("medium" if evidence >= 1 else "low")

    df["pullback_event"] = pullback
    df["reversal_event"] = reversal
    df["reversal_confidence"] = confidence
    return df


def get_pullback_reversal_events(df: pd.DataFrame, timeframe: str = "unknown") -> list[dict[str, Any]]:
    """Flatten pullback_event/reversal_event rows into explainable dicts."""
    events: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        pb = row.get("pullback_event")
        if pb:
            zone = row.get("support_zone") if "BULLISH" in pb else row.get("resistance_zone")
            level = zone.get("upper") if zone and "BULLISH" in pb else (zone.get("lower") if zone else None)
            note = _PULLBACK_NOTES.get(pb, pb)
            if level is not None:
                note = note.format(level=level)
            events.append(
                {
                    "category": "pullback",
                    "event": pb,
                    "timestamp": str(idx),
                    "timeframe": timeframe,
                    "price": round(float(row["close"]), 2),
                    "level": round(float(level), 2) if level is not None else None,
                    "note": note,
                }
            )
        rv = row.get("reversal_event")
        if rv:
            events.append(
                {
                    "category": "reversal",
                    "event": rv,
                    "timestamp": str(idx),
                    "timeframe": timeframe,
                    "price": round(float(row["close"]), 2),
                    "confidence": row.get("reversal_confidence"),
                    "note": (
                        f"{rv.replace('_', ' ').title()} (confidence: {row.get('reversal_confidence')}) -- "
                        "structure just changed character; this is a potential reversal signal, not a confirmed one."
                    ),
                }
            )
    return events
