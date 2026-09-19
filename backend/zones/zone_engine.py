"""Multi-source support/resistance ZONE engine.

Evolves the existing single-price support/resistance columns (computed in
backend/indicators/indicator_engine.add_support_resistance) into clustered
zones with strength, sources, touch counts, and state -- without touching
or duplicating that existing calculation. `support`/`resistance` stay
exactly as they are; this module reads swing points, EMA, VWAP, and ATR
that already exist and adds a second, additive layer on top.

LOOK-AHEAD SAFETY (read this before changing anything below):
Every source level gathered for row i must have been KNOWABLE using only
data at or before row i:
  - Swing highs/lows are only used once confirmed, i.e. once
    `swing_position + lookback <= i` -- identical to the confirmation rule
    indicator_engine.add_support_resistance already uses. A swing at
    position j is invisible to every row i < j + lookback.
  - Previous-day / previous-week high-low (PDH/PDL/PWH/PWL) are the
    high/low of the immediately preceding COMPLETED calendar day/week --
    never the current, still-forming one. See add_reference_levels().
  - EMA/VWAP/ATR at row i are already point-in-time by construction in
    indicator_engine (they're causal rolling/ewm calculations).
  - Consolidation bands use a backward-looking (non-centered) rolling
    window, so the band at row i never includes row i+1 or later.
  - Touch counts for a zone only count candles strictly BEFORE row i.
This is what makes the engine backtest-safe: replaying historical rows one
at a time produces the exact same zones a live run would have seen.

ZONE STRENGTH is a technical-evidence score (0-100), NOT a probability of
a bounce, a reversal, or a profitable trade. It measures how much
independent, non-duplicated evidence corroborates the zone existing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# --- Clustering tolerance: volatility-aware, not a fixed rupee amount. ---
# A level cluster's tolerance is the LARGER of an ATR-based distance and a
# percentage-of-price distance, so it scales sensibly for both a Rs 100
# stock and a Rs 5000 stock, and widens naturally in more volatile stretches.
ATR_TOLERANCE_MULT = 1.5
PCT_TOLERANCE = 0.005  # 0.5% of price

# --- Strength weights, keyed by SOURCE TYPE (not raw level count), so e.g.
# three nearby swing highs count once as "swing_high" evidence rather than
# tripling the score. Weights are a documented, hand-set heuristic ranking
# of how much independent evidence each source type represents. ---
_SOURCE_WEIGHTS = {
    "swing_high": 25,
    "swing_low": 25,
    "pdh": 15,
    "pdl": 15,
    "pwh": 10,
    "pwl": 10,
    "vwap": 15,
    "ema20": 10,
    "consolidation": 15,
    "breakout_retest": 10,
}
_TOUCH_BONUS_PER_TOUCH = 5
_TOUCH_BONUS_CAP = 25
_STALE_AFTER_CANDLES = 40
_STALE_DECAY = 0.6
_SWING_LOOKBACK_WINDOW = 60  # how far back to gather swing points from, matches chart_pattern_engine's convention
_NEAR_PCT = 0.01  # within 1% counts as "approaching" a zone
_VWAP_INTERACTION_WINDOW = 20  # candles to look back for a genuine VWAP crossing before trusting it as a source


@dataclass
class Zone:
    type: str  # "support" | "resistance"
    lower: float
    upper: float
    mid: float
    strength: float
    timeframe: str
    sources: list[str]
    touches: int
    state: str  # ACTIVE | TESTED | REJECTED | BROKEN | RECLAIMED | FLIPPED | STALE
    recency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "lower": round(self.lower, 2),
            "upper": round(self.upper, 2),
            "mid": round(self.mid, 2),
            "strength": round(self.strength, 1),
            "timeframe": self.timeframe,
            "sources": self.sources,
            "touches": self.touches,
            "state": self.state,
            "recency": self.recency,
        }


def add_reference_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Add pdh/pdl/pwh/pwl: the previous COMPLETED day's/week's high/low.

    Valid for every row of the current day/week -- never the still-forming
    one. For daily-interval data this reduces to simply the prior row's
    high/low, which is correct (each row already is one full day).
    """
    df = df.copy()
    dates = pd.Series(df.index.date, index=df.index)
    daily_high = df.groupby(dates)["high"].max()
    daily_low = df.groupby(dates)["low"].min()
    ordered_dates = sorted(daily_high.index)
    prev_daily_high = {d: daily_high[ordered_dates[i - 1]] for i, d in enumerate(ordered_dates) if i > 0}
    prev_daily_low = {d: daily_low[ordered_dates[i - 1]] for i, d in enumerate(ordered_dates) if i > 0}
    df["pdh"] = dates.map(prev_daily_high).to_numpy(dtype=float)
    df["pdl"] = dates.map(prev_daily_low).to_numpy(dtype=float)

    weeks = pd.Series(
        [f"{y}-W{w}" for y, w, _ in (d.isocalendar() for d in df.index)], index=df.index
    )
    weekly_high = df.groupby(weeks)["high"].max()
    weekly_low = df.groupby(weeks)["low"].min()
    ordered_weeks = sorted(weekly_high.index)
    prev_weekly_high = {wk: weekly_high[ordered_weeks[i - 1]] for i, wk in enumerate(ordered_weeks) if i > 0}
    prev_weekly_low = {wk: weekly_low[ordered_weeks[i - 1]] for i, wk in enumerate(ordered_weeks) if i > 0}
    df["pwh"] = weeks.map(prev_weekly_high).to_numpy(dtype=float)
    df["pwl"] = weeks.map(prev_weekly_low).to_numpy(dtype=float)
    return df


def _cluster_levels(levels: list[tuple[float, str]], tolerance: float) -> list[tuple[float, float, list[str]]]:
    """Greedy merge of nearby levels into zones.

    A naive "merge if adjacent gap <= tolerance" chain-merge is unstable: in
    a dense set of levels (common once swings + PDH/PDL + PWH/PWL + EMA +
    VWAP are all in play) it can chain A-B-C-D... into one zone spanning
    many multiples of the tolerance, even though A and D are nowhere near
    each other. To prevent that, a level only joins the current cluster if
    it is BOTH within `tolerance` of the cluster's most recent member AND
    within `2 * tolerance` of the cluster's first (lowest) member -- capping
    how wide any single zone can grow regardless of how densely packed the
    levels are.
    """
    if not levels:
        return []
    max_width = tolerance * 2
    levels = sorted(levels, key=lambda x: x[0])
    clusters: list[list[tuple[float, str]]] = [[levels[0]]]
    for value, source in levels[1:]:
        cluster_min = clusters[-1][0][0]
        gap_to_last = value - clusters[-1][-1][0]
        if gap_to_last <= tolerance and (value - cluster_min) <= max_width:
            clusters[-1].append((value, source))
        else:
            clusters.append([(value, source)])
    result = []
    for cluster in clusters:
        values = [v for v, _ in cluster]
        sources = list(dict.fromkeys(s for _, s in cluster))  # dedup, preserve order
        result.append((min(values), max(values), sources))
    return result


def _zone_strength(sources: list[str], touches: int, candles_since_touch: int | None) -> float:
    score = sum(_SOURCE_WEIGHTS.get(s, 5) for s in sources)
    score += min(_TOUCH_BONUS_CAP, touches * _TOUCH_BONUS_PER_TOUCH)
    if candles_since_touch is not None and candles_since_touch > _STALE_AFTER_CANDLES:
        score *= _STALE_DECAY
    return float(min(100.0, max(0.0, score)))


def _count_touches(df: pd.DataFrame, up_to: int, lower: float, upper: float) -> tuple[int, int | None]:
    """Count prior candles (strictly before `up_to`) whose range overlapped
    [lower, upper]. Returns (touch_count, candles_since_last_touch).
    """
    if up_to == 0:
        return 0, None
    highs = df["high"].to_numpy()[:up_to]
    lows = df["low"].to_numpy()[:up_to]
    overlap = (highs >= lower) & (lows <= upper)
    count = int(overlap.sum())
    if count == 0:
        return 0, None
    last_idx = int(np.where(overlap)[0][-1])
    return count, up_to - 1 - last_idx


def add_zone_columns(df: pd.DataFrame, timeframe: str = "unknown") -> pd.DataFrame:
    """Add support_zone / resistance_zone (dicts), support_strength /
    resistance_strength, and sr_interaction (debounced event string or None)
    columns.

    Requires: candle_engine.add_candle_metrics, indicator_engine.add_indicators
    (for ema/atr/vwap), find_swing_points, and add_reference_levels to have
    already run.
    """
    df = df.copy()
    n = len(df)
    if n == 0:
        for col in ("support_zone", "resistance_zone", "support_strength", "resistance_strength", "sr_interaction"):
            df[col] = None
        return df

    close = df["close"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float) if "atr" in df.columns else np.full(n, np.nan)
    ema = df["ema"].to_numpy(dtype=float) if "ema" in df.columns else np.full(n, np.nan)
    vwap = df["vwap"].to_numpy(dtype=float) if "vwap" in df.columns else np.full(n, np.nan)
    pdh = df["pdh"].to_numpy(dtype=float) if "pdh" in df.columns else np.full(n, np.nan)
    pdl = df["pdl"].to_numpy(dtype=float) if "pdl" in df.columns else np.full(n, np.nan)
    pwh = df["pwh"].to_numpy(dtype=float) if "pwh" in df.columns else np.full(n, np.nan)
    pwl = df["pwl"].to_numpy(dtype=float) if "pwl" in df.columns else np.full(n, np.nan)
    swing_high_flag = df["swing_high"].to_numpy() if "swing_high" in df.columns else np.zeros(n, dtype=bool)
    swing_low_flag = df["swing_low"].to_numpy() if "swing_low" in df.columns else np.zeros(n, dtype=bool)
    lookback = 5  # matches indicator_engine's default; swings confirm after this many candles

    # Rolling (backward-only) consolidation band: no lookahead since pandas
    # .rolling() without center=True only ever looks at the current row and
    # earlier ones.
    cons_window = 20
    roll_high = df["high"].rolling(cons_window, min_periods=cons_window).max().to_numpy()
    roll_low = df["low"].rolling(cons_window, min_periods=cons_window).min().to_numpy()
    is_consolidation = np.where(
        (close > 0) & ~np.isnan(roll_high) & ~np.isnan(roll_low),
        (roll_high - roll_low) / np.where(close > 0, close, np.nan) < 0.05,
        False,
    )

    # Genuine VWAP interaction: only trust VWAP as a source if price has
    # actually crossed it within a recent lookback window, not just because
    # it happens to be nearby right now. Uses strict above/below (not a
    # tri-state sign()) so a row where close happens to equal VWAP exactly
    # doesn't register as its own "side" and create a false crossing on the
    # next row -- that artifact is exactly what a naive sign-change check
    # would produce at the very first bar, where typical price and a
    # single-sample VWAP can coincide exactly.
    above = close > vwap
    below = close < vwap
    vwap_cross = np.zeros(n, dtype=bool)
    vwap_cross[1:] = (above[1:] & below[:-1]) | (below[1:] & above[:-1])
    vwap_recent_cross = (
        pd.Series(vwap_cross).rolling(_VWAP_INTERACTION_WINDOW, min_periods=1).max().astype(bool).to_numpy()
    )

    support_zones: list[dict | None] = [None] * n
    resistance_zones: list[dict | None] = [None] * n
    support_strength = np.zeros(n)
    resistance_strength = np.zeros(n)
    interactions: list[str | None] = [None] * n

    last_support_interaction: str | None = None
    last_resistance_interaction: str | None = None
    # The zone actually in effect going into the CURRENT candle -- i.e. the
    # previous row's chosen zone. Crossing/break/reclaim detection must use
    # this, not a zone recomputed fresh for the current row: once price
    # closes below a support level, that level is (correctly) no longer
    # classified as "support" relative to the NEW price, so a freshly
    # recomputed "nearest support" can never appear to have been "broken" by
    # this candle -- it simply won't be selected as support at all. This is
    # the same class of bug fixed earlier in market_structure/structure_engine.py.
    prev_support: Zone | None = None
    prev_resistance: Zone | None = None
    # level bucket (rounded to nearest 0.5%) -> last known state, used for a
    # first-pass FLIPPED detection (an old broken support/resistance now
    # acting as the opposite).
    broken_level_history: dict[float, str] = {}

    for i in range(n):
        price = close[i]
        tolerance = max(
            (atr[i] * ATR_TOLERANCE_MULT) if not np.isnan(atr[i]) else 0.0,
            price * PCT_TOLERANCE,
        )

        raw_levels: list[tuple[float, str]] = []
        lo = max(0, i - _SWING_LOOKBACK_WINDOW)
        for j in range(lo, i + 1):
            if swing_high_flag[j] and j + lookback <= i:
                raw_levels.append((high[j], "swing_high"))
            if swing_low_flag[j] and j + lookback <= i:
                raw_levels.append((low[j], "swing_low"))
        if not np.isnan(pdh[i]):
            raw_levels.append((pdh[i], "pdh"))
        if not np.isnan(pdl[i]):
            raw_levels.append((pdl[i], "pdl"))
        if not np.isnan(pwh[i]):
            raw_levels.append((pwh[i], "pwh"))
        if not np.isnan(pwl[i]):
            raw_levels.append((pwl[i], "pwl"))
        if not np.isnan(ema[i]):
            raw_levels.append((ema[i], "ema20"))
        if not np.isnan(vwap[i]) and vwap_recent_cross[i]:
            raw_levels.append((vwap[i], "vwap"))

        clusters = _cluster_levels(raw_levels, tolerance)
        if is_consolidation[i]:
            clusters.append((roll_low[i], roll_high[i], ["consolidation"]))

        best_support = None
        best_resistance = None
        for lower, upper, sources in clusters:
            mid = (lower + upper) / 2
            zone_type = "support" if mid < price else ("resistance" if mid > price else None)
            if zone_type is None:
                continue

            bucket = round(mid / max(tolerance, 1e-9))
            if bucket in broken_level_history and broken_level_history[bucket] != zone_type:
                if "breakout_retest" not in sources:
                    sources = sources + ["breakout_retest"]

            touches, since = _count_touches(df, i, lower, upper)
            strength = _zone_strength(sources, touches, since)
            state = "ACTIVE"
            if bucket in broken_level_history and broken_level_history[bucket] != zone_type:
                state = "FLIPPED"
            elif touches == 0:
                state = "ACTIVE"
            elif since is not None and since > _STALE_AFTER_CANDLES:
                state = "STALE"
            else:
                state = "TESTED"

            zone = Zone(
                type=zone_type,
                lower=lower,
                upper=upper,
                mid=mid,
                strength=strength,
                timeframe=timeframe,
                sources=sources,
                touches=touches,
                state=state,
                recency=str(df.index[i - since]) if since is not None else None,
            )
            if zone_type == "support":
                if best_support is None or mid > best_support.mid:
                    best_support = zone
            else:
                if best_resistance is None or mid < best_resistance.mid:
                    best_resistance = zone

        # --- Interaction detection (debounced), evaluated against the zone
        # that was actually in effect BEFORE this candle (prev_support /
        # prev_resistance), not the freshly-reclassified current-row zone.
        pc = close[i - 1] if i > 0 else price
        support_broke_this_row = False
        resistance_broke_this_row = False

        if prev_support is not None:
            z = prev_support
            candidate = None
            if pc >= z.lower and low[i] <= z.upper and price >= z.lower:
                candidate = "SUPPORT_REJECTION" if price > z.upper else "TOUCH_SUPPORT"
            elif pc >= z.lower and price < z.lower:
                candidate = "SUPPORT_BREAK"
            elif pc < z.lower and price > z.upper:
                candidate = "SUPPORT_RECLAIM"
            elif z.upper < pc <= z.upper * (1 + _NEAR_PCT) and low[i] > z.upper:
                candidate = "APPROACHING_SUPPORT"
            if candidate is not None and candidate != last_support_interaction:
                interactions[i] = candidate
                last_support_interaction = candidate
                if candidate == "SUPPORT_BREAK":
                    bucket = round(z.mid / max(tolerance, 1e-9))
                    broken_level_history[bucket] = "support"
                    support_broke_this_row = True

        if prev_resistance is not None:
            z = prev_resistance
            candidate = None
            if pc <= z.upper and high[i] >= z.lower and price <= z.upper:
                candidate = "RESISTANCE_REJECTION" if price < z.lower else "TOUCH_RESISTANCE"
            elif pc <= z.upper and price > z.upper:
                candidate = "RESISTANCE_BREAK"
            elif pc > z.upper and price < z.lower:
                candidate = "RESISTANCE_RECLAIM"
            elif z.lower * (1 - _NEAR_PCT) <= pc < z.lower and high[i] < z.lower:
                candidate = "APPROACHING_RESISTANCE"
            if candidate is not None and candidate != last_resistance_interaction:
                # Support and resistance interactions share one output column;
                # only overwrite if support didn't already claim this row.
                if interactions[i] is None:
                    interactions[i] = candidate
                last_resistance_interaction = candidate
                if candidate == "RESISTANCE_BREAK":
                    bucket = round(z.mid / max(tolerance, 1e-9))
                    broken_level_history[bucket] = "resistance"
                    resistance_broke_this_row = True

        # Displayed zone for this row: normally the freshly-reclassified
        # nearest zone, EXCEPT on the exact row a break just happened, where
        # showing the just-broken zone (marked BROKEN) is more informative
        # than silently jumping to some unrelated new level.
        if support_broke_this_row:
            broken = Zone(**{**prev_support.__dict__, "state": "BROKEN"})
            support_zones[i] = broken.to_dict()
            support_strength[i] = broken.strength
        elif best_support is not None:
            support_zones[i] = best_support.to_dict()
            support_strength[i] = best_support.strength

        if resistance_broke_this_row:
            broken = Zone(**{**prev_resistance.__dict__, "state": "BROKEN"})
            resistance_zones[i] = broken.to_dict()
            resistance_strength[i] = broken.strength
        elif best_resistance is not None:
            resistance_zones[i] = best_resistance.to_dict()
            resistance_strength[i] = best_resistance.strength

        prev_support = best_support
        prev_resistance = best_resistance

    df["support_zone"] = support_zones
    df["resistance_zone"] = resistance_zones
    df["support_strength"] = support_strength
    df["resistance_strength"] = resistance_strength
    df["sr_interaction"] = interactions
    return df


def get_support_resistance_snapshot(df: pd.DataFrame, ticker: str, timeframe: str = "unknown", lookback_events: int = 20) -> dict[str, Any]:
    """Build the full /support-resistance/{ticker} API response from the
    last row of a pipeline DataFrame that has already run through
    add_zone_columns.
    """
    if df.empty:
        return {
            "ticker": ticker,
            "price": None,
            "supports": [],
            "resistances": [],
            "nearest_support": None,
            "nearest_resistance": None,
            "interactions": [],
            "confluence": [],
        }

    last = df.iloc[-1]
    price = round(float(last["close"]), 2)
    nearest_support = last.get("support_zone")
    nearest_resistance = last.get("resistance_zone")

    recent = df.tail(lookback_events)
    interactions = [
        {"timestamp": str(idx), "event": row["sr_interaction"]}
        for idx, row in recent.iterrows()
        if row.get("sr_interaction")
    ]

    confluence = []
    for zone in (nearest_support, nearest_resistance):
        if zone and len(zone.get("sources", [])) >= 2:
            confluence.append(zone)

    return {
        "ticker": ticker,
        "price": price,
        "supports": [nearest_support] if nearest_support else [],
        "resistances": [nearest_resistance] if nearest_resistance else [],
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "interactions": interactions,
        "confluence": confluence,
    }
