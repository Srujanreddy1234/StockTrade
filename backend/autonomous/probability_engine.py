"""Fast buy/sell probability scoring from incremental tick statistics.

This is a transparent heuristic scorer, not a calibrated statistical model:
it combines mean-reversion (distance from the rolling low/high, z-score),
momentum (RSI, fast/slow EMA spread) into a single number in [0, 1] via a
logistic squash. Treat the output as "how strongly do several simple signals
agree that this looks like a good entry/exit right now", not a true
probability of profit. It is deliberately O(1) per tick (see tick_stats.py)
so it can run on every second-by-second price update across a watchlist
without falling behind.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.autonomous.tick_stats import TickerTickState

# Weights are hand-tuned, not fit to data. They express relative emphasis:
# "how close to the recent low/high" matters most, RSI and z-score corroborate
# it, and the EMA spread requires the trend to already be turning before we
# treat a dip as buyable (avoids catching a falling knife).
_W_RANGE = 2.2
_W_ZSCORE = 1.1
_W_RSI = 1.6
_W_MOMENTUM = 1.0


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


@dataclass
class ProbabilitySignal:
    buy_probability: float
    sell_probability: float
    range_position: float | None
    z_score: float
    rsi: float
    momentum: float  # ema_fast - ema_slow, sign indicates trend direction
    ready: bool  # False until the rolling window has enough samples


def score(state: TickerTickState, min_samples: int = 30) -> ProbabilitySignal:
    if state.last_price is None or state.window.count < min_samples:
        return ProbabilitySignal(0.0, 0.0, None, 0.0, 50.0, 0.0, ready=False)

    price = state.last_price
    range_position = state.window.position_in_range(price)
    z = state.window.z_score(price)
    rsi = state.rsi.value
    ema_fast = state.ema_fast.value or price
    ema_slow = state.ema_slow.value or price
    momentum = (ema_fast - ema_slow) / price if price else 0.0

    rp = range_position if range_position is not None else 0.5

    # Buy raw score: high when near the rolling low, below-mean, oversold,
    # and momentum is at least flat-to-turning (not still accelerating down).
    buy_raw = (
        _W_RANGE * (0.5 - rp) * 2.0
        + _W_ZSCORE * max(0.0, -z)
        + _W_RSI * max(0.0, (50.0 - rsi) / 50.0)
        + _W_MOMENTUM * math.tanh(momentum * 50)
    )
    # Sell raw score: high when near the rolling high, above-mean, overbought,
    # or momentum is rolling over.
    sell_raw = (
        _W_RANGE * (rp - 0.5) * 2.0
        + _W_ZSCORE * max(0.0, z)
        + _W_RSI * max(0.0, (rsi - 50.0) / 50.0)
        + _W_MOMENTUM * math.tanh(-momentum * 50)
    )

    return ProbabilitySignal(
        buy_probability=_sigmoid(buy_raw - 1.0),
        sell_probability=_sigmoid(sell_raw - 1.0),
        range_position=rp,
        z_score=z,
        rsi=rsi,
        momentum=momentum,
        ready=True,
    )
