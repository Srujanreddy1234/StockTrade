"""Data loading for the trading assistant.

Loads OHLCV data into a standard DataFrame (columns: open, high, low, close,
volume; DatetimeIndex).
"""

from __future__ import annotations

import pandas as pd
import numpy as np

_EXPECTED_COLS = ["open", "high", "low", "close", "volume"]


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame must have a DatetimeIndex")
    for col in _EXPECTED_COLS:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
    return df[_EXPECTED_COLS]


def load_from_yfinance(ticker: str, period: str = "1mo", interval: str = "1d"):
    """Load OHLCV data using yfinance. Requires network access."""
    import yfinance as yf

    raw = yf.download(
        ticker, period=period, interval=interval, auto_adjust=True, progress=False
    )
    raw = raw.dropna(how="any")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0].lower() for c in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]
    return _standardize(raw)


def load_from_csv(path: str) -> pd.DataFrame:
    """Load OHLCV data from a CSV file with a date column + OHLCV columns."""
    raw = pd.read_csv(path, parse_dates=True, index_col=0)
    return _standardize(raw)


def generate_synthetic(
    n_candles: int = 220,
    start_price: float = 100.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data with clearly visible swing points.

    Builds a price path from explicit trend legs (up and down) plus noise so
    the swing-based detectors have obvious higher-highs / higher-lows and
    lower-highs / lower-lows to find.
    """
    rng = np.random.default_rng(seed)

    # Each leg is a linear trend slope; we add a regular sine oscillation so
    # that clear swing highs/lows appear every ~cycle candles. The final leg is
    # a long uptrend so the tail exhibits higher highs + higher lows.
    legs = [
        (45, -0.35),  # downtrend
        (45, 0.00),   # sideways
        (45, -0.30),  # downtrend
        (130, 0.35),  # long uptrend -> tail ends clearly bullish
    ]
    cycle = 11.0  # candles per full oscillation -> swing every ~5-6 candles

    # Build a piecewise-linear trend baseline, then overlay the oscillation.
    trend = np.zeros(n_candles)
    pos = 0
    for length, slope in legs:
        seg = np.arange(length) * slope
        end = min(pos + length, n_candles)
        trend[pos:end] = trend[pos] + seg[: end - pos]
        pos = end
        if pos >= n_candles:
            break

    i = np.arange(n_candles)
    osc = 3.0 * np.sin(2 * np.pi * i / cycle)
    closes = start_price + trend + osc + rng.normal(0, 0.25, size=n_candles)
    closes = np.maximum(closes, 1.0)

    # Build OHLC around each close with intrabar range so wicks/swings exist.
    opens = np.empty_like(closes)
    opens[0] = closes[0] - rng.normal(0, 0.3)
    for i in range(1, len(closes)):
        opens[i] = closes[i - 1] + rng.normal(0, 0.2)

    noise = np.abs(rng.normal(0, 0.6, size=len(closes)))
    highs = np.maximum(opens, closes) + noise
    lows = np.minimum(opens, closes) - noise
    volume = rng.integers(1_000, 5_000, size=len(closes)).astype(float)

    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
        },
        index=idx,
    )
    return df
