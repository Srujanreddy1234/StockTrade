"""Technical indicators, trend reads, and support/resistance zones.

Replaces the naive EMA-slope trend read and rolling max/min
support/resistance with swing-point-based detection.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add ema, rsi, atr, vwap columns (implemented manually, no pandas_ta)."""
    df = df.copy()

    df["ema"] = df["close"].ewm(span=20, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50.0)

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    df["atr"] = df["atr"].fillna(df["atr"].median())

    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_pv = (typical * df["volume"]).cumsum()
    cum_v = df["volume"].cumsum()
    df["vwap"] = cum_pv / cum_v.replace(0, np.nan)

    return df


def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """Identify swing highs and swing lows.

    A candle is a swing high if its high is the highest in a window of
    `lookback` candles before and after it (inclusive). A swing low is the
    analog using the low. Adds boolean 'swing_high' and 'swing_low' columns.
    """
    df = df.copy()
    high = df["high"]
    low = df["low"]

    window = 2 * lookback + 1
    roll_high = high.rolling(window=window, center=True, min_periods=lookback + 1).max()
    roll_low = low.rolling(window=window, center=True, min_periods=lookback + 1).min()

    df["swing_high"] = (high >= roll_high) & high.notna()
    df["swing_low"] = (low <= roll_low) & low.notna()

    # Break ties in flat plateaus: keep only the first of consecutive equal
    # swing points so we get distinct pivots.
    sh = df["swing_high"].astype(bool)
    sl = df["swing_low"].astype(bool)
    prev_sh = sh.to_numpy().copy()
    prev_sh[1:] = prev_sh[:-1]
    prev_sh[0] = False
    prev_sl = sl.to_numpy().copy()
    prev_sl[1:] = prev_sl[:-1]
    prev_sl[0] = False
    df["swing_high"] = sh & ~pd.Series(prev_sh, index=df.index)
    df["swing_low"] = sl & ~pd.Series(prev_sl, index=df.index)

    # A centered window only "knows" a swing at index i once `lookback` future
    # candles exist. Record the index at which each swing becomes confirmed so
    # downstream trend/S&R logic can avoid look-ahead bias.
    pos = np.arange(len(df))
    df["swing_high_confirmed_at"] = np.where(df["swing_high"], pos + lookback, np.nan)
    df["swing_low_confirmed_at"] = np.where(df["swing_low"], pos + lookback, np.nan)
    return df


def add_trend_read(df: pd.DataFrame, window: int = 60, lookback: int = 5) -> pd.DataFrame:
    """Classify trend from actual swing structure.

    - uptrend   = recent swing highs rising AND recent swing lows rising
                  (higher highs + higher lows)
    - downtrend = recent swing highs falling AND recent swing lows falling
                  (lower highs + lower lows)
    - sideways  = anything else (mixed structure)

    Output column 'trend' keeps the same three string values.
    """
    df = df.copy()
    if "swing_high" not in df.columns or "swing_low" not in df.columns:
        df = find_swing_points(df, lookback=lookback)

    high_vals = df["high"].to_numpy()
    low_vals = df["low"].to_numpy()
    swing_high = df["swing_high"].to_numpy()
    swing_low = df["swing_low"].to_numpy()

    trends = np.full(len(df), "sideways", dtype=object)
    for i in range(len(df)):
        lo = max(0, i - window)
        # Only swings confirmed by (or before) this row may be used, i.e. the
        # swing's own index must be <= i - lookback. The first `lookback`
        # rows therefore cannot have confirmed swings -> 'sideways'.
        cut = i - lookback
        h_idx = np.where(
            swing_high[: i + 1] & (np.arange(i + 1) >= lo) & (np.arange(i + 1) <= cut)
        )[0]
        l_idx = np.where(
            swing_low[: i + 1] & (np.arange(i + 1) >= lo) & (np.arange(i + 1) <= cut)
        )[0]
        if len(h_idx) >= 2 and len(l_idx) >= 2:
            hh = high_vals[h_idx[-1]] > high_vals[h_idx[-2]]
            hl = low_vals[l_idx[-1]] > low_vals[l_idx[-2]]
            lh = high_vals[h_idx[-1]] < high_vals[h_idx[-2]]
            ll = low_vals[l_idx[-1]] < low_vals[l_idx[-2]]
            if hh and hl:
                trends[i] = "uptrend"
            elif lh and ll:
                trends[i] = "downtrend"

    df["trend"] = trends
    return df


def add_support_resistance(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """Build support/resistance ZONES from actual swing points.

    - support     = most recent significant swing low below current price
    - resistance  = most recent significant swing high above current price
    - near_support / near_resistance: within 1x ATR of current price.
    """
    df = df.copy()
    if "swing_high" not in df.columns or "swing_low" not in df.columns:
        df = find_swing_points(df, lookback=lookback)
    if "atr" not in df.columns or "ema" not in df.columns:
        df = add_indicators(df)

    high_vals = df["high"].to_numpy()
    low_vals = df["low"].to_numpy()
    close_vals = df["close"].to_numpy()
    atr_vals = df["atr"].to_numpy()
    swing_high = df["swing_high"].to_numpy()
    swing_low = df["swing_low"].to_numpy()

    supports = np.full(len(df), np.nan)
    resists = np.full(len(df), np.nan)
    near_s = np.zeros(len(df), dtype=bool)
    near_r = np.zeros(len(df), dtype=bool)

    for i in range(len(df)):
        price = close_vals[i]
        atr = atr_vals[i]
        # Only swings confirmed by this row (own index <= i - lookback) count.
        cut = i - lookback
        mask = np.arange(i + 1) <= cut
        l_below = np.where(swing_low[: i + 1] & mask & (low_vals[: i + 1] < price))[0]
        h_above = np.where(swing_high[: i + 1] & mask & (high_vals[: i + 1] > price))[0]

        if len(l_below):
            sup = low_vals[l_below].max()
            supports[i] = sup
            near_s[i] = (price - sup) <= atr
        if len(h_above):
            res = high_vals[h_above].min()
            resists[i] = res
            near_r[i] = (res - price) <= atr

    df["support"] = supports
    df["resistance"] = resists
    df["near_support"] = near_s
    df["near_resistance"] = near_r

    # EMA-based dynamic S/R -- a SECOND, independent source beyond swing
    # points. In an uptrend the EMA sits below price and acts as dynamic
    # support; in a downtrend it sits above price and acts as dynamic
    # resistance. We flag "near the EMA" when price is within 1x ATR of it,
    # on the relevant side. This is additive: it is OR-ed into the existing
    # swing-based near_support / near_resistance flags and does NOT alter the
    # numeric support / resistance ZONE columns used by risk_engine.
    ema_vals = df["ema"].to_numpy()
    ema_support = np.zeros(len(df), dtype=bool)
    ema_resist = np.zeros(len(df), dtype=bool)
    for i in range(len(df)):
        e = ema_vals[i]
        a = atr_vals[i]
        if np.isnan(e) or np.isnan(a):
            continue
        diff = close_vals[i] - e  # >0: price above EMA; <0: price below EMA
        if 0 <= diff <= a:
            ema_support[i] = True  # pulled back to EMA from above (uptrend support)
        if -a <= diff <= 0:
            ema_resist[i] = True  # bounced off EMA from below (downtrend resistance)

    df["near_ema_support"] = ema_support
    df["near_ema_resistance"] = ema_resist
    # Additive: near_support/resistance now true if swing-based OR EMA-based.
    df["near_support"] = near_s | ema_support
    df["near_resistance"] = near_r | ema_resist
    return df
