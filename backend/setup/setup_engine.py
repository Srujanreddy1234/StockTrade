"""Unified trading setup confluence engine (Step 15).

Every engine added in Steps 6-11 (market structure, support/resistance
zones, breakouts, pullback/reversal, volume, VWAP) computes real,
tested, look-ahead-free columns -- but until now nothing downstream
actually read them. The autonomous trader and the scoring/risk engines
only ever consulted the original 7-candle-pattern score and a single
nearest support/resistance level.

This module does not replace that scoring. It sits AFTER
scoring_engine.add_scores + risk_engine.add_risk_levels in the pipeline
and asks a narrower, more honest question: "independent of the base
pattern+trend score, do the newer structural engines agree with this
setup, or do they disagree?" It can only CONFIRM or DOWNGRADE the base
verdict -- it never invents an ENTRY the base scorer did not already
propose, so a bad base signal can't be rescued by borderline confluence.

This keeps the change additive and safe: existing 'status'/'direction'
columns are untouched, and any caller that ignores 'confluence_status'/
'confluence_direction' sees identical behavior to before this module
existed.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# How many recent candles count as "still relevant" corroboration for a
# momentary (non-persistent) event column such as breakout_event, which
# only fires on the candle it happens and is None afterwards. A genuine
# breakout confirming a fresh ENTRY signal a candle or two later is still
# meaningful; one from 20 candles ago is not.
EVENT_LOOKBACK = 5

_BULLISH_SR_CONFIRM = {"SUPPORT_REJECTION", "SUPPORT_RECLAIM", "RESISTANCE_BREAK", "TOUCH_SUPPORT"}
_BULLISH_SR_CONTRADICT = {"SUPPORT_BREAK", "RESISTANCE_REJECTION"}
_BEARISH_SR_CONFIRM = {"RESISTANCE_REJECTION", "RESISTANCE_RECLAIM", "SUPPORT_BREAK", "TOUCH_RESISTANCE"}
_BEARISH_SR_CONTRADICT = {"RESISTANCE_BREAK", "SUPPORT_REJECTION"}

_BULLISH_BREAKOUT_CONFIRM = {"CONFIRMED_BREAKOUT_BULLISH", "BREAKOUT_RETEST_SUCCESS_BULLISH"}
_BULLISH_BREAKOUT_CONTRADICT = {"FALSE_BREAKOUT_BULLISH", "CONFIRMED_BREAKOUT_BEARISH"}
_BEARISH_BREAKOUT_CONFIRM = {"CONFIRMED_BREAKOUT_BEARISH", "BREAKOUT_RETEST_SUCCESS_BEARISH"}
_BEARISH_BREAKOUT_CONTRADICT = {"FALSE_BREAKOUT_BEARISH", "CONFIRMED_BREAKOUT_BULLISH"}

_BULLISH_VWAP_CONFIRM = {"VWAP_RECLAIM"}
_BULLISH_VWAP_CONTRADICT = {"VWAP_LOSS", "VWAP_REJECTION_ABOVE"}
_BEARISH_VWAP_CONFIRM = {"VWAP_LOSS"}
_BEARISH_VWAP_CONTRADICT = {"VWAP_RECLAIM", "VWAP_REJECTION_BELOW"}


def _recent(df: pd.DataFrame, col: str, i: int, lookback: int) -> list:
    if col not in df.columns:
        return []
    start = max(0, i - lookback + 1)
    values = df[col].iloc[start : i + 1].tolist()
    return [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]


def _score_row(df: pd.DataFrame, i: int, lookback: int) -> dict[str, Any]:
    direction = df["direction"].iloc[i] if "direction" in df.columns else None
    status = df["status"].iloc[i] if "status" in df.columns else "NO TRADE"

    if direction not in ("bullish", "bearish") or status == "NO TRADE":
        return {
            "confluence_score": 0.0,
            "confluence_status": "NO TRADE",
            "confluence_direction": None,
            "confluence_reasons": [],
        }

    bullish = direction == "bullish"
    reasons: list[str] = []
    corroborations = 0
    contradictions = 0

    # 1. Market structure regime (persistent, same-row).
    structure_state = df["structure_state"].iloc[i] if "structure_state" in df.columns else None
    if structure_state == direction:
        corroborations += 1
        reasons.append(f"market structure regime is {direction}")
    elif structure_state in ("bullish", "bearish") and structure_state != direction:
        contradictions += 1
        reasons.append(f"market structure regime is {structure_state}, conflicting with this {direction} setup")

    # 2. Support/resistance zone interaction (momentary, recent window).
    sr_events = _recent(df, "sr_interaction", i, lookback)
    confirm_sr = _BULLISH_SR_CONFIRM if bullish else _BEARISH_SR_CONFIRM
    contradict_sr = _BULLISH_SR_CONTRADICT if bullish else _BEARISH_SR_CONTRADICT
    if any(e in confirm_sr for e in sr_events):
        corroborations += 1
        reasons.append("recent support/resistance zone behavior supports this direction")
    if any(e in contradict_sr for e in sr_events):
        contradictions += 1
        reasons.append("recent support/resistance zone behavior conflicts with this direction")

    # 3. Breakout/breakdown confirmation (momentary, recent window).
    breakout_events = _recent(df, "breakout_event", i, lookback)
    confirm_bo = _BULLISH_BREAKOUT_CONFIRM if bullish else _BEARISH_BREAKOUT_CONFIRM
    contradict_bo = _BULLISH_BREAKOUT_CONTRADICT if bullish else _BEARISH_BREAKOUT_CONTRADICT
    if any(e in confirm_bo for e in breakout_events):
        corroborations += 1
        reasons.append("a confirmed breakout/retest supports this direction")
    if any(e in contradict_bo for e in breakout_events):
        contradictions += 1
        reasons.append("a recent breakout failure or opposite breakout conflicts with this direction")

    # 4. Pullback continuation / reversal (momentary, recent window).
    pullback_events = _recent(df, "pullback_event", i, lookback)
    pb_confirm_name = f"PULLBACK_CONTINUATION_{'BULLISH' if bullish else 'BEARISH'}"
    pb_contradict_name = f"PULLBACK_CONTINUATION_{'BEARISH' if bullish else 'BULLISH'}"
    rev_confirm_name = f"POTENTIAL_REVERSAL_{'BULLISH' if bullish else 'BEARISH'}"
    rev_contradict_name = f"POTENTIAL_REVERSAL_{'BEARISH' if bullish else 'BULLISH'}"
    if pb_confirm_name in pullback_events:
        corroborations += 1
        reasons.append("price is pulling back and continuing in this direction")
    if pb_contradict_name in pullback_events:
        contradictions += 1
        reasons.append("a pullback continuation in the opposite direction conflicts with this setup")

    start = max(0, i - lookback + 1)
    window = df.iloc[start : i + 1]
    strong_reversal = False
    strong_opposite_reversal = False
    if "reversal_event" in df.columns and "reversal_confidence" in df.columns:
        for rev, conf in zip(window["reversal_event"], window["reversal_confidence"]):
            if rev == rev_confirm_name and conf in ("medium", "high"):
                strong_reversal = True
            if rev == rev_contradict_name and conf in ("medium", "high"):
                strong_opposite_reversal = True
    if strong_reversal:
        corroborations += 1
        reasons.append("a corroborated potential reversal supports this direction")
    if strong_opposite_reversal:
        contradictions += 1
        reasons.append("a corroborated potential reversal in the opposite direction conflicts with this setup")

    # 5. Volume confirmation on whichever event fired (momentary window).
    vol_confirms = _recent(df, "volume_confirms_event", i, lookback)
    if any(v is True for v in vol_confirms):
        corroborations += 1
        reasons.append("volume confirmed the recent move")

    # 6. VWAP interaction (momentary, recent window).
    vwap_events = _recent(df, "vwap_interaction", i, lookback)
    confirm_vwap = _BULLISH_VWAP_CONFIRM if bullish else _BEARISH_VWAP_CONFIRM
    contradict_vwap = _BULLISH_VWAP_CONTRADICT if bullish else _BEARISH_VWAP_CONTRADICT
    if any(e in confirm_vwap for e in vwap_events):
        corroborations += 1
        reasons.append("price action relative to VWAP supports this direction")
    if any(e in contradict_vwap for e in vwap_events):
        contradictions += 1
        reasons.append("price action relative to VWAP conflicts with this direction")

    score = max(0.0, min(100.0, 100.0 * (corroborations - contradictions) / 6.0))

    if contradictions >= 2:
        confluence_status = "NO TRADE"
    elif status == "ENTRY":
        if corroborations >= 2 and contradictions == 0:
            confluence_status = "ENTRY"
        else:
            confluence_status = "WATCH"
    elif status == "WATCH":
        confluence_status = "NO TRADE" if contradictions >= 1 else "WATCH"
    else:
        confluence_status = "NO TRADE"

    confluence_direction = direction if confluence_status != "NO TRADE" else None
    if not reasons:
        reasons = ["no independent confirmation yet from structure/zone/breakout/pullback/volume/VWAP engines"]

    return {
        "confluence_score": round(score, 1),
        "confluence_status": confluence_status,
        "confluence_direction": confluence_direction,
        "confluence_reasons": reasons,
    }


def add_setup_confluence(df: pd.DataFrame, lookback: int = EVENT_LOOKBACK) -> pd.DataFrame:
    """Add confluence_score/confluence_status/confluence_direction/confluence_reasons.

    Must run after scoring_engine.add_scores and risk_engine.add_risk_levels
    (needs 'status', 'direction') and after the Step 6-11 engines it reads.
    Purely additive -- does not modify 'status'/'direction'/'score'.
    """
    df = df.copy()
    n = len(df)
    scores = [None] * n
    statuses = [None] * n
    directions = [None] * n
    reasons_col: list[list[str]] = [None] * n

    for i in range(n):
        result = _score_row(df, i, lookback)
        scores[i] = result["confluence_score"]
        statuses[i] = result["confluence_status"]
        directions[i] = result["confluence_direction"]
        reasons_col[i] = result["confluence_reasons"]

    df["confluence_score"] = scores
    df["confluence_status"] = statuses
    df["confluence_direction"] = directions
    df["confluence_reasons"] = reasons_col
    return df


def get_setup_events(df: pd.DataFrame, timeframe: str = "unknown") -> list[dict[str, Any]]:
    """Flatten confluence_status transitions into explainable event dicts."""
    events: list[dict[str, Any]] = []
    last_status = None
    for idx, row in df.iterrows():
        status = row.get("confluence_status")
        if status is None or status == last_status:
            continue
        last_status = status
        events.append(
            {
                "category": "setup",
                "event": f"CONFLUENCE_{status.replace(' ', '_')}",
                "timestamp": str(idx),
                "timeframe": timeframe,
                "price": round(float(row["close"]), 2),
                "score": row.get("confluence_score"),
                "direction": row.get("confluence_direction"),
                "reasons": row.get("confluence_reasons") or [],
                "note": (
                    f"Confluence verdict changed to {status}"
                    + (f" ({row.get('confluence_direction')})" if row.get("confluence_direction") else "")
                    + f", score {row.get('confluence_score')}/100."
                ),
            }
        )
    return events
