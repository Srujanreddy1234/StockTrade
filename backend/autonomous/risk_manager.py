"""Hard risk guardrails for the autonomous loop.

These checks are intentionally independent of the strategy/probability
logic: even if the scoring engine is wrong or a broken feed produces bad
signals, the loop still cannot exceed these boundaries. State persists to a
small JSON file so a restart mid-day does not reset the daily loss counter
or the kill-switch.

Four guardrails, all enabled by default:
  1. Max capital per trade (pct of available margin, capped by an absolute
     rupee ceiling too) -- bounds the damage from any single bad trade.
  2. Daily loss kill-switch -- once realized+unrealized loss for the day
     crosses a threshold, no new positions are opened until a human resets
     it. Existing positions are still monitored so target/stop exits keep
     working.
  3. Max concurrent open positions -- bounds total exposure and prevents the
     loop from spreading capital across too many simultaneous bets.
  4. Per-ticker cooldown after a closed trade -- prevents rapid re-entry
     whipsaw on noisy ticks right after an exit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.autonomous.config import AutonomousConfig

IST = ZoneInfo("Asia/Kolkata")


def _today_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


@dataclass
class RiskState:
    date: str = field(default_factory=_today_str)
    day_start_margin: float | None = None
    realized_pnl_today: float = 0.0
    # realized_pnl_today at the moment of the last manual kill-switch reset
    # (or 0.0 if never reset today). The loss-limit check is evaluated
    # relative to this baseline, not to the raw daily total -- otherwise a
    # reset would be immediately overridden by the very loss that triggered
    # it, since realized_pnl_today does not change just because a human
    # acknowledged the drawdown and chose to keep trading.
    loss_baseline: float = 0.0
    kill_switch_active: bool = False
    kill_switch_reason: str | None = None
    last_closed_at: dict[str, str] = field(default_factory=dict)  # ticker -> iso ts

    @classmethod
    def load(cls, path: str) -> "RiskState":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r") as f:
                data = json.load(f)
            state = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()
        if state.date != _today_str():
            # New trading day: carry nothing forward except the kill switch
            # reason for audit purposes is dropped; loss counters reset.
            return cls()
        return state

    def save(self, path: str) -> None:
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(self.__dict__, f, indent=2)
        os.replace(tmp_path, path)


class RiskManager:
    def __init__(self, config: AutonomousConfig) -> None:
        self.config = config
        self.state = RiskState.load(config.state_path)

    def _save(self) -> None:
        self.state.save(self.config.state_path)

    def ensure_day_started(self, available_margin: float) -> None:
        if self.state.day_start_margin is None:
            self.state.day_start_margin = available_margin
            self._save()

    def record_realized_pnl(self, amount: float) -> None:
        self.state.realized_pnl_today += amount
        self._save()

    def record_position_closed(self, ticker: str) -> None:
        self.state.last_closed_at[ticker] = datetime.now(IST).isoformat()
        self._save()

    def reset_kill_switch(self) -> None:
        self.state.kill_switch_active = False
        self.state.kill_switch_reason = None
        # Give a fresh loss allowance from the current P&L, so the same
        # already-acknowledged loss doesn't immediately re-trip the switch.
        self.state.loss_baseline = self.state.realized_pnl_today
        self._save()

    def check_kill_switch(self, unrealized_pnl_estimate: float = 0.0) -> bool:
        """Re-evaluate and persist the kill-switch. Returns True if active."""
        if self.state.day_start_margin and not self.state.kill_switch_active:
            loss_limit = self.state.day_start_margin * self.config.daily_loss_limit_pct
            pnl_since_baseline = (
                self.state.realized_pnl_today - self.state.loss_baseline
            ) + unrealized_pnl_estimate
            if pnl_since_baseline <= -loss_limit:
                self.state.kill_switch_active = True
                self.state.kill_switch_reason = (
                    f"Daily loss limit hit: {pnl_since_baseline:.2f} <= -{loss_limit:.2f}"
                    + (
                        f" (relative to {self.state.loss_baseline:.2f} reset baseline)"
                        if self.state.loss_baseline
                        else ""
                    )
                )
                self._save()
        return self.state.kill_switch_active

    def cooldown_ok(self, ticker: str) -> bool:
        last = self.state.last_closed_at.get(ticker)
        if not last:
            return True
        elapsed_minutes = (datetime.now(IST) - datetime.fromisoformat(last)).total_seconds() / 60.0
        return elapsed_minutes >= self.config.cooldown_minutes

    def position_limit_ok(self, open_position_count: int) -> bool:
        return open_position_count < self.config.max_open_positions

    def max_trade_value(self, available_margin: float) -> float:
        pct_cap = available_margin * self.config.max_capital_per_trade_pct
        if self.config.max_capital_per_trade_abs > 0:
            return min(pct_cap, self.config.max_capital_per_trade_abs)
        return pct_cap

    def size_position(self, available_margin: float, price: float) -> int:
        """Return the max whole-share quantity allowed for a new trade."""
        if price <= 0:
            return 0
        cap = self.max_trade_value(available_margin)
        return max(0, int(cap // price))

    def can_open_new_position(
        self, ticker: str, open_position_count: int, unrealized_pnl_estimate: float = 0.0
    ) -> tuple[bool, str | None]:
        if self.check_kill_switch(unrealized_pnl_estimate):
            return False, self.state.kill_switch_reason
        if not self.position_limit_ok(open_position_count):
            return False, f"max open positions reached ({self.config.max_open_positions})"
        if not self.cooldown_ok(ticker):
            return False, f"{ticker} is in cooldown after a recent close"
        return True, None
