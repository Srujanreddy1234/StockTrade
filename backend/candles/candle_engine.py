"""Candle metrics and pattern detection."""

from __future__ import annotations

import pandas as pd
import numpy as np


def add_candle_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["body"] = (df["close"] - df["open"]).abs()
    df["range"] = df["high"] - df["low"]
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["is_bullish"] = df["close"] > df["open"]
    df["body_to_range"] = np.where(df["range"] > 0, df["body"] / df["range"], 0.0)
    return df


def detect_patterns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    range_mean = df["range"].rolling(20, min_periods=1).mean()
    df["is_doji"] = df["body_to_range"] < 0.1
    df["is_hammer"] = (
        (df["lower_wick"] > 2 * df["body"])
        & (df["upper_wick"] < df["body"])
        & (~df["is_bullish"])
    )
    df["is_shooting_star"] = (
        (df["upper_wick"] > 2 * df["body"])
        & (df["lower_wick"] < df["body"])
        & (df["is_bullish"])
    )

    # Engulfing patterns compare each candle to the previous one.
    prev_close = df["close"].shift(1)
    prev_open = df["open"].shift(1)
    bull_engulf = (
        (prev_close < prev_open)
        & (df["close"] > df["open"])
        & (df["close"] >= prev_open)
        & (df["open"] <= prev_close)
    )
    bear_engulf = (
        (prev_close > prev_open)
        & (df["close"] < df["open"])
        & (df["open"] >= prev_close)
        & (df["close"] <= prev_open)
    )

    # 3-candle star patterns look two candles back (shift(2)) and one back
    # (shift(1)) plus the current candle.
    o2, c2, body2, range2 = (
        df["open"].shift(2),
        df["close"].shift(2),
        df["body"].shift(2),
        df["range"].shift(2),
    )
    o1 = df["open"].shift(1)
    first_bearish = c2 < o2
    first_bullish = c2 > o2
    middle_small = body2 < 0.3 * range2
    closes_into_first = df["close"] > (o2 + c2) / 2
    closes_below_first = df["close"] < (o2 + c2) / 2

    morning_star = (
        first_bearish
        & middle_small
        & (o1 < c2)            # gap down / small overlap on the middle candle
        & (df["close"] > df["open"])
        & closes_into_first
    )
    evening_star = (
        first_bullish
        & middle_small
        & (o1 > c2)            # gap up / small overlap on the middle candle
        & (df["close"] < df["open"])
        & closes_below_first
    )

    pattern = pd.Series([None] * len(df), index=df.index, dtype=object)
    # Priority: 3-candle > 2-candle > single-candle. Assign higher-priority
    # patterns first and only fill rows that are still empty.
    pattern[morning_star] = "morning_star"
    pattern[evening_star] = "evening_star"
    pattern[bull_engulf & pattern.isna()] = "bullish_engulfing"
    pattern[bear_engulf & pattern.isna()] = "bearish_engulfing"
    pattern[df["is_doji"] & pattern.isna()] = "doji"
    pattern[df["is_hammer"] & pattern.isna()] = "hammer"
    pattern[df["is_shooting_star"] & pattern.isna()] = "shooting_star"
    df["pattern"] = pattern
    return df
