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
