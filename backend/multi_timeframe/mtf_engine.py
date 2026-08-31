"""Multi-timeframe alignment analysis.

Runs the existing pipeline on daily, 1-hour, and 15-minute timeframes for
the same ticker and classifies how the trends across timeframes align.
"""

from __future__ import annotations

from typing import Any

from backend.pipeline import load_and_run


_TIMEFRAMES = [
    {"interval": "1d", "period": "2y", "label": "daily"},
    {"interval": "1h", "period": "60d", "label": "hourly"},
    {"interval": "15m", "period": "60d", "label": "m15"},
]


def _classify(daily: str | None, hourly: str | None, m15: str | None) -> dict[str, Any]:
    trends = {"daily": daily, "hourly": hourly, "m15": m15}

    if any(v is None for v in trends.values()):
        return {
            "alignment": None,
            "note": "One or more timeframes could not be evaluated.",
            "trends": trends,
        }

    up = "uptrend"
    down = "downtrend"
    side = "sideways"

    d, h, m = daily, hourly, m15

    if d == up and h == up and m == up:
        return {
            "alignment": "strong_bullish",
            "note": "All three timeframes agree: uptrend.",
            "trends": trends,
        }
    if d == down and h == down and m == down:
        return {
            "alignment": "strong_bearish",
            "note": "All three timeframes agree: downtrend.",
            "trends": trends,
        }
    if d == up and h == up and m != up:
        return {
            "alignment": "aligned_bullish",
            "note": "Daily and hourly are bullish, but the 15-minute chart shows weakness against the higher timeframe trend.",
            "trends": trends,
        }
    if d == down and h == down and m != down:
        return {
            "alignment": "aligned_bearish",
            "note": "Daily and hourly are bearish, but the 15-minute chart shows strength against the higher timeframe trend.",
            "trends": trends,
        }
    if d == h and d != m:
        outlier = "15-minute"
        outlier_trend = m
        agree = d
        agree_label = "Daily and hourly"
    elif d == m and d != h:
        outlier = "1-hour"
        outlier_trend = h
        agree = d
        agree_label = "Daily and 15-minute"
    elif h == m and h != d:
        outlier = "Daily"
        outlier_trend = d
        agree = h
        agree_label = "Hourly and 15-minute"
    else:
        return {
            "alignment": "mixed",
            "note": f"Daily is {d}, hourly is {h}, and 15-minute is {m} -- no clear alignment across timeframes.",
            "trends": trends,
        }
    return {
        "alignment": "conflicting",
        "note": (
            f"{agree_label} are {agree}, but the {outlier} chart is {outlier_trend} -- "
            f"the {outlier} view is out of step with the other two timeframes."
        ),
        "trends": trends,
    }


def check_alignment(ticker: str) -> dict[str, Any]:
    """Run the pipeline on three timeframes and return alignment info."""
    trends: dict[str, str | None] = {}
    failures: list[str] = []

    for tf in _TIMEFRAMES:
        try:
            df = load_and_run(ticker=ticker, interval=tf["interval"], period=tf["period"])
            if df.empty:
                failures.append(tf["label"])
                trends[tf["label"]] = None
                continue
            last_loc = df.index[-1]
            row = df.loc[last_loc]
            trends[tf["label"]] = row.get("trend", "sideways")
        except Exception:
            failures.append(tf["label"])
            trends[tf["label"]] = None

    classification = _classify(
        trends.get("daily"), trends.get("hourly"), trends.get("m15")
    )

    result = {
        "ticker": ticker,
        "daily_trend": trends.get("daily"),
        "hourly_trend": trends.get("hourly"),
        "m15_trend": trends.get("m15"),
        "alignment": classification["alignment"],
        "note": classification["note"],
    }

    if failures:
        result["alignment"] = None
        result["note"] = f"Could not compute alignment: {', '.join(failures)} data unavailable."

    return result
