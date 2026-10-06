"""yfinance-backed provider -- kept as the default/fallback data source.
Not removed per the task: Groww is additive, not a replacement, until its
data has been validated.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd

from backend.backtest.data_providers.base import DataProvider, FetchResult, standardize_frame
from backend.data_engine.loader import load_from_yfinance

# yfinance intraday intervals only serve this many days of history --
# requesting more just gets silently clamped by Yahoo, so there is no
# point asking for a bigger period than this.
_MAX_INTRADAY_DAYS = 60


def _to_yfinance_ticker(ticker: str, exchange: str) -> str:
    if "." in ticker:
        return ticker
    suffix = {"NSE": ".NS", "BSE": ".BO"}.get(exchange.upper(), ".NS")
    return f"{ticker}{suffix}"


def _interval_to_yfinance(interval: str) -> str:
    # Already matches yfinance's own spelling ("5m", "15m", "1d", ...).
    return interval


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def fetch(self, ticker: str, exchange: str, interval: str, start: datetime, end: datetime) -> FetchResult:
        days = max(1, math.ceil((end - start).total_seconds() / 86400))
        is_intraday = interval not in ("1d", "1wk", "1mo")
        if is_intraday:
            days = min(days, _MAX_INTRADAY_DAYS)
        df = load_from_yfinance(
            ticker=_to_yfinance_ticker(ticker, exchange), period=f"{days}d", interval=_interval_to_yfinance(interval)
        )
        df = standardize_frame(df)
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        df = df.loc[(df.index >= start_utc) & (df.index <= end_utc)]

        return FetchResult(
            df=df,
            requested_start=start,
            requested_end=end,
            available_start=df.index.min() if not df.empty else None,
            available_end=df.index.max() if not df.empty else None,
        )
