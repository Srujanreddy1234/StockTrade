"""FastAPI backend wrapping the existing trading-assistant pipeline."""

from __future__ import annotations

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.data_engine.loader import generate_synthetic, load_from_yfinance
from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.indicators.indicator_engine import (
    add_indicators,
    find_swing_points,
    add_trend_read,
    add_support_resistance,
)
from backend.signals.scoring_engine import add_scores
from backend.risk.risk_engine import add_risk_levels
from backend.signals.explanation_engine import explain as explain_row
from backend.learning.content import list_topics, get_topic


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
    df = load_from_yfinance(ticker=ticker, period=period, interval=interval)
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
# Local dev only -- tighten before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full existing pipeline on a standard OHLCV DataFrame."""
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_indicators(df)
    df = find_swing_points(df, lookback=5)
    df = add_trend_read(df)
    df = add_support_resistance(df)
    df = add_scores(df)
    df = add_risk_levels(df)
    return df


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/analyze/{source}")
def analyze(
    source: str,
    n_candles: int = Query(600),
    interval: str = Query("15m"),
    ticker: str = Query("RELIANCE.NS"),
):
    # yfinance intraday intervals (m/h) are restricted to ~60 days of history;
    # daily or longer intervals can pull multiple years.
    if source == "synthetic":
        df = generate_synthetic(n_candles=n_candles, seed=42)
    elif source == "yfinance":
        if interval.endswith("m") or interval.endswith("h"):
            period = "60d"
        else:
            period = "2y"
        try:
            df = load_from_yfinance(ticker=ticker, period=period, interval=interval)
        except Exception as exc:  # yfinance not installed, network, or bad ticker
            from fastapi import HTTPException

            raise HTTPException(
                status_code=502,
                detail=(
                    f"Failed to load market data for '{ticker}' ({interval}). "
                    f"Ensure yfinance is installed (`pip install yfinance`) and "
                    f"the ticker is valid. Underlying error: {exc}"
                ),
            )
        if df.empty:
            from fastapi import HTTPException

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

    last_loc = df.index[-1]
    explanation = explain_row(df, last_loc)

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
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Topic '{topic_id}' not found.")
    return topic
