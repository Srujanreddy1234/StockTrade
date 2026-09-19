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
    # "manual" (opened via the UI/API) or "autonomous" (opened by the
    # autonomous trading loop). Defaults to "manual" for backward
    # compatibility with rows created before this column existed.
    source = Column(String, nullable=False, default="manual")
    quantity = Column(Integer, nullable=True)
    order_id = Column(String, nullable=True)
    # Quantity as originally opened, kept even as `quantity` is decremented by
    # partial exits, so a correct return_pct can be computed from cumulative
    # realized_pnl once the position is fully closed. Nullable for rows
    # created before this column existed; readers fall back to `quantity`.
    initial_quantity = Column(Integer, nullable=True)
    # Cumulative realized P&L (in price units, i.e. rupees, not %) from any
    # partial exits applied so far. Added to on each partial fill of a SELL
    # (or BUY, for a bearish position) against this position.
    realized_pnl = Column(Float, nullable=False, default=0.0)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat(), onupdate=lambda: utcnow().isoformat())


class OrderDB(Base):
    """A broker order and its full lifecycle, tracked independently of the
    position it may eventually produce. A position is only created/updated
    once an order reaches a fill-confirmed state -- see
    backend/orders/order_manager.py for the state machine.

    Terminal states: FILLED, REJECTED, CANCELLED, FAILED. Non-terminal:
    CREATED, SUBMITTING, SUBMITTED, PENDING, PARTIALLY_FILLED,
    CANCEL_PENDING, UNKNOWN.
    """

    __tablename__ = "orders"

    id = Column(String, primary_key=True, index=True)  # internal order id (uuid)
    # Identifies the single trading decision that produced this order, so a
    # retry/resubmission attempt can check "does an order for this decision
    # already exist?" instead of blindly submitting again.
    decision_id = Column(String, nullable=False, index=True)
    # Sent to Groww as order_reference_id (the SDK's own idempotency
    # parameter) so a lost-response order can be looked up by reference
    # even without knowing the broker's own order id yet.
    order_reference_id = Column(String, nullable=True, index=True)
    broker_order_id = Column(String, nullable=True, index=True)
    ticker = Column(String, nullable=False, index=True)
    exchange = Column(String, nullable=False)
    segment = Column(String, nullable=False, default="CASH")
    side = Column(String, nullable=False)  # BUY | SELL
    order_type = Column(String, nullable=False)
    product = Column(String, nullable=False)
    requested_quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, nullable=False, default=0)
    remaining_quantity = Column(Integer, nullable=False)
    requested_price = Column(Float, nullable=True)
    average_fill_price = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="CREATED", index=True)
    error_category = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    mode = Column(String, nullable=False, default="paper")  # paper | live
    # JSON blob: confluence_status/score/reasons that authorized this order,
    # for audit purposes -- never contains credentials.
    setup_reference = Column(Text, nullable=True)
    position_id = Column(String, nullable=True, index=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    submitted_at = Column(String, nullable=True)
    last_checked_at = Column(String, nullable=True)
    terminal_at = Column(String, nullable=True)
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


class AutonomousEventDB(Base):
    """Audit log for every decision the autonomous loop makes, including
    ticks it decided NOT to act on, so the full reasoning trail survives a
    restart and can be reviewed after the fact.
    """

    __tablename__ = "autonomous_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(String, nullable=False, default=lambda: utcnow().isoformat())
    ticker = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)  # buy | sell | skip | error | kill_switch
    price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=True)
    buy_probability = Column(Float, nullable=True)
    sell_probability = Column(Float, nullable=True)
    mode = Column(String, nullable=False, default="paper")  # paper | live
    order_id = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )
