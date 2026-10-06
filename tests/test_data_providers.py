"""Tests for the historical-data provider abstraction
(backend/backtest/data_providers/): Groww pagination, dedup, OHLCV
validation, timezone handling, cache reuse/extension, retry/backoff,
auth-failure fast-fail, and provider switching. No real network call --
GrowwProvider.fetch() goes through a fake client injected via
monkeypatch.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from backend.backtest.data_providers import GrowwProvider, YFinanceProvider, get_provider
from backend.backtest.data_providers.base import validate_candle
from backend.backtest.data_providers.cache import CandleCache
from backend.backtest.data_providers.groww_provider import GrowwProviderError


class FakeApi:
    """Records every get_historical_candles call and returns one scripted
    response per call (in order), by default a synthetic 3-candle chunk
    spanning [start, end)."""

    def __init__(self, responses=None, raise_sequence=None):
        self.calls: list[dict] = []
        self.responses = responses  # list[list[row]] or None for auto-generated
        self.raise_sequence = list(raise_sequence or [])
        self._call_index = 0

    def get_historical_candles(self, exchange, segment, groww_symbol, start_time, end_time, candle_interval):
        self.calls.append({
            "exchange": exchange, "segment": segment, "groww_symbol": groww_symbol,
            "start_time": start_time, "end_time": end_time, "candle_interval": candle_interval,
        })
        if self.raise_sequence:
            exc = self.raise_sequence.pop(0)
            if exc is not None:
                raise exc

        if self.responses is not None:
            idx = self._call_index
            self._call_index += 1
            return {"candles": self.responses[idx] if idx < len(self.responses) else []}

        start = pd.Timestamp(start_time, tz="UTC")
        row = [int(start.timestamp()), 100.0, 101.0, 99.0, 100.5, 1000]
        return {"candles": [row]}


class FakeClient:
    def __init__(self, api: FakeApi):
        self._api_obj = api

    def _get_api(self):
        return self._api_obj


def _provider_with_fake(monkeypatch, api: FakeApi, tmp_path, delay=0.0) -> GrowwProvider:
    monkeypatch.setattr("backend.backtest.data_providers.groww_provider.get_client", lambda: FakeClient(api))
    monkeypatch.setattr("backend.backtest.data_providers.groww_provider.time.sleep", lambda s: None)
    cache = CandleCache(provider="groww-test", base_dir=str(tmp_path))
    return GrowwProvider(cache=cache, request_delay_seconds=delay)


# --- pagination ---

def test_paginates_requests_over_30_day_limit_for_5m(monkeypatch, tmp_path):
    api = FakeApi()
    provider = _provider_with_fake(monkeypatch, api, tmp_path)

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 7, tzinfo=timezone.utc)  # 65 days -> 3 chunks of <=30 days
    provider.fetch("RELIANCE", "NSE", "5m", start, end)

    assert len(api.calls) == 3
    assert api.calls[0]["candle_interval"] == "5minute"
    assert api.calls[0]["groww_symbol"] == "NSE-RELIANCE"


def test_15m_uses_90_day_chunks_not_30(monkeypatch, tmp_path):
    api = FakeApi()
    provider = _provider_with_fake(monkeypatch, api, tmp_path)

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 7, tzinfo=timezone.utc)  # 65 days -> fits in one 90-day chunk
    provider.fetch("RELIANCE", "NSE", "15m", start, end)

    assert len(api.calls) == 1


# --- dedup ---

def test_dedup_keeps_one_row_for_overlapping_boundary_timestamp(monkeypatch, tmp_path):
    shared_ts = int(pd.Timestamp("2026-01-15 09:15:00", tz="UTC").timestamp())
    chunk1 = [[shared_ts, 100.0, 101.0, 99.0, 100.5, 1000]]
    chunk2 = [[shared_ts, 999.0, 999.0, 999.0, 999.0, 1]]  # same ts, different values
    api = FakeApi(responses=[chunk1, chunk2])
    provider = _provider_with_fake(monkeypatch, api, tmp_path)

    # Force two chunks by requesting >30 days for a 5m interval.
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 5, tzinfo=timezone.utc)
    result = provider.fetch("RELIANCE", "NSE", "5m", start, end)

    matching = result.df[result.df.index == pd.Timestamp(shared_ts, unit="s", tz="UTC")]
    assert len(matching) == 1
    # "keep last" -- the second chunk's value wins.
    assert matching.iloc[0]["open"] == 999.0


# --- validation ---

def test_invalid_ohlc_candle_is_rejected_and_counted(monkeypatch, tmp_path):
    ts = int(pd.Timestamp("2026-01-15 09:15:00", tz="UTC").timestamp())
    bad_row = [ts, 100.0, 90.0, 99.0, 100.5, 1000]  # high < max(open, close)
    api = FakeApi(responses=[[bad_row]])
    provider = _provider_with_fake(monkeypatch, api, tmp_path)

    result = provider.fetch("RELIANCE", "NSE", "5m", datetime(2026, 1, 1, tzinfo=timezone.utc),
                             datetime(2026, 1, 20, tzinfo=timezone.utc))
    assert result.rejected_candles == 1
    assert result.df.empty


@pytest.mark.parametrize("o,h,l,c,v,expected", [
    (100, 105, 95, 102, 1000, True),
    (100, 95, 95, 102, 1000, False),   # high < close
    (100, 105, 101, 102, 1000, False),  # low > open
    (100, 105, 110, 102, 1000, False),  # high < low
    (100, 105, 95, 102, -1, False),     # negative volume
])
def test_validate_candle_cases(o, h, l, c, v, expected):
    assert validate_candle(o, h, l, c, v) is expected


# --- timezone ---

def test_epoch_seconds_and_ms_both_normalize_to_utc(monkeypatch, tmp_path):
    ts_seconds = int(pd.Timestamp("2026-01-15 09:15:00", tz="UTC").timestamp())
    ts_ms = ts_seconds * 1000 + 500_000  # ms-resolution timestamp, later bar
    chunk = [
        [ts_seconds, 100.0, 101.0, 99.0, 100.5, 1000],
        [ts_ms, 101.0, 102.0, 100.0, 101.5, 1000],
    ]
    api = FakeApi(responses=[chunk])
    provider = _provider_with_fake(monkeypatch, api, tmp_path)

    result = provider.fetch("RELIANCE", "NSE", "5m", datetime(2026, 1, 1, tzinfo=timezone.utc),
                             datetime(2026, 1, 20, tzinfo=timezone.utc))
    assert str(result.df.index.tz) == "UTC"
    assert len(result.df) == 2


# --- cache reuse / extension (partial download recovery) ---

def test_second_fetch_of_same_range_hits_cache_not_the_api(monkeypatch, tmp_path):
    api = FakeApi()
    provider = _provider_with_fake(monkeypatch, api, tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)

    provider.fetch("RELIANCE", "NSE", "5m", start, end)
    calls_after_first = len(api.calls)
    provider.fetch("RELIANCE", "NSE", "5m", start, end)
    assert len(api.calls) == calls_after_first  # no new calls -- fully cached


def test_extending_the_requested_range_only_fetches_the_new_part(monkeypatch, tmp_path):
    api = FakeApi()
    provider = _provider_with_fake(monkeypatch, api, tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    provider.fetch("RELIANCE", "NSE", "5m", start, end)
    calls_after_first = len(api.calls)

    later_end = datetime(2026, 1, 20, tzinfo=timezone.utc)
    provider.fetch("RELIANCE", "NSE", "5m", start, later_end)
    new_calls = api.calls[calls_after_first:]
    assert len(new_calls) == 1  # just the extension, not a re-fetch of [start, end]
    assert new_calls[0]["start_time"].startswith("2026-01-10")


# --- retry / backoff ---

def test_transient_error_is_retried_then_succeeds(monkeypatch, tmp_path):
    api = FakeApi(raise_sequence=[RuntimeError("timeout"), None])
    provider = _provider_with_fake(monkeypatch, api, tmp_path)
    result = provider.fetch("RELIANCE", "NSE", "5m", datetime(2026, 1, 1, tzinfo=timezone.utc),
                             datetime(2026, 1, 10, tzinfo=timezone.utc))
    assert len(api.calls) == 2  # first failed, second (retry) succeeded
    assert not result.df.empty


def test_auth_error_fails_fast_without_retry(monkeypatch, tmp_path):
    class GrowwAPIAuthenticationException(Exception):
        pass

    api = FakeApi(raise_sequence=[GrowwAPIAuthenticationException("bad token")])
    provider = _provider_with_fake(monkeypatch, api, tmp_path)
    with pytest.raises(GrowwProviderError):
        provider.fetch("RELIANCE", "NSE", "5m", datetime(2026, 1, 1, tzinfo=timezone.utc),
                        datetime(2026, 1, 10, tzinfo=timezone.utc))
    assert len(api.calls) == 1  # no retries for an auth failure


def test_exhausting_retries_raises_clear_error(monkeypatch, tmp_path):
    api = FakeApi(raise_sequence=[RuntimeError("x")] * 10)
    provider = _provider_with_fake(monkeypatch, api, tmp_path)
    with pytest.raises(GrowwProviderError):
        provider.fetch("RELIANCE", "NSE", "5m", datetime(2026, 1, 1, tzinfo=timezone.utc),
                        datetime(2026, 1, 10, tzinfo=timezone.utc))


# --- provider switching ---

def test_get_provider_returns_correct_types():
    assert isinstance(get_provider("groww"), GrowwProvider)
    assert isinstance(get_provider("yfinance"), YFinanceProvider)
    with pytest.raises(ValueError):
        get_provider("not-a-real-provider")
