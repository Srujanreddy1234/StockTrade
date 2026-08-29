"""Combine patterns, trend, support/resistance and volume into a score."""

from __future__ import annotations

import pandas as pd
import numpy as np

from backend.signals.explanation_engine import explain as _explain_row


def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    direction = pd.Series(index=df.index, dtype=object)
    bullish_patterns = ["hammer", "bullish_engulfing", "morning_star"]
    bearish_patterns = ["shooting_star", "bearish_engulfing", "evening_star"]
    direction[df["pattern"].isin(bullish_patterns)] = "bullish"
    direction[df["pattern"].isin(bearish_patterns)] = "bearish"
    direction = direction.where(direction.notna(), None)
    df["direction"] = direction
    df["has_direction"] = direction.notna()

    pattern_weight = pd.Series(0.0, index=df.index)
    pattern_weight[df["pattern"] == "hammer"] = 15.0
    pattern_weight[df["pattern"] == "bullish_engulfing"] = 20.0
    pattern_weight[df["pattern"] == "shooting_star"] = 10.0
    pattern_weight[df["pattern"] == "bearish_engulfing"] = 20.0

    has_dir = direction.notna()
    no_dir = direction.isna()

    trend_weight = pd.Series(0.0, index=df.index)
    trend_weight[has_dir & (direction == "bullish") & (df["trend"] == "uptrend")] = 25.0
    trend_weight[has_dir & (direction == "bearish") & (df["trend"] == "downtrend")] = 25.0
    trend_weight[has_dir & (direction == "bullish") & (df["trend"] == "downtrend")] = -15.0
    trend_weight[has_dir & (direction == "bearish") & (df["trend"] == "uptrend")] = -15.0
    trend_weight[no_dir & (df["trend"] == "uptrend")] = 25.0
    trend_weight[no_dir & (df["trend"] == "downtrend")] = -25.0

    sr_weight = pd.Series(0.0, index=df.index)
    sr_weight[has_dir & (direction == "bullish") & df["near_support"]] = 20.0
    sr_weight[has_dir & (direction == "bearish") & df["near_resistance"]] = 20.0
    sr_weight[no_dir & df["near_support"] & (df["trend"] != "downtrend")] = 20.0
    sr_weight[no_dir & df["near_resistance"] & (df["trend"] != "uptrend")] = -15.0

    vol_mean = df["volume"].rolling(20, min_periods=1).mean()
    vol_weight = np.where(df["volume"] > 1.5 * vol_mean, 10.0, 0.0)

    score = pattern_weight + trend_weight + sr_weight + vol_weight
    score = score.clip(lower=0, upper=100)
    df["score"] = score.astype(float)

    status = pd.Series("NO TRADE", index=df.index)
    status[score >= 70] = "ENTRY"
    status[(score >= 40) & (score < 70)] = "WATCH"
    df["status"] = status

    return df


def explain(df: pd.DataFrame, loc) -> str:
    """Human-readable explanation for a single row (kept for backward compat).

    Delegates to the structured explanation engine and formats the result as a
    single string. `loc` may be a row label or an integer position.
    """
    info = _explain_row(df, loc)
    lines = [
        f"Setup on {info['date']} (close {info['close']}):",
        f"  Pattern : {info['pattern']} ({info['pattern_direction']})",
        f"           {info['pattern_description']}",
        f"  Trend   : {info['trend']} - agrees with pattern? {info['trend_agrees']}",
        f"           {info['trend_note']}",
        f"  Zone    : near {info['near_zone']} "
        f"(support {info['support']}, resistance {info['resistance']})",
        f"  Volume  : {'above average' if info['volume_above_average'] else 'below average'}",
        f"  Score   : {info['score']} -> {info['status']}",
    ]
    return "\n".join(lines)
