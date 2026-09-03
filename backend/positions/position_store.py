"""Database-backed paper-trading position store.

Preserves the original in-memory PositionStore interface while persisting
to SQLite via SQLAlchemy.
"""

from __future__ import annotations

from typing import Any

from backend.db.engine import SessionLocal
from backend.db.repository import PositionRepository


class PositionStore:
    """Database-backed store for open and closed paper positions."""

    def __init__(self) -> None:
        self._repo: PositionRepository | None = None

    def _get_repo(self) -> PositionRepository:
        if self._repo is None:
            db = SessionLocal()
            self._repo = PositionRepository(db)
        return self._repo

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._get_repo().create(payload)

    def get(self, position_id: str) -> dict[str, Any] | None:
        return self._get_repo().get(position_id)

    def list_all(self) -> list[dict[str, Any]]:
        return self._get_repo().list_all()

    def close(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str = "manual_close",
    ) -> dict[str, Any] | None:
        return self._get_repo().close(position_id, exit_price, exit_reason)

    def update_unrealized(self, position_id: str, current_price: float) -> dict[str, Any] | None:
        return self._get_repo().update_unrealized(position_id, current_price)

    def mark_closed_by_system(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str,
    ) -> dict[str, Any] | None:
        return self.close(position_id, exit_price, exit_reason)


position_store = PositionStore()
