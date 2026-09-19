"""Structured, plain-language explanation of a trading setup for a single row."""

from __future__ import annotations

import pandas as pd
import numpy as np

# Direction each pattern implies, plus a beginner-friendly one-liner describing
# what it means. Phrasing avoids jargon ("buyers stepped in" not "bullish
# absorption").
_PATTERN_INFO = {
    "doji": (
        "neutral",
        "Neither buyers nor sellers took control - the candle closed almost "
        "where it opened, so the market is undecided.",
    ),
    "hammer": (
        "bullish",
        "Sellers pushed the price down during the session, but buyers stepped "
        "back in and closed it near the high - a possible sign of strength.",
    ),
    "shooting_star": (
        "bearish",
        "Buyers pushed the price up, but sellers took over and closed it back "
        "near the low - a possible sign of weakness.",
    ),
    "bullish_engulfing": (
        "bullish",
        "A bullish candle completely swallowed the previous bearish candle, "
        "showing buyers took control.",
    ),
    "bearish_engulfing": (
        "bearish",
        "A bearish candle completely swallowed the previous bullish candle, "
        "showing sellers took control.",
    ),
    "morning_star": (
        "bullish",
        "After a down move, a small indecisive candle appeared and then buyers "
        "pushed the price back up - a possible bullish reversal.",
    ),
    "evening_star": (
        "bearish",
        "After an up move, a small indecisive candle appeared and then sellers "
        "pushed the price back down - a possible bearish reversal.",
    ),
}


def explain(df: pd.DataFrame, loc) -> dict:
    """Return a structured explanation dict for the row at `loc`.

    `loc` may be a row label (index value) or an integer position.
    """
    if not isinstance(loc, (int, np.integer)) or loc in df.index:
        row = df.loc[loc]
    else:
        row = df.iloc[loc]

    pattern = row.get("pattern", None)
    direction, description = _PATTERN_INFO.get(
        pattern,
        ("neutral", "No specific candle pattern fired on this candle."),
    )

    trend = row.get("trend", "sideways")
    if direction == "bullish":
        trend_agrees = trend in ("uptrend", "sideways")
        trend_note = (
            "The broader trend is up (or flat), which supports a bullish setup."
            if trend_agrees
            else "The broader trend is down, which works against a bullish setup."
        )
    elif direction == "bearish":
        trend_agrees = trend in ("downtrend", "sideways")
        trend_note = (
            "The broader trend is down (or flat), which supports a bearish setup."
            if trend_agrees
            else "The broader trend is up, which works against a bearish setup."
        )
    else:
        trend_agrees = None
        trend_note = "This pattern is neutral, so trend direction is not decisive."

    near_support = bool(row.get("near_support", False))
    near_resistance = bool(row.get("near_resistance", False))
    near_zone = "support" if near_support else ("resistance" if near_resistance else "none")

    vol_mean = df["volume"].rolling(20, min_periods=1).mean().loc[row.name]
    volume_above_avg = bool(row.get("volume", 0) > 1.5 * vol_mean)

    ez_low = row.get("entry_zone_low", pd.NA)
    ez_high = row.get("entry_zone_high", pd.NA)
    rr = row.get("risk_reward", pd.NA)
    if pd.isna(ez_low) or pd.isna(ez_high):
        entry_zone = None
    else:
        entry_zone = [round(float(ez_low), 2), round(float(ez_high), 2)]

    if pd.isna(rr):
        risk_reward = None
        risk_reward_ok = None
    else:
        risk_reward = round(float(rr), 1)
        risk_reward_ok = risk_reward >= 1.5
    risk_reward_note = (
        None
        if risk_reward_ok is None
        else f"risk/reward acceptable {'✓' if risk_reward_ok else '✗'} (1:{risk_reward})"
    )

    def _num(col):
        v = row.get(col, pd.NA)
        return None if pd.isna(v) else round(float(v), 2)

    pattern_direction = row.get("pattern_direction", direction)
    # Confidence status as a string (not a plain boolean) so bullish and
    # bearish can carry different labels: bullish is "provisional" (promising
    # but not yet statistically conclusive), bearish is "experimental"
    # (no demonstrated skill), and neutral patterns are null.
    if pattern_direction == "bullish":
        validated = "provisional"
    elif pattern_direction == "bearish":
        validated = "experimental"
    else:
        validated = None

    return {
        "date": str(row.name),
        "close": round(float(row["close"]), 2),
        "pattern": pattern,
        "pattern_direction": pattern_direction,
        "pattern_description": description,
        "chart_pattern": row.get("chart_pattern"),
        "chart_pattern_direction": row.get("chart_pattern_direction"),
        "trend": trend,
        "trend_agrees": trend_agrees,
        "trend_note": trend_note,
        "near_support": near_support,
        "near_resistance": near_resistance,
        "near_zone": near_zone,
        "support": _num("support"),
        "resistance": _num("resistance"),
        "volume_above_average": volume_above_avg,
        "score": round(float(row["score"]), 1) if "score" in df.columns else None,
        "entry_zone": entry_zone,
        "invalidation": _num("invalidation"),
        "target1": _num("target1"),
        "target2": _num("target2"),
        "risk_reward": risk_reward,
        "risk_reward_ok": risk_reward_ok,
        "risk_reward_note": risk_reward_note,
        "status_reason": row.get("status_reason", None),
        "status": row.get("status", None),
        "validated": validated,
        "confluence_score": row.get("confluence_score", None),
        "confluence_status": row.get("confluence_status", None),
        "confluence_direction": row.get("confluence_direction", None),
        "confluence_reasons": row.get("confluence_reasons", None) or [],
    }
