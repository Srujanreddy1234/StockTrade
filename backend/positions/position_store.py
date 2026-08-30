"""In-memory store for paper-trading positions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class PositionStore:
    """Simple in-memory store for open and closed paper positions."""

    def __init__(self) -> None:
        self._positions: dict[str, dict[str, Any]] = {}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        position_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        position = {
            "id": position_id,
            "ticker": payload["ticker"],
            "interval": payload["interval"],
            "direction": payload["direction"],
            "entry_price": payload["entry_price"],
            "target1": payload["target1"],
            "invalidation": payload["invalidation"],
            "entry_date": now,
            "status": "open",
            "exit_price": None,
            "exit_date": None,
            "exit_reason": None,
            "return_pct": None,
            "unrealized_return_pct": 0.0,
        }
        self._positions[position_id] = position
        return position

    def get(self, position_id: str) -> dict[str, Any] | None:
        return self._positions.get(position_id)

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._positions.values())

    def close(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str = "manual_close",
    ) -> dict[str, Any] | None:
        position = self._positions.get(position_id)
        if not position:
            return None
        if position["status"] != "open":
            return None
        position["status"] = "closed"
        position["exit_price"] = exit_price
        position["exit_date"] = datetime.now(timezone.utc).isoformat()
        position["exit_reason"] = exit_reason
        position["return_pct"] = self._compute_return(
            position["direction"], position["entry_price"], exit_price
        )
        position["unrealized_return_pct"] = 0.0
        return position

    def update_unrealized(self, position_id: str, current_price: float) -> dict[str, Any] | None:
        position = self._positions.get(position_id)
        if not position or position["status"] != "open":
            return None
        position["unrealized_return_pct"] = self._compute_return(
            position["direction"], position["entry_price"], current_price
        )
        return position

    def mark_closed_by_system(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str,
    ) -> dict[str, Any] | None:
        return self.close(position_id, exit_price, exit_reason)

    @staticmethod
    def _compute_return(direction: str, entry_price: float, exit_price: float) -> float:
        if entry_price is None or exit_price is None:
            return 0.0
        if direction == "bullish":
            return round(((exit_price - entry_price) / entry_price) * 100, 2)
        return round(((entry_price - exit_price) / entry_price) * 100, 2)


position_store = PositionStore()
