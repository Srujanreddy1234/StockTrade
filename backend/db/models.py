"""Database models for persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, Boolean
from sqlalchemy.sql import func

from backend.db.engine import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PositionDB(Base):
    __tablename__ = "positions"

    id = Column(String, primary_key=True, index=True)
    ticker = Column(String, nullable=False, index=True)
    interval = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False)
    target1 = Column(Float, nullable=False)
    invalidation = Column(Float, nullable=False)
    entry_date = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    exit_price = Column(Float, nullable=True)
    exit_date = Column(String, nullable=True)
    exit_reason = Column(String, nullable=True)
    return_pct = Column(Float, nullable=True)
    unrealized_return_pct = Column(Float, nullable=False, default=0.0)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat(), onupdate=lambda: utcnow().isoformat())


class BacktestRunDB(Base):
    __tablename__ = "backtest_runs"

    id = Column(String, primary_key=True)
    ticker = Column(String, nullable=False, index=True)
    interval = Column(String, nullable=False)
    parameters = Column(Text, nullable=False)
    metrics = Column(Text, nullable=False)
    created_at = Column(String, default=lambda: utcnow().isoformat())


class OHLCVCacheDB(Base):
    __tablename__ = "ohlcv_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    interval = Column(String, nullable=False)
    fetched_at = Column(String, nullable=False)
    data_json = Column(Text, nullable=False)
    source = Column(String, nullable=False)
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )


class ScanHistoryDB(Base):
    __tablename__ = "scan_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scanned_at = Column(String, nullable=False)
    results_json = Column(Text, nullable=False)
    skipped_json = Column(Text, nullable=False)
    count = Column(Integer, nullable=False)
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )
