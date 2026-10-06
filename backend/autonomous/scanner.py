"""Scans a universe of tickers and ranks them by the same Step-15 confluence
engine the autonomous trader already trusts for entries, so the bot can pick
*which* few stocks out of many deserve a slot in the active watchlist
instead of being stuck watching a fixed, manually-curated list all day.

This runs on a slow clock (AutonomousConfig.scan_interval_seconds, default
30 minutes) and is deliberately independent of the fast per-tick loop in
trader.py -- it only decides WHICH tickers get a tick/pipeline slot, never
when to actually buy/sell one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from backend.autonomous.config import AutonomousConfig
from backend.data_engine.loader import load_from_yfinance
from backend.pipeline import run_pipeline

logger = logging.getLogger("autonomous.scanner")


@dataclass
class ScanResult:
    ticker: str
    confluence_score: float
    confluence_status: str
    confluence_direction: str | None
    last_price: float


def _isnan(value) -> bool:
    try:
        return value is None or value != value
    except Exception:
        return True


def _to_yfinance_ticker(ticker: str, exchange: str) -> str:
    if "." in ticker:
        return ticker
    suffix = {"NSE": ".NS", "BSE": ".BO"}.get(exchange.upper(), ".NS")
    return f"{ticker}{suffix}"


def _scan_one(ticker: str, config: AutonomousConfig) -> ScanResult | None:
    try:
        df = load_from_yfinance(
            ticker=_to_yfinance_ticker(ticker, config.exchange),
            period=config.candle_period,
            interval=config.candle_interval,
        )
        if df.empty:
            return None
        df = run_pipeline(df)
        last = df.iloc[-1]
        status = str(last.get("confluence_status") or "NO TRADE")
        direction = last.get("confluence_direction") or None
        score = float(last["confluence_score"]) if not _isnan(last.get("confluence_score")) else 0.0
        price = float(last["close"]) if not _isnan(last.get("close")) else 0.0
        return ScanResult(
            ticker=ticker,
            confluence_score=score,
            confluence_status=status,
            confluence_direction=direction,
            last_price=price,
        )
    except Exception:
        logger.exception("Scan failed for %s, skipping it this cycle", ticker)
        return None


def scan_universe(
    config: AutonomousConfig,
    universe: list[str] | None = None,
    top_n: int | None = None,
) -> list[ScanResult]:
    """Evaluate every ticker in `universe` (default config.universe) and
    return the top `top_n` (default config.scan_top_n) bullish ENTRY/WATCH
    candidates, ranked by confluence_score descending.

    Only bullish setups qualify -- this bot only ever opens long (BUY)
    intraday positions (see trader._maybe_enter), so a top-ranked bearish
    setup would never actually be tradeable here.
    """
    universe = universe if universe is not None else config.universe
    top_n = top_n if top_n is not None else config.scan_top_n

    results: list[ScanResult] = []
    for ticker in universe:
        result = _scan_one(ticker, config)
        if result is None:
            continue
        if result.confluence_direction != "bullish":
            continue
        if result.confluence_status not in ("ENTRY", "WATCH"):
            continue
        results.append(result)

    results.sort(key=lambda r: r.confluence_score, reverse=True)
    return results[:top_n]


def scan_universe_timed(config: AutonomousConfig) -> tuple[list[ScanResult], float]:
    start = time.time()
    results = scan_universe(config)
    return results, time.time() - start
