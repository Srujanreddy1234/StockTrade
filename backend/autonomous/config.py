"""Configuration for the autonomous trading loop, loaded from environment
variables so risk limits can be tuned without touching code.

Every limit here exists because an unattended loop that can place real
orders needs hard boundaries that do not depend on the strategy logic being
correct. Defaults are deliberately conservative.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return [t.strip() for t in raw.split(",") if t.strip()]


@dataclass(frozen=True)
class AutonomousConfig:
    # Universe to watch. Kept small on purpose for a first autonomous run.
    watchlist: list[str] = field(
        default_factory=lambda: _env_list(
            "AUTOTRADE_WATCHLIST",
            ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"],
        )
    )
    exchange: str = os.environ.get("AUTOTRADE_EXCHANGE", "NSE")

    # Loop timing.
    tick_interval_seconds: float = _env_float("AUTOTRADE_TICK_SECONDS", 1.0)
    pipeline_refresh_seconds: float = _env_float("AUTOTRADE_PIPELINE_REFRESH_SECONDS", 60.0)
    candle_interval: str = os.environ.get("AUTOTRADE_CANDLE_INTERVAL", "5m")
    candle_period: str = os.environ.get("AUTOTRADE_CANDLE_PERIOD", "60d")

    # Rolling window (in ticks) used for the fast O(1) probability engine.
    rolling_window: int = _env_int("AUTOTRADE_ROLLING_WINDOW", 300)

    # Decision thresholds (probabilities in [0, 1]).
    buy_probability_threshold: float = _env_float("AUTOTRADE_BUY_THRESHOLD", 0.68)
    sell_probability_threshold: float = _env_float("AUTOTRADE_SELL_THRESHOLD", 0.65)

    # --- Risk guardrails (non-negotiable; all enabled by default) ---
    # Max fraction of available margin risked on any single trade.
    max_capital_per_trade_pct: float = _env_float("AUTOTRADE_MAX_CAPITAL_PCT", 0.05)
    # Absolute rupee cap per trade, applied on top of the percentage cap
    # (whichever is smaller wins). 0 disables the absolute cap.
    max_capital_per_trade_abs: float = _env_float("AUTOTRADE_MAX_CAPITAL_ABS", 10000.0)
    # Daily loss kill-switch: stop opening new positions once cumulative
    # realized + unrealized loss for the day crosses this fraction of the
    # margin snapshot taken at day start.
    daily_loss_limit_pct: float = _env_float("AUTOTRADE_DAILY_LOSS_LIMIT_PCT", 0.03)
    # Max number of concurrent open autonomous positions.
    max_open_positions: int = _env_int("AUTOTRADE_MAX_OPEN_POSITIONS", 3)
    # Cooldown after closing a position on a ticker before re-entering it.
    cooldown_minutes: float = _env_float("AUTOTRADE_COOLDOWN_MINUTES", 15.0)

    # Order type used for autonomous entries/exits. MARKET keeps execution
    # simple and guarantees the fill happens; slippage risk is accepted and
    # bounded by the position-sizing cap above.
    order_type: str = os.environ.get("AUTOTRADE_ORDER_TYPE", "MARKET")
    product: str = os.environ.get("AUTOTRADE_PRODUCT", "CNC")

    # Market hours guard (NSE regular session, IST). Trading outside these
    # hours is refused regardless of signals.
    market_open: str = os.environ.get("AUTOTRADE_MARKET_OPEN", "09:15")
    market_close: str = os.environ.get("AUTOTRADE_MARKET_CLOSE", "15:20")

    # Kill-switch state file (survives process restarts within a trading day).
    state_path: str = os.environ.get("AUTOTRADE_STATE_PATH", "./autotrade_state.json")


def load_config() -> AutonomousConfig:
    return AutonomousConfig()
