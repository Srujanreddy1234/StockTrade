"""NSE regular-session market hours guard (IST, Mon-Fri).

Does not account for exchange holidays (NSE publishes a yearly holiday
calendar) -- on a holiday this will incorrectly report the market as open.
Treat this as a necessary-but-not-sufficient check; order placement will
still fail (harmlessly) against Groww on a real holiday.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from backend.autonomous.config import AutonomousConfig

IST = ZoneInfo("Asia/Kolkata")


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def is_market_open(config: AutonomousConfig, now: datetime | None = None) -> bool:
    now = (now or datetime.now(IST)).astimezone(IST)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_t = _parse_hhmm(config.market_open)
    close_t = _parse_hhmm(config.market_close)
    return open_t <= now.time() <= close_t


def new_entries_allowed(config: AutonomousConfig, now: datetime | None = None) -> bool:
    """False once it's too late in the session to safely open AND exit a
    fresh intraday position before square-off. Independent of
    is_market_open() -- the market can still be open while this is False.
    """
    now = (now or datetime.now(IST)).astimezone(IST)
    cutoff = _parse_hhmm(config.no_new_entries_after)
    return now.time() < cutoff


def past_square_off(config: AutonomousConfig, now: datetime | None = None) -> bool:
    """True once every open intraday position must be force-closed,
    regardless of target/stop/signal state.
    """
    now = (now or datetime.now(IST)).astimezone(IST)
    cutoff = _parse_hhmm(config.square_off_time)
    return now.time() >= cutoff
