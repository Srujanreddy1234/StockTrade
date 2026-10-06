"""Common interface every historical-data provider implements, so the
backtest engine never has to know whether candles came from yfinance or
Groww (or anything added later).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

_EXPECTED_COLS = ["open", "high", "low", "close", "volume"]


class InvalidCandleError(Exception):
    """Raised when a candle fails OHLCV sanity validation."""


@dataclass
class FetchResult:
    df: pd.DataFrame  # OHLCV, DatetimeIndex, UTC-aware
    requested_start: datetime
    requested_end: datetime
    available_start: datetime | None
    available_end: datetime | None
    rejected_candles: int = 0


class DataProvider(ABC):
    """fetch() must return a DataFrame with columns
    [open, high, low, close, volume], a tz-aware UTC DatetimeIndex, no
    duplicate timestamps, and every row already passed validate_candle().
    """

    name: str = "base"

    @abstractmethod
    def fetch(self, ticker: str, exchange: str, interval: str, start: datetime, end: datetime) -> FetchResult:
        raise NotImplementedError


def validate_candle(o: float, h: float, l: float, c: float, v: float) -> bool:
    """OHLCV sanity checks shared by every provider (Task 1.3)."""
    try:
        if any(x is None for x in (o, h, l, c, v)):
            return False
        if any(pd.isna(x) for x in (o, h, l, c, v)):
            return False
    except TypeError:
        return False
    if h < l:
        return False
    if h < max(o, c):
        return False
    if l > min(o, c):
        return False
    if v < 0:
        return False
    return True


def standardize_frame(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame must have a DatetimeIndex")
    for col in _EXPECTED_COLS:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
    return df[_EXPECTED_COLS]
