"""Educational content for the Learn section.

Reuses pattern descriptions from ``backend.signals.explanation_engine`` and
extends them with beginner-friendly sections on what each concept is, why it
matters, how it is detected, what confirms it, and what invalidates it.

Also includes core-concept entries (trend, support, resistance, risk/reward,
status meanings, and validation labels) that tie directly to the app's
scoring and backtesting findings.
"""

from __future__ import annotations

from backend.signals.explanation_engine import _PATTERN_INFO

# Each topic entry has the shape:
# {
#     "id": str,
#     "title": str,
#     "teaser": str,
#     "what_is_it": str,
#     "why_it_matters": str,
#     "how_is_it_detected": str,
#     "confirmation": list[str],
#     "invalidation": list[str],
# }
# Pattern entries also expose a ``direction`` field (``"bullish"``,
# ``"bearish"``, or ``"neutral"``) so the frontend can match them to the
# ``pattern`` field returned by the analysis pipeline.

_PATTERN_DESCRIPTIONS = {
    "doji": (
        "neutral",
        "Neither buyers nor sellers took control — the candle closed almost "
        "where it opened, so the market is undecided.",
    ),
    "hammer": (
        "bullish",
        "Sellers pushed the price down during the session, but buyers stepped "
        "back in and closed it near the high — a possible sign of strength.",
    ),
    "shooting_star": (
        "bearish",
        "Buyers pushed the price up, but sellers took over and closed it back "
        "near the low — a possible sign of weakness.",
    ),
    "bullish_engulfing": (
        "bullish",
        "A bullish candle completely swallowed the previous bearish candle, "
        "showing buyers took control.",
    ),
    "bearish_engulfing": (
        "bearish",
        "A bearish candle completely swallowed the previous bullish candle, "
        "showing sellers took control.",
    ),
    "morning_star": (
        "bullish",
        "After a down move, a small indecisive candle appeared and then buyers "
        "pushed the price back up — a possible bullish reversal.",
    ),
    "evening_star": (
        "bearish",
        "After an up move, a small indecisive candle appeared and then sellers "
        "pushed the price back down — a possible bearish reversal.",
    ),
}

_PATTERNS = {
    "doji": {
        "id": "doji",
        "title": "Doji",
        "teaser": "A candle that closes almost where it opened, signalling market indecision.",
        "direction": "neutral",
        "what_is_it": (
            "A doji is a single candle whose open and close are nearly equal, "
            "producing a tiny (or non-existent) body with wicks extending above and below."
        ),
        "why_it_matters": (
            "It shows buyers and sellers fought to a draw during the session. "
            "On its own it means nothing — but after a strong trend it can hint the momentum is fading."
        ),
        "how_is_it_detected": (
            "The engine checks whether the absolute difference between open and close "
            "is smaller than a small fraction of the candle's total range (high minus low). "
            "A long-legged doji has long wicks; a dragonfly or gravestone doji has wicks skewed to one side."
        ),
        "confirmation": [
            "A strong directional candle in the next session (green after a downtrend, red after an uptrend).",
            "Rising volume on the confirming candle.",
            "The doji appears near a known support or resistance level.",
        ],
        "invalidation": [
            "The next candle is small and directionless again — the market is still undecided.",
            "Price closes far on the 'wrong' side of the doji's wicks.",
            "Volume dries up completely on follow-through candles.",
        ],
    },
    "hammer": {
        "id": "hammer",
        "title": "Hammer",
        "teaser": "A bullish reversal candle with a long lower wick and a small body near the high.",
        "direction": "bullish",
        "what_is_it": (
            "A hammer has a small body near the top of the candle and a long lower wick "
            "(at least twice the body length). It looks like a hammer head hanging down."
        ),
        "why_it_matters": (
            "It means sellers drove the price sharply lower during the session, "
            "but buyers overwhelmed them before the close. After a downtrend, this "
            "can be the first sign of a reversal."
        ),
        "how_is_it_detected": (
            "The engine checks: (1) the lower wick is at least 2x the body, "
            "(2) the upper wick is very small or absent, "
            "(3) the body is in the upper third of the candle's range."
        ),
        "confirmation": [
            "The next candle closes above the hammer's high ( bullish confirmation).",
            "Volume is above average on the hammer or the next candle.",
            "The hammer forms at or just above an existing support level.",
            "Broader trend is sideways or down (a hammer in a strong rally is less meaningful).",
        ],
        "invalidation": [
            "Price closes below the hammer's low on the next candle.",
            "The hammer appears in the middle of a range with no nearby support level.",
            "Volume is very low, suggesting no real buying interest.",
        ],
    },
    "shooting_star": {
        "id": "shooting_star",
        "title": "Shooting Star",
        "teaser": "A bearish reversal candle with a long upper wick and a small body near the low.",
        "direction": "bearish",
        "what_is_it": (
            "A shooting star is the mirror image of a hammer: a small body near the "
            "bottom of the candle and a long upper wick (at least twice the body length)."
        ),
        "why_it_matters": (
            "It means buyers tried to push the price higher but sellers forced it back "
            "down before the close. After an uptrend, this can signal that the rally is stalling."
        ),
        "how_is_it_detected": (
            "The engine checks: (1) the upper wick is at least 2x the body, "
            "(2) the lower wick is very small or absent, "
            "(3) the body is in the lower third of the candle's range."
        ),
        "confirmation": [
            "The next candle closes below the shooting star's low.",
            "Volume spikes on the shooting star or the next red candle.",
            "The shooting star forms at or just below a known resistance level.",
        ],
        "invalidation": [
            "Price closes above the shooting star's high on the next candle.",
            "The pattern appears mid-range with no nearby resistance.",
            "Volume is very low, suggesting the sell-off lacked conviction.",
        ],
    },
    "bullish_engulfing": {
        "id": "bullish_engulfing",
        "title": "Bullish Engulfing",
        "teaser": "A green candle that completely covers the body of the previous red candle.",
        "direction": "bullish",
        "what_is_it": (
            "A two-candle pattern: a red (down) candle followed by a larger green (up) "
            "candle whose body fully engulfs the previous candle's body."
        ),
        "why_it_matters": (
            "It shows buyers not only erased the prior session's losses but pushed "
            "the price further up, signalling a potential shift in control."
        ),
        "how_is_it_detected": (
            "The engine checks: (1) previous candle is bearish (close < open), "
            "(2) current candle is bullish (close > open), "
            "(3) current body fully contains the previous body (current open <= previous close "
            "and current close >= previous open)."
        ),
        "confirmation": [
            "A third green candle that holds above the engulfing candle's close.",
            "Volume above average on the engulfing candle.",
            "The pattern occurs near a support level or after a pullback in an uptrend.",
        ],
        "invalidation": [
            "Price gaps down and closes below the engulfing candle's low.",
            "The next candle is a small doji, suggesting the momentum has already faded.",
            "The engulfing candle's body is tiny relative to prior volatility.",
        ],
    },
    "bearish_engulfing": {
        "id": "bearish_engulfing",
        "title": "Bearish Engulfing",
        "teaser": "A red candle that completely covers the body of the previous green candle.",
        "direction": "bearish",
        "what_is_it": (
            "A two-candle pattern: a green (up) candle followed by a larger red (down) "
            "candle whose body fully engulfs the previous candle's body."
        ),
        "why_it_matters": (
            "It shows sellers not only erased the prior session's gains but pushed "
            "the price lower, signalling a potential shift in control."
        ),
        "how_is_it_detected": (
            "The engine checks: (1) previous candle is bullish (close > open), "
            "(2) current candle is bearish (close < open), "
            "(3) current body fully contains the previous body (current open >= previous close "
            "and current close <= previous open)."
        ),
        "confirmation": [
            "A third red candle that closes below the engulfing candle's low.",
            "Volume above average on the engulfing candle.",
            "The pattern occurs near a resistance level or after a rally in a downtrend.",
        ],
        "invalidation": [
            "Price gaps up and closes above the engulfing candle's high.",
            "The next candle is a small doji, suggesting the sell-off lacked follow-through.",
            "The engulfing candle's body is tiny relative to prior volatility.",
        ],
    },
    "morning_star": {
        "id": "morning_star",
        "title": "Morning Star",
        "teaser": "A three-candle bullish reversal pattern signalling a potential bottom.",
        "direction": "bullish",
        "what_is_it": (
            "A three-candle pattern: a long red candle, followed by a small-bodied "
            "indecisive candle (the 'star'), followed by a long green candle that "
            "closes well into the body of the first candle."
        ),
        "why_it_matters": (
            "It captures a transition from strong selling to indecision to strong buying. "
            "Because it involves three candles and a clear gap, it is generally considered "
            "a more reliable reversal signal than a single-candle pattern."
        ),
        "how_is_it_detected": (
            "The engine checks: (1) first candle is bearish with a long body, "
            "(2) second candle gaps down and has a small body, "
            "(3) third candle is bullish and closes above the midpoint of the first candle's body."
        ),
        "confirmation": [
            "A fourth green candle that holds above the morning star's high.",
            "Volume is above average on the third (bullish) candle.",
            "The pattern appears after a clear downtrend, not in a sideways chop.",
        ],
        "invalidation": [
            "Price gaps up at the open but then closes below the star's body.",
            "The third candle fails to close above the midpoint of the first candle.",
            "The pattern forms near a resistance level, so any rally may be short-lived.",
        ],
    },
    "evening_star": {
        "id": "evening_star",
        "title": "Evening Star",
        "teaser": "A three-candle bearish reversal pattern signalling a potential top.",
        "direction": "bearish",
        "what_is_it": (
            "A three-candle pattern: a long green candle, followed by a small-bodied "
            "indecisive candle (the 'star'), followed by a long red candle that "
            "closes well into the body of the first candle."
        ),
        "why_it_matters": (
            "It captures a transition from strong buying to indecision to strong selling. "
            "Like the morning star, the three-candle structure and gap make it a stronger "
            "signal than a single candle."
        ),
        "how_is_it_detected": (
            "The engine checks: (1) first candle is bullish with a long body, "
            "(2) second candle gaps up and has a small body, "
            "(3) third candle is bearish and closes below the midpoint of the first candle's body."
        ),
        "confirmation": [
            "A fourth red candle that closes below the evening star's low.",
            "Volume is above average on the third (bearish) candle.",
            "The pattern appears after a clear uptrend, not in a sideways range.",
        ],
        "invalidation": [
            "Price gaps down at the open but then closes above the star's body.",
            "The third candle fails to close below the midpoint of the first candle.",
            "The pattern forms near a support level, so any decline may be short-lived.",
        ],
    },
}

_CHART_PATTERNS = {
    "double_top": {
        "id": "double_top",
        "title": "Double Top",
        "teaser": "Two similar swing highs separated by a swing low, often signaling a bearish reversal.",
        "direction": "bearish",
        "what_is_it": (
            "A double top is a reversal pattern where price hits a resistance level twice, "
            "with a swing low in between. The second peak is roughly level with the first "
            "(within ~1.5%), and the 'neckline' is that middle swing low."
        ),
        "why_it_matters": (
            "It shows buyers tried to push price higher twice but failed both times. "
            "When price breaks below the neckline, it suggests the rally is exhausted "
            "and a downtrend may follow."
        ),
        "how_is_it_detected": (
            "The engine scans for two recent swing highs within ~1.5% of each other, "
            "with a swing low between them. It then checks whether the latest price has "
            "broken below that middle swing low (the neckline)."
        ),
        "confirmation": [
            "Price closes decisively below the neckline (the swing low between the two peaks).",
            "Volume is higher on the breakdown candle than on the peaks.",
            "The breakdown holds for at least one or two candles after the break.",
        ],
        "invalidation": [
            "Price bounces back above the neckline quickly -- the breakdown was a false signal.",
            "The two peaks are more than ~1.5% apart, so they are not truly 'double'.",
            "No clear swing low exists between the peaks, so there is no neckline to break.",
        ],
    },
    "double_bottom": {
        "id": "double_bottom",
        "title": "Double Bottom",
        "teaser": "Two similar swing lows separated by a swing high, often signaling a bullish reversal.",
        "direction": "bullish",
        "what_is_it": (
            "A double bottom is the mirror image of a double top. Price hits a support "
            "level twice, with a swing high in between. The second trough is roughly level "
            "with the first, and the 'neckline' is that middle swing high."
        ),
        "why_it_matters": (
            "It shows sellers tried to push price lower twice but failed both times. "
            "When price breaks above the neckline, it suggests the downtrend is exhausted "
            "and an uptrend may follow."
        ),
        "how_is_it_detected": (
            "The engine scans for two recent swing lows within ~1.5% of each other, "
            "with a swing high between them. It then checks whether the latest price has "
            "broken above that middle swing high (the neckline)."
        ),
        "confirmation": [
            "Price closes decisively above the neckline (the swing high between the two troughs).",
            "Volume is higher on the breakout candle than on the troughs.",
            "The breakout holds for at least one or two candles after the break.",
        ],
        "invalidation": [
            "Price falls back below the neckline quickly -- the breakout was a false signal.",
            "The two troughs are more than ~1.5% apart, so they are not truly 'double'.",
            "No clear swing high exists between the troughs, so there is no neckline to break.",
        ],
    },
    "ascending_triangle": {
        "id": "ascending_triangle",
        "title": "Ascending Triangle",
        "teaser": "A flat resistance level with rising swing lows beneath it -- a bullish continuation pattern.",
        "direction": "bullish",
        "what_is_it": (
            "An ascending triangle forms when price is contained by a horizontal resistance "
            "level (two or more swing highs at roughly the same price) while swing lows "
            "below it keep rising. The range converges into a triangle shape."
        ),
        "why_it_matters": (
            "It shows buyers are stepping in at higher and higher prices, but sellers "
            "are consistently defending the same ceiling. The tension usually resolves "
            "with a breakout above resistance."
        ),
        "how_is_it_detected": (
            "The engine looks for 2+ recent swing highs within ~1.5% of each other "
            "(flat top) and at least 2 swing lows between them that are rising "
            "(each low is higher than the previous one)."
        ),
        "confirmation": [
            "Price breaks above the flat resistance level on above-average volume.",
            "The breakout candle closes firmly above the resistance, not just a wick.",
            "The rising lows sequence has at least 3 distinct higher lows.",
        ],
        "invalidation": [
            "Price fails to break resistance and instead breaks below the most recent rising low.",
            "The 'flat' highs are more than ~1.5% apart, so resistance is not actually flat.",
            "The lows are not actually rising -- they are flat or falling.",
        ],
    },
    "descending_triangle": {
        "id": "descending_triangle",
        "title": "Descending Triangle",
        "teaser": "A flat support level with falling swing highs above it -- a bearish continuation pattern.",
        "direction": "bearish",
        "what_is_it": (
            "A descending triangle is the mirror of an ascending triangle. Price is contained "
            "by a horizontal support level (two or more swing lows at roughly the same price) "
            "while swing highs above it keep falling. The range converges downward."
        ),
        "why_it_matters": (
            "It shows sellers are stepping in at lower and lower prices, but buyers "
            "are consistently defending the same floor. The tension usually resolves "
            "with a breakdown below support."
        ),
        "how_is_it_detected": (
            "The engine looks for 2+ recent swing lows within ~1.5% of each other "
            "(flat bottom) and at least 2 swing highs between them that are falling "
            "(each high is lower than the previous one)."
        ),
        "confirmation": [
            "Price breaks below the flat support level on above-average volume.",
            "The breakdown candle closes firmly below the support, not just a wick.",
            "The falling highs sequence has at least 3 distinct lower highs.",
        ],
        "invalidation": [
            "Price fails to break support and instead rallies above the most recent falling high.",
            "The 'flat' lows are more than ~1.5% apart, so support is not actually flat.",
            "The highs are not actually falling -- they are flat or rising.",
        ],
    },
}

_CORE_CONCEPTS = {
    "trend": {
        "id": "trend",
        "title": "Trend",
        "teaser": "The general direction price is moving over time — the backbone of every setup.",
        "direction": None,
        "what_is_it": (
            "Trend describes the overall direction of price movement: uptrend (higher highs and higher lows), "
            "downtrend (lower highs and lower lows), or sideways (no clear direction). "
            "The app determines trend using swing-point analysis on recent price data."
        ),
        "why_it_matters": (
            "Trading with the trend increases the odds that a pattern will play out. "
            "A bullish pattern in an uptrend is more reliable than the same pattern in a downtrend."
        ),
        "how_is_it_detected": (
            "The engine identifies swing highs and swing lows over a rolling lookback window. "
            "A series of rising swing points signals an uptrend; falling swing points signal a downtrend."
        ),
        "confirmation": [
            "Price is making a series of higher highs and higher lows (uptrend) or lower highs and lower lows (downtrend).",
            "Moving averages (e.g. EMA) slope in the trend direction.",
            "The trend has persisted for multiple weeks, not just a single volatile session.",
        ],
        "invalidation": [
            "Price breaks a key swing point in the opposite direction.",
            "The moving average flattens or crosses price sharply.",
            "The trend exists on only a very short timeframe (e.g. 15-minute chart) but not daily.",
        ],
    },
    "support": {
        "id": "support",
        "title": "Support",
        "teaser": "A price level where buying pressure has historically prevented further declines.",
        "direction": None,
        "what_is_it": (
            "Support is a price level where the asset has repeatedly found buying interest "
            "and bounced upward. It acts like a 'floor' — when price falls to that level, "
            "demand typically increases."
        ),
        "why_it_matters": (
            "Patterns that form at or near support are stronger than identical patterns "
            "in the middle of nowhere. Support gives the setup a logical reason to work."
        ),
        "how_is_it_detected": (
            "The engine clusters recent swing lows that are within a tolerance band. "
            "If the current price is within that band, it flags the candle as 'near support'."
        ),
        "confirmation": [
            "Price has bounced off this level at least twice before.",
            "Volume spikes when the level is tested.",
            "The level aligns with a round number or a prior significant low.",
        ],
        "invalidation": [
            "Price closes decisively below the level (typically on higher-than-average volume).",
            "The level was only tested once and broke immediately.",
            "The support band is so wide it spans more than 2-3% of price — that is noise, not a level.",
        ],
    },
    "resistance": {
        "id": "resistance",
        "title": "Resistance",
        "teaser": "A price level where selling pressure has historically prevented further gains.",
        "direction": None,
        "what_is_it": (
            "Resistance is the opposite of support: a price ceiling where the asset has "
            "repeatedly faced selling pressure and reversed downward. It acts like a 'roof'."
        ),
        "why_it_matters": (
            "Bearish patterns at resistance are stronger. The level provides a logical "
            "target for profit-taking and a natural place for sellers to step in."
        ),
        "how_is_it_detected": (
            "The engine clusters recent swing highs within a tolerance band. "
            "If the current price is within that band, it flags the candle as 'near resistance'."
        ),
        "confirmation": [
            "Price has reversed off this level at least twice before.",
            "Volume rises as the level is approached.",
            "The level aligns with a round number or a prior significant high.",
        ],
        "invalidation": [
            "Price closes decisively above the level (ideally on higher-than-average volume).",
            "The level was only tested once and broke immediately.",
            "The resistance band is so wide it spans more than 2-3% of price — that is noise, not a level.",
        ],
    },
    "risk_reward": {
        "id": "risk_reward",
        "title": "Risk / Reward",
        "teaser": "The ratio of potential profit to potential loss on a trade.",
        "direction": None,
        "what_is_it": (
            "Risk/reward compares how much you stand to gain (reward) against how much "
            "you stand to lose (risk) if the trade hits its invalidation level. "
            "A 1:2 ratio means you risk $1 to make $2."
        ),
        "why_it_matters": (
            "Even a pattern with a 40% win rate can be profitable if the winners are "
            "twice as large as the losers. Low risk/reward setups require near-perfect "
            "timing to be worthwhile."
        ),
        "how_is_it_detected": (
            "The engine computes the distance from the entry zone midpoint to the nearest "
            "target (reward) and the distance from the entry zone midpoint to the "
            "invalidation level (risk). The ratio is reward / risk."
        ),
        "confirmation": [
            "The nearest target is at least 1.5x the distance to the invalidation level (the app's minimum threshold).",
            "The second target is 2x or more, giving room to scale out.",
            "The entry zone is tight, so the required stop distance is small.",
        ],
        "invalidation": [
            "The invalidation level is so far away that the risk dwarfs the reward.",
            "The entry zone is extremely wide, making the stop distance unpredictable.",
            "Targets are too close together, offering no incremental reward for holding.",
        ],
    },
    "entry_status": {
        "id": "entry_status",
        "title": "ENTRY / WATCH / NO TRADE",
        "teaser": "What the app's three status labels mean and when each is used.",
        "direction": None,
        "what_is_it": (
            "The app assigns one of three statuses to every analysed candle:\n\n"
            "**ENTRY** — All conditions are met: a confirmed pattern, supportive trend, "
            "near support/resistance, acceptable risk/reward, and a validated confidence label.\n\n"
            "**WATCH** — Price is near a key level in a matching trend, but no confirming "
            "candle pattern has formed yet, or the setup is not yet fully validated.\n\n"
            "**NO TRADE** — The current candle does not offer a tradeable setup. "
            "This is the default state for most candles."
        ),
        "why_it_matters": (
            "These labels save you from scanning charts manually. ENTRY is the app's "
            "highest-confidence signal; WATCH tells you to keep an eye on a level; "
            "NO TRADE means stay out."
        ),
        "how_is_it_detected": (
            "A scoring engine tallies points for pattern presence, trend agreement, "
            "proximity to support/resistance, volume confirmation, and risk/reward. "
            "ENTRY requires the highest score threshold; WATCH is a middle band; "
            "everything below is NO TRADE."
        ),
        "confirmation": [
            "ENTRY: score is above the ENTRY threshold, a pattern fired, and trend agrees.",
            "WATCH: score is in the WATCH band, or price is near a key level but the pattern is missing.",
            "NO TRADE: score is low, no pattern, trend opposes, or risk/reward is unacceptable.",
        ],
        "invalidation": [
            "ENTRY degrades to WATCH if the pattern is only provisional and the next candle breaks the setup.",
            "WATCH degrades to NO TRADE if price moves away from the key level without forming a pattern.",
            "A setup is never ENTRY if the confidence label is missing or the risk/reward is below 1.5.",
        ],
    },
    "validation_labels": {
        "id": "validation_labels",
        "title": "Provisional vs. Experimental",
        "teaser": "Honest confidence labels tied to real backtesting — not generic disclaimers.",
        "direction": None,
        "what_is_it": (
            "**Provisional** (bullish signals) — Backtesting across multiple stocks and years "
            "showed promising directional accuracy (48% on held-out data vs a 25% baseline), "
            "but the sample size is still limited. Treat as a useful early signal, not a guarantee.\n\n"
            "**Experimental** (bearish signals) — Backtesting across 40 stocks and 5 years "
            "showed no reliable directional accuracy for this signal type yet. "
            "The model has not demonstrated skill at bearish pattern detection, so these "
            "labels are included for completeness but should be weighted far less heavily."
        ),
        "why_it_matters": (
            "Most apps give every signal a confident-sounding label. This app tells you "
            "what the data actually says: bullish patterns are encouraging but unproven at scale; "
            "bearish patterns are not yet ready to trade on. Knowing this prevents overconfidence."
        ),
        "how_is_it_detected": (
            "During pipeline execution, the scoring engine assigns a direction to each "
            "pattern. The explanation engine then maps direction to a validation label: "
            "'bullish' -> 'provisional', 'bearish' -> 'experimental', neutral -> None. "
            "These labels are surfaced in the UI and included in validation notes."
        ),
        "confirmation": [
            "Bullish (provisional): held-out backtest accuracy is above the random baseline, suggesting real (if modest) skill.",
            "The label changes if retraining on new data improves the backtest results.",
            "More training data over time will convert provisional -> proven, or experimental -> provisional.",
        ],
        "invalidation": [
            "Bearish (experimental): held-out backtest accuracy is at or below random — there is no demonstrated skill yet.",
            "A signal is never labeled 'provisional' or 'experimental' for neutral patterns (directionless setups carry no predictive claim).",
            "If the backtest methodology changes, the labels are recomputed rather than carried forward.",
        ],
    },
}

LEARN_CONTENT: dict[str, dict] = {**_PATTERNS, **_CHART_PATTERNS, **_CORE_CONCEPTS}


def list_topics() -> list[dict]:
    """Return a lightweight list of (id, title, teaser) for every topic."""
    return [
        {
            "id": topic["id"],
            "title": topic["title"],
            "teaser": topic["teaser"],
            "direction": topic.get("direction"),
        }
        for topic in LEARN_CONTENT.values()
    ]


def get_topic(topic_id: str) -> dict | None:
    """Return the full content dict for a single topic, or None if not found."""
    return LEARN_CONTENT.get(topic_id)
