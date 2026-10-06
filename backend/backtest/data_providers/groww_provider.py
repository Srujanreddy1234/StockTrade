"""Groww-backed historical data provider.

Pulls multi-year 5m/15m NSE candle history from Groww's dedicated
backtesting `get_historical_candles` API (documented back to 2020 for
CASH/FNO, with a per-request duration cap that varies by interval -- see
_MAX_DAYS_PER_REQUEST). This module turns that into a single
`fetch(ticker, exchange, interval, start, end)` call: it paginates
automatically, deduplicates at chunk boundaries, validates every OHLCV
candle, retries transient failures with backoff, throttles requests, and
caches everything to disk so a second run doesn't re-download anything
already fetched (see backend/backtest/data_providers/cache.py).

Does NOT replace yfinance -- this is additive (see data_providers/__init__.py
get_provider()), and the live trading data source is untouched; this is
backtesting-only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.backtest.data_providers.base import DataProvider, FetchResult, validate_candle
from backend.backtest.data_providers.cache import CandleCache
from backend.groww.client import get_client

logger = logging.getLogger("backtest.data_providers.groww")

# Per Groww's backtesting API docs: max days per single get_historical_candles
# request, by candle interval.
_MAX_DAYS_PER_REQUEST = {
    "1minute": 30, "2minute": 30, "3minute": 30, "5minute": 30,
    "10minute": 90, "15minute": 90, "30minute": 90,
    "1hour": 180, "4hour": 180, "1day": 180, "1week": 180, "1month": 180,
}

# Our internal interval spelling ("5m") -> Groww's candle_interval spelling.
_INTERVAL_MAP = {
    "1m": "1minute", "2m": "2minute", "3m": "3minute", "5m": "5minute",
    "10m": "10minute", "15m": "15minute", "30m": "30minute",
    "1h": "1hour", "4h": "4hour", "1d": "1day", "1wk": "1week", "1mo": "1month",
}


class GrowwProviderError(Exception):
    pass


@dataclass
class _RetryConfig:
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 20.0


class GrowwProvider(DataProvider):
    name = "groww"

    def __init__(
        self,
        cache: CandleCache | None = None,
        request_delay_seconds: float = 0.3,
        retry: _RetryConfig | None = None,
    ) -> None:
        self.cache = cache or CandleCache(provider="groww")
        self.request_delay_seconds = request_delay_seconds
        self.retry = retry or _RetryConfig()

    def _groww_symbol(self, ticker: str, exchange: str) -> str:
        # Equities/indices use the plain "EXCHANGE-TRADINGSYMBOL" format
        # (verified against a live instrument lookup -- RELIANCE on NSE
        # resolves to "NSE-RELIANCE"). Derivatives use a different,
        # expiry/strike-encoded format this provider does not build.
        return f"{exchange.upper()}-{ticker.upper()}"

    def _fetch_chunk(self, groww_symbol: str, segment: str, start: datetime, end: datetime, groww_interval: str) -> list[dict]:
        client = get_client()
        api = client._get_api()
        attempt = 0
        delay = self.retry.base_delay_seconds
        while True:
            attempt += 1
            try:
                response = api.get_historical_candles(
                    exchange="NSE" if "NSE" in groww_symbol else "BSE",
                    segment=segment,
                    groww_symbol=groww_symbol,
                    start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                    candle_interval=groww_interval,
                )
                return response.get("candles", []) if isinstance(response, dict) else (response or [])
            except Exception as exc:
                # Auth/authorization failures are not transient -- retrying
                # will never succeed, so fail fast with a clear message
                # instead of burning the retry budget.
                exc_name = type(exc).__name__
                if "Auth" in exc_name:
                    raise GrowwProviderError(
                        f"Groww historical-candles call failed with an auth/permission error "
                        f"({exc_name}: {exc}). This is not a transient failure -- the configured "
                        f"API credentials likely lack historical-data/market-data access. Retrying "
                        f"will not help; this needs to be fixed on Groww's developer portal."
                    ) from exc
                if attempt > self.retry.max_retries:
                    raise GrowwProviderError(
                        f"Groww historical-candles request failed after {attempt} attempts: {exc}"
                    ) from exc
                logger.warning(
                    "Groww historical-candles request failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt, self.retry.max_retries, exc, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, self.retry.max_delay_seconds)

    def _parse_candle_row(self, row) -> tuple[pd.Timestamp, float, float, float, float, float] | None:
        """Groww's V2 response represents each candle as a list:
        [timestamp, open, high, low, close, volume] (epoch seconds or ms
        for the timestamp, per the SDK's documented V2 format). Handles
        both a list-shaped row and a dict-shaped row defensively, since
        the exact shape is sparsely documented.
        """
        try:
            if isinstance(row, (list, tuple)):
                ts, o, h, l, c, v = row[:6]
            else:
                ts = row.get("timestamp") or row.get("start_time") or row.get("ts")
                o, h, l, c, v = row["open"], row["high"], row["low"], row["close"], row.get("volume", 0)
            if isinstance(ts, (int, float)):
                # Heuristic: epoch ms vs epoch seconds.
                ts = ts / 1000 if ts > 10_000_000_000 else ts
                timestamp = pd.Timestamp(ts, unit="s", tz="UTC")
            else:
                timestamp = pd.Timestamp(ts)
                timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
            return timestamp, float(o), float(h), float(l), float(c), float(v)
        except Exception:
            logger.exception("Could not parse Groww candle row: %r", row)
            return None

    def _chunks(self, start: datetime, end: datetime, max_days: int) -> list[tuple[datetime, datetime]]:
        chunks = []
        cursor = start
        step = timedelta(days=max_days)
        while cursor < end:
            chunk_end = min(cursor + step, end)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end
        return chunks

    def fetch(self, ticker: str, exchange: str, interval: str, start: datetime, end: datetime) -> FetchResult:
        groww_interval = _INTERVAL_MAP.get(interval)
        if groww_interval is None:
            raise ValueError(f"Unsupported interval for GrowwProvider: {interval}")
        max_days = _MAX_DAYS_PER_REQUEST[groww_interval]

        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

        cached_df, missing_ranges = self.cache.plan_fetch(ticker, interval, start, end)

        rejected_total = 0
        new_rows: list[dict] = []
        groww_symbol = self._groww_symbol(ticker, exchange)
        segment = "CASH"

        for range_start, range_end in missing_ranges:
            for chunk_start, chunk_end in self._chunks(range_start, range_end, max_days):
                raw_rows = self._fetch_chunk(groww_symbol, segment, chunk_start, chunk_end, groww_interval)
                time.sleep(self.request_delay_seconds)  # throttle, don't hammer the API
                for row in raw_rows:
                    parsed = self._parse_candle_row(row)
                    if parsed is None:
                        rejected_total += 1
                        continue
                    ts, o, h, l, c, v = parsed
                    if not validate_candle(o, h, l, c, v):
                        rejected_total += 1
                        continue
                    new_rows.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})

        new_df = pd.DataFrame(new_rows).set_index("timestamp") if new_rows else pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )
        merged = self.cache.merge_and_save(
            ticker, interval, cached_df, new_df, provider=self.name,
            requested_start=start, requested_end=end,
        )

        windowed = merged.loc[(merged.index >= start) & (merged.index <= end)]
        return FetchResult(
            df=windowed,
            requested_start=start,
            requested_end=end,
            available_start=merged.index.min() if not merged.empty else None,
            available_end=merged.index.max() if not merged.empty else None,
            rejected_candles=rejected_total,
        )
