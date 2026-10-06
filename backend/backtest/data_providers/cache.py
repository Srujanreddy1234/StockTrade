"""Local on-disk cache for historical candles, so the backtester doesn't
re-download years of data on every run.

Layout:
    data_cache/<provider>/<interval>/<ticker>.csv   -- the candles
    data_cache/<provider>/manifest.json              -- what's cached, per Task 1.9

The coverage model is intentionally simple: each ticker+interval tracks
ONE contiguous covered range [start, end]. Requesting a range that
extends before/after it downloads only the extension and unions the
range; a genuinely disjoint request (e.g. asking for 2019 data after only
ever having fetched 2024) is treated as two missing pieces the first time,
and the covered range becomes their union afterward -- this does not
model multiple disjoint cached islands, which is a known simplification,
not a bug: this project always grows coverage outward from whatever was
fetched first, never requests a scattered set of unrelated windows.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass
class _ManifestEntry:
    ticker: str
    interval: str
    start: str
    end: str
    candles: int
    downloaded_at: str
    provider: str
    data_hash: str


class CandleCache:
    def __init__(self, provider: str, base_dir: str = "data_cache") -> None:
        self.provider = provider
        self.root = os.path.join(base_dir, provider)
        os.makedirs(self.root, exist_ok=True)
        self.manifest_path = os.path.join(self.root, "manifest.json")

    # --- manifest ---

    def _load_manifest(self) -> dict:
        if not os.path.exists(self.manifest_path):
            return {}
        try:
            with open(self.manifest_path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_manifest(self, manifest: dict) -> None:
        tmp = f"{self.manifest_path}.tmp"
        with open(tmp, "w") as f:
            json.dump(manifest, f, indent=2, default=str)
        os.replace(tmp, self.manifest_path)

    @staticmethod
    def _key(ticker: str, interval: str) -> str:
        return f"{ticker}:{interval}"

    # --- file paths ---

    def _csv_path(self, ticker: str, interval: str) -> str:
        directory = os.path.join(self.root, interval)
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{ticker}.csv")

    def _load_cached_df(self, ticker: str, interval: str) -> pd.DataFrame:
        path = self._csv_path(ticker, interval)
        if not os.path.exists(path):
            return pd.DataFrame(columns=_COLUMNS)
        df = pd.read_csv(path, index_col=0, parse_dates=[0])
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        df.index.name = "timestamp"
        return df[_COLUMNS]

    # --- public API used by GrowwProvider ---

    def plan_fetch(
        self, ticker: str, interval: str, start: datetime, end: datetime
    ) -> tuple[pd.DataFrame, list[tuple[datetime, datetime]]]:
        """Returns (already-cached df, list of (start, end) ranges still
        needing a real fetch to satisfy the requested [start, end])."""
        manifest = self._load_manifest()
        entry = manifest.get(self._key(ticker, interval))
        cached_df = self._load_cached_df(ticker, interval)

        if entry is None:
            return cached_df, [(start, end)]

        covered_start = pd.Timestamp(entry["start"]).to_pydatetime()
        covered_end = pd.Timestamp(entry["end"]).to_pydatetime()

        missing: list[tuple[datetime, datetime]] = []
        if start < covered_start:
            missing.append((start, min(covered_start, end)))
        if end > covered_end:
            missing.append((max(covered_end, start), end))
        return cached_df, missing

    def merge_and_save(
        self,
        ticker: str,
        interval: str,
        cached_df: pd.DataFrame,
        new_df: pd.DataFrame,
        provider: str,
        requested_start: datetime | None = None,
        requested_end: datetime | None = None,
    ) -> pd.DataFrame:
        """Dedup (ticker+timestamp -- the index IS the timestamp for a
        single ticker's own file) by keeping the newest fetch's value for
        any overlapping bar, sort, save to CSV, and update the manifest's
        covered range.

        The manifest's covered range is tracked by REQUESTED dates, not by
        the actual min/max candle timestamp returned -- a request for
        [Jan 1, Jan 10] that only returns candles starting Jan 2 (because
        Jan 1 was a market holiday) must still record Jan 1 as covered, or
        the next identical request would look like it has a "gap" at the
        start and re-fetch something that's genuinely just missing data,
        not missing data WE haven't tried for yet.
        """
        if new_df.empty and cached_df.empty and requested_start is None:
            return cached_df
        non_empty = [df for df in (cached_df, new_df) if not df.empty]
        combined = pd.concat(non_empty) if non_empty else cached_df
        if not combined.empty:
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()

        path = self._csv_path(ticker, interval)
        combined.to_csv(path)

        manifest = self._load_manifest()
        key = self._key(ticker, interval)
        existing = manifest.get(key)
        candidates_start = [pd.Timestamp(requested_start)] if requested_start is not None else []
        candidates_end = [pd.Timestamp(requested_end)] if requested_end is not None else []
        if existing:
            candidates_start.append(pd.Timestamp(existing["start"]))
            candidates_end.append(pd.Timestamp(existing["end"]))
        covered_start = min(candidates_start) if candidates_start else combined.index.min()
        covered_end = max(candidates_end) if candidates_end else combined.index.max()

        data_hash = hashlib.sha256(combined.to_csv().encode()).hexdigest()[:16]
        manifest[key] = {
            "ticker": ticker,
            "interval": interval,
            "start": covered_start.isoformat(),
            "end": covered_end.isoformat(),
            "candles": int(len(combined)),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "data_hash": data_hash,
        }
        self._save_manifest(manifest)
        return combined

    def manifest_entry(self, ticker: str, interval: str) -> dict | None:
        return self._load_manifest().get(self._key(ticker, interval))
