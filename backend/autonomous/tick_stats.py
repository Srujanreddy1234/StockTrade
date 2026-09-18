"""O(1)-per-update incremental statistics for live tick prices.

Recomputing mean/std/min/max/RSI over a growing price history on every tick
would be O(n) per tick and would not scale to a 1-second refresh rate across
a watchlist. Everything here updates in amortized O(1) time per new price so
the per-second loop stays cheap no matter how long the session runs:

- Rolling mean/std over a fixed window: Welford-style running sums with a
  bounded deque, updated incrementally (subtract the value leaving the
  window, add the value entering it).
- Rolling min/max over a fixed window: a monotonic deque (classic sliding
  window minimum/maximum), amortized O(1) per push.
- RSI: Wilder's smoothing, an EMA-style recurrence -> O(1) per tick.
- EMA: standard exponential recurrence -> O(1) per tick.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


class RollingWindowStats:
    """Fixed-size rolling mean/std/min/max, all O(1) amortized per push."""

    def __init__(self, window: int) -> None:
        self.window = window
        self._values: deque[float] = deque()
        self._sum = 0.0
        self._sum_sq = 0.0
        # Monotonic deques store (value) with strictly increasing index order,
        # front is always the current window min / max.
        self._min_deque: deque[float] = deque()
        self._max_deque: deque[float] = deque()

    def push(self, value: float) -> None:
        self._values.append(value)
        self._sum += value
        self._sum_sq += value * value

        while self._min_deque and self._min_deque[-1] >= value:
            self._min_deque.pop()
        self._min_deque.append(value)

        while self._max_deque and self._max_deque[-1] <= value:
            self._max_deque.pop()
        self._max_deque.append(value)

        if len(self._values) > self.window:
            old = self._values.popleft()
            self._sum -= old
            self._sum_sq -= old * old
            if self._min_deque[0] == old:
                self._min_deque.popleft()
            if self._max_deque[0] == old:
                self._max_deque.popleft()

    @property
    def count(self) -> int:
        return len(self._values)

    @property
    def mean(self) -> float:
        if not self._values:
            return 0.0
        return self._sum / len(self._values)

    @property
    def std(self) -> float:
        n = len(self._values)
        if n < 2:
            return 0.0
        variance = max(0.0, (self._sum_sq / n) - self.mean**2)
        return variance**0.5

    @property
    def min(self) -> float | None:
        return self._min_deque[0] if self._min_deque else None

    @property
    def max(self) -> float | None:
        return self._max_deque[0] if self._max_deque else None

    def z_score(self, value: float) -> float:
        std = self.std
        if std < 1e-9:
            return 0.0
        return (value - self.mean) / std

    def position_in_range(self, value: float) -> float | None:
        """0.0 = at the rolling low, 1.0 = at the rolling high."""
        lo, hi = self.min, self.max
        if lo is None or hi is None or hi - lo < 1e-9:
            return None
        return (value - lo) / (hi - lo)


class WilderRSI:
    """Wilder-smoothed RSI, O(1) per new price."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev_price: float | None = None
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self.value: float = 50.0

    def push(self, price: float) -> float:
        if self._prev_price is None:
            self._prev_price = price
            return self.value

        change = price - self._prev_price
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        self._prev_price = price

        if self._avg_gain is None or self._avg_loss is None:
            self._avg_gain = gain
            self._avg_loss = loss
        else:
            alpha = 1.0 / self.period
            self._avg_gain = (1 - alpha) * self._avg_gain + alpha * gain
            self._avg_loss = (1 - alpha) * self._avg_loss + alpha * loss

        if self._avg_loss < 1e-9:
            self.value = 100.0
        else:
            rs = self._avg_gain / self._avg_loss
            self.value = 100 - (100 / (1 + rs))
        return self.value


class EMA:
    """Standard exponential moving average, O(1) per new price."""

    def __init__(self, span: int) -> None:
        self.alpha = 2.0 / (span + 1)
        self.value: float | None = None

    def push(self, price: float) -> float:
        if self.value is None:
            self.value = price
        else:
            self.value = self.alpha * price + (1 - self.alpha) * self.value
        return self.value


@dataclass
class TickerTickState:
    """All incremental state needed to score one ticker on every tick."""

    window: RollingWindowStats
    rsi: WilderRSI = field(default_factory=lambda: WilderRSI(14))
    ema_fast: EMA = field(default_factory=lambda: EMA(9))
    ema_slow: EMA = field(default_factory=lambda: EMA(26))
    last_price: float | None = None

    def push(self, price: float) -> None:
        self.window.push(price)
        self.rsi.push(price)
        self.ema_fast.push(price)
        self.ema_slow.push(price)
        self.last_price = price


def new_state(rolling_window: int) -> TickerTickState:
    return TickerTickState(window=RollingWindowStats(rolling_window))
