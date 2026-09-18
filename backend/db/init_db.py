"""Initialize database schema on startup."""

from __future__ import annotations

from sqlalchemy import text

from backend.db.engine import Base, engine

# (table, column, DDL type) additions made to existing models after their
# tables were first created. SQLAlchemy's create_all() only creates tables
# that don't exist yet -- it never alters an existing table -- so columns
# added to a model later need an explicit ALTER TABLE here or they silently
# never show up on a database file created before the column existed.
_COLUMN_MIGRATIONS = [
    ("positions", "source", "VARCHAR NOT NULL DEFAULT 'manual'"),
    ("positions", "quantity", "INTEGER"),
    ("positions", "order_id", "VARCHAR"),
]


def _run_sqlite_migrations() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        for table, column, ddl_type in _COLUMN_MIGRATIONS:
            existing = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if existing and column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _run_sqlite_migrations()
