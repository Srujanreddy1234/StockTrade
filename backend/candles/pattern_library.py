"""Extended candlestick pattern detection.

This is additive to candle_engine.py, not a replacement: candle_engine's
`pattern` / `pattern_direction` columns stay exactly as they are (the
scoring engine and the frontend key off them), and this module adds a
second, richer layer alongside it.

Two things are deliberately NOT done here, on purpose, per the project's
"study the chart like a trader" direction:

1. Pattern detection alone does not produce a trade signal. A hammer at a
   random point in a range and a hammer at a tested support level are the
   same shape but very different evidence -- distinguishing them needs a
   market-structure/support-resistance context, which is the next stage
   (market structure engine). Until then, `context` on each pattern event
   is reported as "unscored" rather than invented.
2. `quality` here is a single, generic, documented proxy -- how large the
   candle is relative to its recent local range (rolling mean of `range`).
   A bigger, more decisive candle is generally a more significant pattern
   than a tiny one of the same shape. This is NOT a per-pattern "textbook
   perfection" score (e.g. exact hammer wick ratios); building 24 bespoke
   quality formulas without market-structure context to validate them
   against would be effort spent on a number nobody can act on yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

_DOJI_BODY_RATIO = 0.1
_SPINNING_TOP_MAX_BODY_RATIO = 0.3
_MARUBOZU_MIN_BODY_RATIO = 0.9
_LONG_WICK_MULT = 2.0
_TWEEZER_TOLERANCE = 0.001  # 0.1% of price


def add_extended_candle_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add metrics candle_engine doesn't compute: relative size and close
    position within the candle's own range. Requires candle_engine's
    add_candle_metrics() to have already run (needs body/range/wicks).
    """
    df = df.copy()
    avg_range = df["range"].rolling(20, min_periods=1).mean()
    df["relative_size"] = np.where(avg_range > 0, df["range"] / avg_range, 1.0)
    span = (df["high"] - df["low"]).replace(0, np.nan)
    df["close_position_in_range"] = ((df["close"] - df["low"]) / span).fillna(0.5)
    return df


def detect_extended_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Add boolean is_<pattern> columns for the full candlestick catalogue,
    plus a priority-ordered `extended_pattern` / `extended_pattern_direction`
    (3-candle > 2-candle > 1-candle, same convention as candle_engine's
    `pattern` column) and `patterns_detected` (every pattern true on that
    row, since more than one can legitimately fire at once).

    Requires: add_candle_metrics(), add_extended_candle_metrics(), and
    candle_engine.detect_patterns() to have already run (reuses is_doji,
    is_hammer, is_shooting_star, body/wick columns).
    """
    df = df.copy()
    body = df["body"]
    rng = df["range"]
    upper = df["upper_wick"]
    lower = df["lower_wick"]
    bull = df["is_bullish"]
    btr = df["body_to_range"]

    # --- Single-candle patterns not already in candle_engine ---
    df["is_spinning_top"] = (btr >= _DOJI_BODY_RATIO) & (btr < _SPINNING_TOP_MAX_BODY_RATIO) & (
        upper > body
    ) & (lower > body)
    df["is_marubozu"] = btr >= _MARUBOZU_MIN_BODY_RATIO
    df["is_dragonfly_doji"] = df["is_doji"] & (lower > _LONG_WICK_MULT * upper.clip(lower=1e-9))
    df["is_gravestone_doji"] = df["is_doji"] & (upper > _LONG_WICK_MULT * lower.clip(lower=1e-9))
    df["is_long_legged_doji"] = (
        df["is_doji"] & ~df["is_dragonfly_doji"] & ~df["is_gravestone_doji"]
        & (upper > body) & (lower > body)
    )
    # Same shape as candle_engine's hammer/shooting_star, opposite candle
    # color -- see module docstring convention note.
    df["is_inverted_hammer"] = (
        (upper > _LONG_WICK_MULT * body) & (lower < body) & (~bull)
    )
    df["is_hanging_man"] = (
        (lower > _LONG_WICK_MULT * body) & (upper < body) & bull
    )

    # --- Two-candle patterns ---
    prev_open = df["open"].shift(1)
    prev_close = df["close"].shift(1)
    prev_body_top = pd.concat([prev_open, prev_close], axis=1).max(axis=1)
    prev_body_bottom = pd.concat([prev_open, prev_close], axis=1).min(axis=1)
    cur_body_top = pd.concat([df["open"], df["close"]], axis=1).max(axis=1)
    cur_body_bottom = pd.concat([df["open"], df["close"]], axis=1).min(axis=1)
    prev_bearish = prev_close < prev_open
    prev_bullish = prev_close > prev_open
    contained = (cur_body_top <= prev_body_top) & (cur_body_bottom >= prev_body_bottom)
    smaller_body = body < body.shift(1)

    df["is_bullish_harami"] = prev_bearish & bull & contained & smaller_body
    df["is_bearish_harami"] = prev_bullish & (~bull) & contained & smaller_body

    prev_mid = (prev_open + prev_close) / 2
    df["is_piercing"] = (
        prev_bearish
        & bull
        & (df["open"] <= prev_close)
        & (df["close"] > prev_mid)
        & (df["close"] < prev_open)
    )
    df["is_dark_cloud_cover"] = (
        prev_bullish
        & (~bull)
        & (df["open"] >= prev_close)
        & (df["close"] < prev_mid)
        & (df["close"] > prev_open)
    )

    high_tol = _TWEEZER_TOLERANCE * df["close"]
    df["is_tweezer_top"] = (
        ((df["high"] - df["high"].shift(1)).abs() <= high_tol)
        & prev_bullish.fillna(False)
        & (~bull)
    )
    df["is_tweezer_bottom"] = (
        ((df["low"] - df["low"].shift(1)).abs() <= high_tol)
        & prev_bearish.fillna(False)
        & bull
    )

    # --- Three-candle patterns ---
    o2, c2, o1, c1 = df["open"].shift(2), df["close"].shift(2), df["open"].shift(1), df["close"].shift(1)
    strong_body = btr > 0.6

    three_bull = (c2 > o2) & (c1 > o1) & (df["close"] > df["open"])
    rising_closes = (c1 > c2) & (df["close"] > c1)
    df["is_three_white_soldiers"] = (
        three_bull & rising_closes & strong_body & (btr.shift(1) > 0.6) & (btr.shift(2) > 0.6)
    )

    three_bear = (c2 < o2) & (c1 < o1) & (df["close"] < df["open"])
    falling_closes = (c1 < c2) & (df["close"] < c1)
    df["is_three_black_crows"] = (
        three_bear & falling_closes & strong_body & (btr.shift(1) > 0.6) & (btr.shift(2) > 0.6)
    )

    first_bearish_3 = c2 < o2
    first_bullish_3 = c2 > o2
    bullish_harami_prev = df["is_bullish_harami"].shift(1).astype("boolean").fillna(False)
    bearish_harami_prev = df["is_bearish_harami"].shift(1).astype("boolean").fillna(False)
    df["is_three_inside_up"] = (
        first_bearish_3 & bullish_harami_prev & (df["close"] > o2)
    )
    df["is_three_inside_down"] = (
        first_bullish_3 & bearish_harami_prev & (df["close"] < o2)
    )

    _assign_priority_pattern(df)
    return df


# Priority order: 3-candle > 2-candle > 1-candle, most decisive first within
# each tier. Mirrors candle_engine's own priority convention.
_PRIORITY = [
    ("is_three_white_soldiers", "three_white_soldiers", "bullish"),
    ("is_three_black_crows", "three_black_crows", "bearish"),
    ("is_three_inside_up", "three_inside_up", "bullish"),
    ("is_three_inside_down", "three_inside_down", "bearish"),
    ("is_piercing", "piercing", "bullish"),
    ("is_dark_cloud_cover", "dark_cloud_cover", "bearish"),
    ("is_bullish_harami", "bullish_harami", "bullish"),
    ("is_bearish_harami", "bearish_harami", "bearish"),
    ("is_tweezer_bottom", "tweezer_bottom", "bullish"),
    ("is_tweezer_top", "tweezer_top", "bearish"),
    ("is_marubozu", "marubozu", None),  # direction resolved from is_bullish below
    ("is_dragonfly_doji", "dragonfly_doji", "bullish"),
    ("is_gravestone_doji", "gravestone_doji", "bearish"),
    ("is_long_legged_doji", "long_legged_doji", "neutral"),
    ("is_inverted_hammer", "inverted_hammer", "bullish"),
    ("is_hanging_man", "hanging_man", "bearish"),
    ("is_spinning_top", "spinning_top", "neutral"),
]


def _assign_priority_pattern(df: pd.DataFrame) -> None:
    name = pd.Series([None] * len(df), index=df.index, dtype=object)
    direction = pd.Series([None] * len(df), index=df.index, dtype=object)
    detected = [[] for _ in range(len(df))]

    for col, pattern_name, fixed_direction in _PRIORITY:
        mask = df[col].fillna(False)
        for pos in np.where(mask.to_numpy())[0]:
            detected[pos].append(pattern_name)
        fill = mask & name.isna()
        name[fill] = pattern_name
        if fixed_direction is None:
            direction[fill] = np.where(df.loc[fill, "is_bullish"], "bullish", "bearish")
        else:
            direction[fill] = fixed_direction

    df["extended_pattern"] = name
    df["extended_pattern_direction"] = direction
    df["patterns_detected"] = detected


@dataclass
class PatternEvent:
    name: str
    timestamp: str
    timeframe: str
    direction: str
    quality: float
    context: str
    confirmation: str  # "confirmed" | "unconfirmed" | "pending"
    invalidation_note: str


def _invalidation_note(direction: str, low: float, high: float) -> str:
    if direction == "bullish":
        return f"Invalidated if price closes back below {low:.2f}."
    if direction == "bearish":
        return f"Invalidated if price closes back above {high:.2f}."
    return "Neutral pattern -- no directional invalidation level."


def get_pattern_events(df: pd.DataFrame, timeframe: str = "unknown") -> list[dict[str, Any]]:
    """Flatten every detected pattern (both candle_engine's single `pattern`
    column and this module's `patterns_detected` list) into structured,
    explainable events.

    `quality` is the generic relative-size proxy described in the module
    docstring. `confirmation` is "pending" for the most recent row (the next
    candle hasn't happened yet to confirm or fail the pattern), "confirmed"
    if the next candle's close continued in the pattern's direction, and
    "unconfirmed" otherwise. `context` reports the prevailing trend/S-R
    state if those columns are present, "unscored" if not -- this module
    does not invent structural context it wasn't given.
    """
    events: list[dict[str, Any]] = []
    n = len(df)
    has_trend = "trend" in df.columns
    has_sr = "near_support" in df.columns and "near_resistance" in df.columns

    for i in range(n):
        row = df.iloc[i]
        names = list(row.get("patterns_detected") or [])
        base_pattern = row.get("pattern")
        if base_pattern and base_pattern not in names:
            names.append(base_pattern)
        if not names:
            continue

        base_direction = row.get("pattern_direction")
        ext_direction = row.get("extended_pattern_direction")

        for pattern_name in names:
            if pattern_name == base_pattern:
                direction = base_direction or "neutral"
            elif pattern_name == row.get("extended_pattern"):
                direction = ext_direction or "neutral"
            else:
                # A secondary pattern in patterns_detected that isn't the
                # priority-selected one; look up its fixed direction.
                direction = next(
                    (d if d is not None else ("bullish" if row["is_bullish"] else "bearish"))
                    for col, nm, d in _PRIORITY
                    if nm == pattern_name
                )

            if i + 1 < n:
                next_close = df.iloc[i + 1]["close"]
                if direction == "bullish":
                    confirmation = "confirmed" if next_close > row["close"] else "unconfirmed"
                elif direction == "bearish":
                    confirmation = "confirmed" if next_close < row["close"] else "unconfirmed"
                else:
                    confirmation = "unconfirmed"
            else:
                confirmation = "pending"

            context = "unscored"
            if has_trend:
                context = f"trend={row.get('trend')}"
            if has_sr:
                if row.get("near_support"):
                    context += ", near_support"
                elif row.get("near_resistance"):
                    context += ", near_resistance"

            events.append(
                {
                    "name": pattern_name,
                    "timestamp": str(row.name),
                    "timeframe": timeframe,
                    "direction": direction,
                    "quality": round(float(min(100.0, 50.0 * row.get("relative_size", 1.0))), 1),
                    "context": context,
                    "confirmation": confirmation,
                    "invalidation_note": _invalidation_note(direction, float(row["low"]), float(row["high"])),
                }
            )

    return events
