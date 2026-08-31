"""Chart pattern detection (multi-candle formations).

Reuses the swing_high / swing_low columns already computed by
indicator_engine.find_swing_points. Adds 'chart_pattern' and
'chart_pattern_direction' columns to the DataFrame.

This is intentionally a conservative v1: it does approximate price matching
(not pixel-perfect geometry) and only flags a candle when price is genuinely
*at* the relevant level (near the neckline for double tops/bottoms, near the
flat side for triangles). That keeps the signal sparse and meaningful
instead of firing on every pullback inside a trend.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_TOLERANCE = 0.015  # 1.5% price tolerance for "roughly equal" swing levels
_NEAR = 0.02        # 2% band: price must be near the key level to count as "live"
_MIN_SEP = 6        # min candles between the two peaks/troughs of a double pattern
_MIN_DEPTH = 0.03   # min valley/peak depth so a "double" is a real reversal, not chop


def _pct_diff(a: float, b: float) -> float:
    if a == 0 or b == 0:
        return 0.0
    return abs(a - b) / max(a, b)


def _swings_in_window(df: pd.DataFrame, i: int, lookback: int):
    """Return positional indices of swing highs and swing lows in [i-lookback, i]."""
    lo = max(0, i - lookback)
    sh = np.where(df["swing_high"].to_numpy()[lo : i + 1])[0] + lo
    sl = np.where(df["swing_low"].to_numpy()[lo : i + 1])[0] + lo
    return sh, sl


def _flat_ok(prices: np.ndarray, tol: float = _TOLERANCE) -> bool:
    """True if all swing prices lie within `tol` of each other (a real 'flat' side)."""
    if len(prices) < 2:
        return False
    return (prices.max() - prices.min()) / prices.mean() <= tol


def _rising(prices: np.ndarray) -> bool:
    return len(prices) >= 2 and bool(np.all(np.diff(prices) > 0))


def _falling(prices: np.ndarray) -> bool:
    return len(prices) >= 2 and bool(np.all(np.diff(prices) < 0))


def _detect_double_top(df: pd.DataFrame, i: int, lookback: int):
    sh, sl = _swings_in_window(df, i, lookback)
    if len(sh) < 2:
        return None, None

    peak_a, peak_b = sh[-2], sh[-1]  # earlier, later
    if peak_b - peak_a < _MIN_SEP:
        return None, None

    high_a = df["high"].iloc[peak_a]
    high_b = df["high"].iloc[peak_b]
    if _pct_diff(high_a, high_b) > _TOLERANCE:
        return None, None

    # Neckline = the lowest swing low strictly between the two peaks.
    between = [idx for idx in sl if peak_a < idx < peak_b]
    if not between:
        return None, None
    neckline = min(df["low"].iloc[idx] for idx in between)

    # The valley must be a meaningful pullback, not a tiny chop, otherwise the
    # "double top" is just noise inside a trend.
    if (high_b - neckline) / neckline < _MIN_DEPTH:
        return None, None

    close = df["close"].iloc[i]
    prev = df["close"].iloc[i - 1] if i > 0 else close
    # Only flag the breakdown or the approach into it (price was above the
    # neckline recently), so a steady downtrend does not re-flag every candle.
    broke = prev > neckline and close <= neckline
    approaching = prev > neckline * (1 + _NEAR) and (
        neckline * (1 - _NEAR) <= close <= neckline * (1 + _NEAR)
    )
    if broke or approaching:
        return "double_top", "bearish"
    return None, None


def _detect_double_bottom(df: pd.DataFrame, i: int, lookback: int):
    sh, sl = _swings_in_window(df, i, lookback)
    if len(sl) < 2:
        return None, None

    trough_a, trough_b = sl[-2], sl[-1]
    if trough_b - trough_a < _MIN_SEP:
        return None, None

    low_a = df["low"].iloc[trough_a]
    low_b = df["low"].iloc[trough_b]
    if _pct_diff(low_a, low_b) > _TOLERANCE:
        return None, None

    # Neckline = the highest swing high strictly between the two troughs.
    between = [idx for idx in sh if trough_a < idx < trough_b]
    if not between:
        return None, None
    neckline = max(df["high"].iloc[idx] for idx in between)

    # The peak must be a meaningful bounce, not a tiny chop.
    if (neckline - low_b) / low_b < _MIN_DEPTH:
        return None, None

    close = df["close"].iloc[i]
    prev = df["close"].iloc[i - 1] if i > 0 else close
    # Only flag the breakout or the approach into it (price was below the
    # neckline recently), so a steady uptrend does not re-flag every candle.
    broke = prev < neckline and close >= neckline
    approaching = prev < neckline * (1 - _NEAR) and (
        neckline * (1 - _NEAR) <= close <= neckline * (1 + _NEAR)
    )
    if broke or approaching:
        return "double_bottom", "bullish"
    return None, None


def _detect_ascending_triangle(df: pd.DataFrame, i: int, lookback: int):
    sh, sl = _swings_in_window(df, i, lookback)
    if len(sh) < 2 or len(sl) < 2:
        return None, None

    highs = df["high"].iloc[sh[-3:]].to_numpy(dtype=float)
    lows = df["low"].iloc[sl[-3:]].to_numpy(dtype=float)
    # Flat resistance (top) + rising swing lows beneath it.
    if not _flat_ok(highs):
        return None, None
    if not _rising(lows):
        return None, None

    resistance = float(highs.mean())
    close = df["close"].iloc[i]
    # Only live while price is testing the resistance level.
    if abs(close - resistance) / resistance <= _NEAR:
        return "ascending_triangle", "bullish"
    return None, None


def _detect_descending_triangle(df: pd.DataFrame, i: int, lookback: int):
    sh, sl = _swings_in_window(df, i, lookback)
    if len(sl) < 2 or len(sh) < 2:
        return None, None

    lows = df["low"].iloc[sl[-3:]].to_numpy(dtype=float)
    highs = df["high"].iloc[sh[-3:]].to_numpy(dtype=float)
    # Flat support (bottom) + falling swing highs above it.
    if not _flat_ok(lows):
        return None, None
    if not _falling(highs):
        return None, None

    support = float(lows.mean())
    close = df["close"].iloc[i]
    if abs(close - support) / support <= _NEAR:
        return "descending_triangle", "bearish"
    return None, None


def detect_chart_patterns(df: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    """Add 'chart_pattern' and 'chart_pattern_direction' columns.

    Scans a rolling window of the most recent `lookback` candles (reusing the
    already-computed swing_high / swing_low points) and detects double tops,
    double bottoms, ascending triangles and descending triangles. A row is
    labelled only when price is genuinely at the formation's key level.
    """
    df = df.copy()
    patterns = np.full(len(df), None, dtype=object)
    directions = np.full(len(df), None, dtype=object)

    checkers = (
        _detect_double_top,
        _detect_double_bottom,
        _detect_ascending_triangle,
        _detect_descending_triangle,
    )
    for i in range(len(df)):
        for checker in checkers:
            pattern, direction = checker(df, i, lookback)
            if pattern is not None:
                patterns[i] = pattern
                directions[i] = direction
                break

    df["chart_pattern"] = pd.Series(patterns, index=df.index)
    df["chart_pattern_direction"] = pd.Series(directions, index=df.index)
    return df
