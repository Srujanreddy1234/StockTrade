"""FastAPI backend wrapping the existing trading-assistant pipeline."""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
from fastapi import FastAPI, Query, HTTPException, Body
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from backend.data_engine.loader import generate_synthetic, load_from_yfinance_cached
from backend.signals.explanation_engine import explain as explain_row
from backend.learning.content import list_topics, get_topic
from backend.positions.position_store import position_store
from backend.pipeline import run_pipeline
from backend.multi_timeframe.mtf_engine import check_alignment
from backend.chart_patterns.chart_pattern_engine import detect_chart_patterns
from backend.db.engine import SessionLocal
from backend.db.init_db import init_db
from backend.db.repository import ScanHistoryRepository, BacktestRunRepository
from backend.groww.client import get_client
from backend.groww.auth import is_real_trading_enabled, GrowwAuthError

init_db()


def detect_currency(ticker: str) -> dict | None:
    """Map a yfinance ticker to its trading currency (code + symbol)."""
    t = (ticker or "").upper()
    if t.endswith(".NS") or t.endswith(".BO"):
        return {"code": "INR", "symbol": "₹"}
    if t.endswith(".L"):
        return {"code": "GBP", "symbol": "£"}
    if t.endswith(".T"):
        return {"code": "JPY", "symbol": "¥"}
    if t.endswith(".DE"):
        return {"code": "EUR", "symbol": "€"}
    if t.endswith(".TO") or t.endswith(".V"):
        return {"code": "CAD", "symbol": "C$"}
    if t.endswith(".HK"):
        return {"code": "HKD", "symbol": "HK$"}
    if t.endswith(".AX"):
        return {"code": "AUD", "symbol": "A$"}
    return {"code": "USD", "symbol": "$"}


# Curated Live-Scanner basket: a representative ~18-ticker subset of the full
# 40-ticker NSE basket. The full basket is too slow to run live on every
# request (each ticker hits yfinance + the full pipeline); 18 liquid,
# sector-diverse names keeps a single /scan call responsive while still
# surfacing a meaningful cross-section of setups.
SCAN_TICKERS = [
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "ITC.NS",
    "BHARTIARTL.NS",
    "LT.NS",
    "KOTAKBANK.NS",
    "WIPRO.NS",
    "MARUTI.NS",
    "NTPC.NS",
    "SUNPHARMA.NS",
    "BAJAJFINSV.NS",
    "HINDUNILVR.NS",
    "AXISBANK.NS",
    "TATASTEEL.NS",
]

# Default window for the scanner. Daily is enough to rank setups; a shorter
# window (2y) keeps the yfinance fetch quick.
SCAN_PERIOD = "2y"
SCAN_INTERVAL = "1d"


def analyze_ticker(ticker: str, interval: str = SCAN_INTERVAL, period: str = SCAN_PERIOD):
    """Run the full pipeline on one ticker and return (df, explanation).

    Single source of truth reused by both /analyze and /scan so the scanner
    does not duplicate pipeline logic.
    """
    df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    df = run_pipeline(df)
    last_loc = df.index[-1]
    return df, explain_row(df, last_loc)


def fetch_news(ticker: str, limit: int = 6) -> list[dict]:
    """Fetch recent news headlines for a ticker via yfinance.

    Best-effort: any failure (no yfinance, rate limit, network) returns [].
    Handles both the legacy flat structure and the newer nested ``content``
    structure returned by recent yfinance versions.
    """
    try:
        import yfinance as yf
        from datetime import datetime

        raw = getattr(yf.Ticker(ticker), "news", []) or []
        out = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            c = item.get("content", item)
            title = c.get("title") or item.get("title")
            summary = (
                c.get("summary") or c.get("description") or item.get("summary")
            )
            publisher = c.get("provider") or item.get("publisher")
            if isinstance(publisher, dict):
                publisher = publisher.get("displayName") or publisher.get("name")
            pub = c.get("pubDate") or item.get("providerPublishTime")
            link = None
            for key in ("canonicalUrl", "clickThroughUrl", "link"):
                v = c.get(key) or item.get(key)
                if isinstance(v, dict) and v.get("url"):
                    link = v["url"]
                    break
                if isinstance(v, str):
                    link = v
                    break
            published_ts = None
            if isinstance(pub, (int, float)):
                published_ts = int(pub)
            elif isinstance(pub, str):
                try:
                    published_ts = int(
                        datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
                    )
                except Exception:
                    published_ts = None
            out.append(
                {
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "publisher": publisher,
                    "published": published_ts,
                }
            )
        return out
    except Exception:
        return []


app = FastAPI(title="Stock Trading Assistant")

_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_allowed_raw = os.environ.get("BACKEND_CORS_ORIGINS")
if _allowed_raw and _allowed_raw.strip() not in ("*", ""):
    _allow_origins = [o.strip() for o in _allowed_raw.split(",") if o.strip()]
else:
    _allow_origins = _default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "").strip()
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 10
_rate_limit_store: dict[str, list[float]] = {}


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in ("/health", "/docs", "/redoc", "/openapi.json"):
            await self.app(scope, receive, send)
            return

        if _BACKEND_API_KEY:
            headers = dict(scope.get("headers", []))
            api_key = headers.get(b"x-api-key", b"").decode("utf-8", errors="ignore")
            if api_key != _BACKEND_API_KEY:
                from starlette.responses import JSONResponse
                await JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)(scope, receive, send)
                return

        await self.app(scope, receive, send)


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path not in ("/analyze", "/analyze/synthetic", "/scan"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if client else "unknown"
        now = __import__("time").time()
        window_start = now - _RATE_LIMIT_WINDOW

        timestamps = _rate_limit_store.get(ip, [])
        timestamps = [t for t in timestamps if t > window_start]

        if len(timestamps) >= _RATE_LIMIT_MAX:
            from starlette.responses import JSONResponse
            await JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)(scope, receive, send)
            return

        timestamps.append(now)
        _rate_limit_store[ip] = timestamps
        await self.app(scope, receive, send)


app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/analyze/{source}")
def analyze(
    source: str,
    n_candles: int = Query(600),
    interval: str = Query("1m"),
    ticker: str = Query("RELIANCE.NS"),
):
    # yfinance intraday intervals (m/h) are restricted to ~60 days of history;
    # daily or longer intervals can pull multiple years.
    # 1m data is typically limited to ~7 days.
    if source == "synthetic":
        df = generate_synthetic(n_candles=n_candles, seed=42)
    elif source == "yfinance":
        if interval == "1m":
            period = "7d"
        elif interval.endswith("m") or interval.endswith("h"):
            period = "60d"
        else:
            period = "2y"
        try:
            df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
        except Exception as exc:  # yfinance not installed, network, or bad ticker
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Failed to load market data for '{ticker}' ({interval}). "
                    f"Ensure yfinance is installed (`pip install yfinance`) and "
                    f"the ticker is valid. Underlying error: {exc}"
                ),
            )
        if df.empty:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No data returned for '{ticker}' ({interval}). "
                    f"The ticker may be invalid or have no history for this interval."
                ),
            )
    else:
        raise NotImplementedError(
            f"source '{source}' not supported yet (only 'synthetic', 'yfinance')"
        )

    df = run_pipeline(df)
    df = detect_chart_patterns(df)

    last_loc = df.index[-1]
    explanation = explain_row(df, last_loc)

    explanation["chart_pattern"] = df.at[last_loc, "chart_pattern"]
    explanation["chart_pattern_direction"] = df.at[last_loc, "chart_pattern_direction"]

    status = explanation.get("status")
    has_direction = explanation.get("pattern_direction") in ("bullish", "bearish")

    if status in ("ENTRY", "WATCH") and has_direction:
        explanation["active_setup"] = True
        explanation["monitoring"] = False
        if explanation.get("pattern_direction") == "bearish":
            explanation["validation_note"] = (
                "Bearish signals are still experimental -- backtesting across 40 stocks and 5 years "
                "showed no reliable directional accuracy for this signal type yet. "
                "Bullish signals have not yet been validated at the same scale."
            )
        elif explanation.get("pattern_direction") == "bullish":
            explanation["validation_note"] = (
                "Backtesting shows promising directional accuracy (48% on "
                "held-out data vs a 25% baseline), but sample size is still limited. "
                "Treat as provisional, not proven."
            )
    elif status in ("ENTRY", "WATCH") and not has_direction:
        # Scored into WATCH from trend + S/R alone, with no confirming pattern.
        explanation["active_setup"] = False
        explanation["monitoring"] = True
        explanation["message"] = (
            "price is near a key level in a matching trend, but no "
            "confirming candle pattern has formed yet"
        )
    else:
        explanation["active_setup"] = False
        explanation["monitoring"] = False

    history = [
        {
            "timestamp": str(idx),
            "close": round(float(row["close"]), 2),
            "status": row["status"],
            "score": round(float(row["score"]), 1),
            "direction": row["direction"],
        }
        for idx, row in df.tail(30).iterrows()
    ]

    # Build candle + indicator series for the frontend chart (last N candles).
    chart_n = 90
    chart_candles = []
    for idx, row in df.tail(chart_n).iterrows():
        ema = row.get("ema")
        vwap = row.get("vwap")
        rsi = row.get("rsi")
        chart_candles.append(
            {
                "timestamp": str(idx),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": round(float(row["volume"]), 2),
                "ema": None if pd.isna(ema) else round(float(ema), 2),
                "vwap": None if pd.isna(vwap) else round(float(vwap), 2),
                "rsi": None if pd.isna(rsi) else round(float(rsi), 1),
            }
        )

    levels = {
        "support": explanation.get("support"),
        "resistance": explanation.get("resistance"),
        "entry_zone_low": (explanation.get("entry_zone") or [None, None])[0],
        "entry_zone_high": (explanation.get("entry_zone") or [None, None])[1],
        "target1": explanation.get("target1"),
        "target2": explanation.get("target2"),
        "invalidation": explanation.get("invalidation"),
    }

    # Currency + news are only meaningful for real market data.
    currency = detect_currency(ticker) if source == "yfinance" else None
    news = fetch_news(ticker) if source == "yfinance" else []

    alignment_result = None
    if source == "yfinance":
        try:
            alignment_result = check_alignment(ticker)
        except Exception:
            alignment_result = None

    return {
        "source": source,
        "interval": interval,
        "n_candles": int(n_candles),
        "ticker": ticker,
        "currency": currency,
        "news": news,
        "explanation": explanation,
        "history": history,
        "chart": {
            "candles": chart_candles,
            "levels": levels,
        },
        "alignment": alignment_result,
    }


@app.get("/scan")
def scan():
    """Live scanner: run the full pipeline across the curated basket and rank
    by setup quality (score, descending).

    Returns a JSON list of compact per-ticker records (ticker, close, status,
    score, direction, validated, pattern, currency) plus a ``skipped`` list of
    tickers that failed to fetch, so the UI can show what was omitted.
    """
    results = []
    skipped = []

    for ticker in SCAN_TICKERS:
        try:
            _df, explanation = analyze_ticker(ticker)
            results.append(
                {
                    "ticker": ticker,
                    "close": explanation["close"],
                    "status": explanation["status"],
                    "score": explanation["score"],
                    "direction": explanation["pattern_direction"],
                    "validated": explanation["validated"],
                    "pattern": explanation["pattern"],
                    "currency": detect_currency(ticker),
                }
            )
        except Exception as exc:  # network/rate-limit/bad ticker -- skip gracefully
            skipped.append({"ticker": ticker, "error": str(exc)})

    results.sort(
        key=lambda r: (r["score"] if r["score"] is not None else -1), reverse=True
    )

    payload = {
        "scanned_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "results": results,
        "skipped": skipped,
    }
    try:
        db = __import__("backend.db.engine", fromlist=["SessionLocal"]).SessionLocal()
        ScanHistoryRepository(db).create(payload)
    except Exception:
        pass

    return {
        "count": len(results),
        "skipped": skipped,
        "results": results,
    }


@app.get("/learn")
def learn_topics():
    """Return a list of all available Learn topics (id, title, teaser)."""
    return {"topics": list_topics()}


@app.get("/learn/{topic_id}")
def learn_topic(topic_id: str):
    """Return the full educational content for a single topic."""
    topic = get_topic(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"Topic '{topic_id}' not found.")
    return topic


@app.get("/patterns/{ticker}")
def patterns(
    ticker: str,
    interval: str = Query("1d"),
    period: str = Query("2y"),
    limit: int = Query(50),
):
    """Return structured, explainable candlestick pattern events for a
    ticker -- both the single mutually-exclusive pattern candle_engine picks
    per row, and every secondary pattern detected by the extended pattern
    library. Each event carries a quality proxy, its trend/support-resistance
    context (where available), whether the next candle confirmed it, and a
    plain invalidation note. Most recent first.
    """
    from backend.candles.pattern_library import get_pattern_events

    try:
        df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}"
        )
    if df.empty:
        raise HTTPException(status_code=400, detail=f"No data returned for '{ticker}' ({interval}).")

    df = run_pipeline(df)
    events = get_pattern_events(df, timeframe=interval)
    events.reverse()
    return {"ticker": ticker, "interval": interval, "events": events[:limit]}


@app.get("/structure/{ticker}")
def structure(
    ticker: str,
    interval: str = Query("1d"),
    period: str = Query("2y"),
    limit: int = Query(50),
):
    """Return explicit market-structure events (Break of Structure / Change
    of Character / range breakout) for a ticker -- the moments price
    actually crossed a structural support/resistance level, and what that
    meant for the prevailing trend. Most recent first.
    """
    from backend.market_structure.structure_engine import get_structure_events

    try:
        df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}"
        )
    if df.empty:
        raise HTTPException(status_code=400, detail=f"No data returned for '{ticker}' ({interval}).")

    df = run_pipeline(df)
    events = get_structure_events(df, timeframe=interval)
    events.reverse()
    current_state = df["structure_state"].iloc[-1] if "structure_state" in df.columns else None
    return {"ticker": ticker, "interval": interval, "current_state": current_state, "events": events[:limit]}


@app.get("/support-resistance/{ticker}")
def support_resistance(
    ticker: str,
    interval: str = Query("1d"),
    period: str = Query("2y"),
):
    """Return multi-source support/resistance ZONES for a ticker: clustered
    swing highs/lows, previous day/week high-low, VWAP (only when genuinely
    interacted with, not just nearby), and EMA20, with a technical-evidence
    strength score (0-100 -- NOT a probability of a bounce or a profitable
    trade), touch count, state, and recent interaction events.
    """
    from backend.zones.zone_engine import get_support_resistance_snapshot

    try:
        df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}"
        )
    if df.empty:
        raise HTTPException(status_code=400, detail=f"No data returned for '{ticker}' ({interval}).")

    df = run_pipeline(df, timeframe=interval)
    return get_support_resistance_snapshot(df, ticker=ticker, timeframe=interval)


@app.get("/breakouts/{ticker}")
def breakouts(
    ticker: str,
    interval: str = Query("1d"),
    period: str = Query("2y"),
    limit: int = Query(50),
):
    """Return breakout/breakdown/retest events for a ticker: whether a zone
    break was ever confirmed (distance + volume + momentum evidence), or
    turned out to be a false breakout, and whether a subsequent retest of
    the broken zone held. Most recent first.
    """
    from backend.breakouts.breakout_engine import get_breakout_events

    try:
        df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}"
        )
    if df.empty:
        raise HTTPException(status_code=400, detail=f"No data returned for '{ticker}' ({interval}).")

    df = run_pipeline(df, timeframe=interval)
    events = get_breakout_events(df, timeframe=interval)
    events.reverse()
    return {"ticker": ticker, "interval": interval, "events": events[:limit]}


@app.get("/pullback-reversal/{ticker}")
def pullback_reversal(
    ticker: str,
    interval: str = Query("1d"),
    period: str = Query("2y"),
    limit: int = Query(50),
):
    """Return pullback and reversal events for a ticker. Pullback events
    distinguish a trend resuming after testing a zone (PULLBACK_CONTINUATION)
    from one that's getting stretched without yet breaking structure
    (PULLBACK_EXHAUSTION). Reversal events are always reported as
    POTENTIAL, never confirmed, with a confidence level based on how much
    corroborating evidence (candle color, pattern direction, momentum,
    a recent false breakout) exists alongside the underlying structure
    change. Most recent first.
    """
    from backend.pullback.pullback_engine import get_pullback_reversal_events

    try:
        df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}"
        )
    if df.empty:
        raise HTTPException(status_code=400, detail=f"No data returned for '{ticker}' ({interval}).")

    df = run_pipeline(df, timeframe=interval)
    events = get_pullback_reversal_events(df, timeframe=interval)
    events.reverse()
    return {"ticker": ticker, "interval": interval, "events": events[:limit]}


@app.get("/volume-vwap/{ticker}")
def volume_vwap(
    ticker: str,
    interval: str = Query("1d"),
    period: str = Query("2y"),
    limit: int = Query(50),
):
    """Return volume/VWAP intelligence for a ticker: current volume state
    (SPIKE/EXPANSION/NORMAL/CONTRACTION relative to its 20-period average),
    volume trend, and VWAP interaction events (reclaim/loss/rejection).
    Also reports the latest row's snapshot alongside the event history.
    """
    from backend.volume.volume_engine import get_volume_vwap_events

    try:
        df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}"
        )
    if df.empty:
        raise HTTPException(status_code=400, detail=f"No data returned for '{ticker}' ({interval}).")

    df = run_pipeline(df, timeframe=interval)
    events = get_volume_vwap_events(df, timeframe=interval)
    events.reverse()
    last = df.iloc[-1]
    return {
        "ticker": ticker,
        "interval": interval,
        "snapshot": {
            "volume_ratio": round(float(last["volume_ratio"]), 2) if not pd.isna(last.get("volume_ratio")) else None,
            "volume_state": last.get("volume_state"),
            "volume_trend": last.get("volume_trend"),
            "price_vs_vwap": last.get("price_vs_vwap"),
            "vwap_slope": last.get("vwap_slope"),
        },
        "events": events[:limit],
    }


@app.get("/setup/{ticker}")
def setup_confluence(
    ticker: str,
    interval: str = Query("1d"),
    period: str = Query("2y"),
    limit: int = Query(50),
):
    """Return the unified setup confluence verdict for a ticker: whether the
    base pattern+trend+risk/reward setup (status/direction) is independently
    corroborated by market structure, support/resistance zones, breakouts,
    pullback/reversal, volume and VWAP. This is the same verdict the
    autonomous trader uses to gate real entries -- confluence can only
    confirm or downgrade the base setup, never invent one.
    """
    from backend.setup.setup_engine import get_setup_events

    try:
        df = load_from_yfinance_cached(ticker=ticker, period=period, interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}"
        )
    if df.empty:
        raise HTTPException(status_code=400, detail=f"No data returned for '{ticker}' ({interval}).")

    df = run_pipeline(df, timeframe=interval)
    events = get_setup_events(df, timeframe=interval)
    events.reverse()
    last = df.iloc[-1]
    return {
        "ticker": ticker,
        "interval": interval,
        "snapshot": {
            "status": last.get("status"),
            "direction": last.get("direction"),
            "confluence_status": last.get("confluence_status"),
            "confluence_direction": last.get("confluence_direction"),
            "confluence_score": round(float(last["confluence_score"]), 1)
            if not pd.isna(last.get("confluence_score"))
            else None,
            "confluence_reasons": last.get("confluence_reasons") or [],
        },
        "events": events[:limit],
    }


@app.get("/alignment/{ticker}")
def alignment(ticker: str):
    """Return multi-timeframe trend alignment for a ticker."""
    try:
        result = check_alignment(ticker)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to compute alignment for '{ticker}': {exc}",
        )
    return result


@app.get("/backtests")
def list_backtests(limit: int = Query(50)):
    """Return recent backtest runs."""
    db = SessionLocal()
    try:
        return {"runs": BacktestRunRepository(db).list_recent(limit=limit)}
    finally:
        db.close()


@app.get("/scan-history")
def list_scan_history(limit: int = Query(50)):
    """Return recent scan history snapshots."""
    db = SessionLocal()
    try:
        return {"scans": ScanHistoryRepository(db).list_recent(limit=limit)}
    finally:
        db.close()


def _fetch_current_price(ticker: str) -> float:
    """Return the latest available price for a ticker using yfinance."""
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="1d")
        if hist.empty:
            raise ValueError(f"No price data returned for '{ticker}'.")
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch current price for '{ticker}': {exc}",
        )


def _evaluate_position(position: dict[str, Any], current_price: float) -> dict[str, Any]:
    """Re-evaluate an open position against current price.

    Returns the position dict (possibly mutated in-place by the store).
    """
    if position["status"] != "open":
        return position

    position_store.update_unrealized(position["id"], current_price)

    if position["direction"] == "bullish":
        if current_price >= position["target1"]:
            return position_store.mark_closed_by_system(
                position["id"], current_price, "target_hit"
            )
        if current_price <= position["invalidation"]:
            return position_store.mark_closed_by_system(
                position["id"], current_price, "invalidated"
            )
    else:
        if current_price <= position["target1"]:
            return position_store.mark_closed_by_system(
                position["id"], current_price, "target_hit"
            )
        if current_price >= position["invalidation"]:
            return position_store.mark_closed_by_system(
                position["id"], current_price, "invalidated"
            )

    return position


@app.post("/positions/open")
def open_position(body: dict[str, str]):
    """Open a new paper position for the most recent analysis row.

    Body must contain ``ticker`` and ``interval``. The pipeline is run
    against the most recent data; the position is only created if the
    latest explanation has status ENTRY or WATCH **and** a real
    bullish/bearish direction.
    """
    ticker = body.get("ticker")
    interval = body.get("interval", "1d")
    if not ticker:
        raise HTTPException(status_code=400, detail="Field 'ticker' is required.")

    try:
        df = load_from_yfinance_cached(ticker=ticker, period="2y", interval=interval)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load market data for '{ticker}' ({interval}): {exc}",
        )

    if df.empty:
        raise HTTPException(
            status_code=400,
            detail=f"No data returned for '{ticker}' ({interval}).",
        )

    df = run_pipeline(df)
    last_loc = df.index[-1]
    explanation = explain_row(df, last_loc)

    status = explanation.get("status")
    direction = explanation.get("pattern_direction")
    entry_zone = explanation.get("entry_zone")
    target1 = explanation.get("target1")
    invalidation = explanation.get("invalidation")
    close = explanation.get("close")

    if status not in ("ENTRY", "WATCH") or direction not in ("bullish", "bearish"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot open position: latest status is '{status}' with direction "
                f"'{direction}'. Positions can only be opened on ENTRY or WATCH "
                f"setups with a confirmed bullish or bearish direction."
            ),
        )

    if not entry_zone or target1 is None or invalidation is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot open position: missing entry zone, target, or invalidation "
                "level in the latest explanation."
            ),
        )

    entry_price = round((entry_zone[0] + entry_zone[1]) / 2, 2)

    position = position_store.create(
        {
            "ticker": ticker,
            "interval": interval,
            "direction": direction,
            "entry_price": entry_price,
            "target1": target1,
            "invalidation": invalidation,
        }
    )
    return position


@app.get("/positions")
def list_positions():
    """List all positions with live evaluation for open ones."""
    positions = position_store.list_all()
    out = []
    for pos in positions:
        current = pos.get("unrealized_return_pct") or 0.0
        exit_return = pos.get("return_pct")
        display_return = exit_return if pos["status"] == "closed" else current

        if pos["status"] == "open":
            try:
                current_price = _fetch_current_price(pos["ticker"])
                pos = _evaluate_position(pos, current_price)
                display_return = pos.get("unrealized_return_pct") or 0.0
            except Exception:
                pass

        out.append(
            {
                "id": pos["id"],
                "ticker": pos["ticker"],
                "interval": pos["interval"],
                "direction": pos["direction"],
                "entry_price": pos["entry_price"],
                "target1": pos["target1"],
                "invalidation": pos["invalidation"],
                "entry_date": pos["entry_date"],
                "status": pos["status"],
                "exit_price": pos.get("exit_price"),
                "exit_date": pos.get("exit_date"),
                "exit_reason": pos.get("exit_reason"),
                "return_pct": pos.get("return_pct"),
                "unrealized_return_pct": pos.get("unrealized_return_pct") or 0.0,
                "display_return_pct": display_return,
            }
        )
    return {"positions": out}


@app.post("/positions/{position_id}/close")
def close_position(position_id: str):
    """Manually close an open position at the current market price."""
    position = position_store.get(position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found.")
    if position["status"] != "open":
        raise HTTPException(
            status_code=400,
            detail=f"Position is already {position['status']}.",
        )

    try:
        current_price = _fetch_current_price(position["ticker"])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch current price for '{position['ticker']}': {exc}",
        )

    closed = position_store.close(position_id, current_price, "manual_close")
    return closed


@app.get("/groww/status")
def groww_status():
    """Return Groww connection status and whether real trading is enabled."""
    try:
        client = get_client()
        api = client._get_api()
        api.get_user_profile()
        connected = True
    except Exception as exc:
        connected = False
        detail = str(exc)
    return {
        "connected": connected,
        "real_trading_enabled": is_real_trading_enabled(),
    }


@app.get("/groww/holdings")
def groww_holdings():
    """Fetch current holdings from Groww."""
    client = get_client()
    try:
        holdings = client.get_holdings()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"holdings": holdings}


@app.get("/groww/positions")
def groww_positions():
    """Fetch current positions from Groww."""
    client = get_client()
    try:
        positions = client.get_positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"positions": positions}


@app.get("/groww/margin")
def groww_margin():
    """Fetch margin details from Groww."""
    client = get_client()
    try:
        margin = client.get_margin()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return margin


@app.get("/groww/orders")
def groww_orders():
    """Fetch order history from Groww."""
    client = get_client()
    try:
        orders = client.get_orders()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"orders": orders}


class GrowwOrderRequest(BaseModel):
    trading_symbol: str
    exchange: str = "NSE"
    segment: str = "EQ"
    product: str = "CNC"
    order_type: str = "LIMIT"
    transaction_type: str = "BUY"
    quantity: int
    price: Optional[float] = None
    trigger_price: Optional[float] = None


@app.post("/groww/orders")
def groww_place_order(payload: GrowwOrderRequest):
    """Place an order via Groww. Only works if GROWW_ALLOW_REAL_ORDERS=true."""
    if not is_real_trading_enabled():
        raise HTTPException(status_code=403, detail="Real trading is disabled. Set GROWW_ALLOW_REAL_ORDERS=true to enable.")
    client = get_client()
    try:
        result = client.place_order(payload.dict())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Order failed: {exc}") from exc
    return result


@app.post("/groww/orders/{order_id}/cancel")
def groww_cancel_order(order_id: str):
    """Cancel an order via Groww."""
    client = get_client()
    try:
        result = client.cancel_order(order_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cancel failed: {exc}") from exc
    return result


@app.get("/autonomous/status")
def autonomous_status():
    """Report the autonomous loop's configuration and current risk state.

    This endpoint only reads state -- it does not start or stop the loop.
    The loop itself must be run as a separate process
    (``python -m backend.autonomous.trader``) since it runs continuously and
    should not live inside a request/response cycle.
    """
    from backend.autonomous.config import load_config
    from backend.autonomous.risk_manager import RiskManager
    from backend.groww.auth import is_real_trading_enabled

    config = load_config()
    risk = RiskManager(config)
    return {
        "mode": "live" if is_real_trading_enabled() else "paper",
        "watchlist": config.watchlist,
        "tick_interval_seconds": config.tick_interval_seconds,
        "pipeline_refresh_seconds": config.pipeline_refresh_seconds,
        "buy_probability_threshold": config.buy_probability_threshold,
        "sell_probability_threshold": config.sell_probability_threshold,
        "risk_limits": {
            "max_capital_per_trade_pct": config.max_capital_per_trade_pct,
            "max_capital_per_trade_abs": config.max_capital_per_trade_abs,
            "daily_loss_limit_pct": config.daily_loss_limit_pct,
            "max_open_positions": config.max_open_positions,
            "cooldown_minutes": config.cooldown_minutes,
        },
        "risk_state": {
            "date": risk.state.date,
            "day_start_margin": risk.state.day_start_margin,
            "realized_pnl_today": risk.state.realized_pnl_today,
            "kill_switch_active": risk.state.kill_switch_active,
            "kill_switch_reason": risk.state.kill_switch_reason,
        },
        "open_autonomous_positions": position_store.count_open(source="autonomous"),
    }


@app.post("/autonomous/kill-switch/reset")
def autonomous_reset_kill_switch():
    """Manually clear the daily-loss kill switch so the loop can resume
    opening new positions. Existing open positions are unaffected either way.
    """
    from backend.autonomous.config import load_config
    from backend.autonomous.risk_manager import RiskManager

    risk = RiskManager(load_config())
    risk.reset_kill_switch()
    return {"kill_switch_active": risk.state.kill_switch_active}


@app.get("/autonomous/events")
def autonomous_events(limit: int = Query(100), ticker: Optional[str] = Query(None)):
    """Return the autonomous loop's decision audit trail, most recent first."""
    from backend.db.repository import AutonomousEventRepository

    db = SessionLocal()
    try:
        return {"events": AutonomousEventRepository(db).list_recent(limit=limit, ticker=ticker)}
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn

    # Most hosts (Render, Railway, Fly) inject the listen port via $PORT.
    port = int(os.environ.get("PORT", 8000))
    # Run as an object (not the "backend.main:app" string) so this works when
    # launched with `python -m backend.main` from the repo root.
    uvicorn.run(app, host="0.0.0.0", port=port)
