"""Initialize database schema on startup."""

from __future__ import annotations

from backend.db.engine import Base, engine


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
