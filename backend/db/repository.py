"""Repository layer for database access."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.db.engine import get_db
from backend.db.models import (
    AutonomousEventDB,
    BacktestRunDB,
    OHLCVCacheDB,
    OrderDB,
    PositionDB,
    ScanHistoryDB,
)


class PositionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        position_id = payload.get("id") or str(__import__("uuid").uuid4())
        now = datetime.now(timezone.utc).isoformat()
        quantity = payload.get("quantity")
        record = PositionDB(
            id=position_id,
            ticker=payload["ticker"],
            interval=payload["interval"],
            direction=payload["direction"],
            entry_price=float(payload["entry_price"]),
            target1=float(payload["target1"]),
            invalidation=float(payload["invalidation"]),
            entry_date=now,
            status="open",
            unrealized_return_pct=0.0,
            source=payload.get("source", "manual"),
            quantity=quantity,
            order_id=payload.get("order_id"),
            initial_quantity=payload.get("initial_quantity", quantity),
            realized_pnl=0.0,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def reduce_quantity(
        self,
        position_id: str,
        filled_qty: int,
        fill_price: float,
        exit_reason: str = "partial_exit",
    ) -> dict[str, Any] | None:
        """Apply a confirmed (partial or final) exit fill to an open position.

        Adds this fill's realized P&L to the running total, decrements the
        remaining quantity, and only marks the position fully 'closed' once
        the remaining quantity reaches zero -- using the cumulative realized
        P&L (not a single exit price) for the final return_pct, since a
        position may have been closed across several fills at different
        prices.
        """
        record = self.db.query(PositionDB).filter(PositionDB.id == position_id).first()
        if not record or record.status != "open":
            return None

        current_qty = int(record.quantity or 0)
        filled_qty = min(int(filled_qty), current_qty) if current_qty > 0 else int(filled_qty)
        sign = 1 if record.direction == "bullish" else -1
        pnl_delta = sign * (fill_price - record.entry_price) * filled_qty
        record.realized_pnl = float(record.realized_pnl or 0.0) + pnl_delta
        record.quantity = max(0, current_qty - filled_qty)

        if record.quantity <= 0:
            initial_qty = record.initial_quantity or (current_qty + filled_qty) or 1
            cost_basis = record.entry_price * initial_qty
            record.status = "closed"
            record.exit_price = float(fill_price)
            record.exit_date = datetime.now(timezone.utc).isoformat()
            record.exit_reason = exit_reason
            record.return_pct = (
                round((record.realized_pnl / cost_basis) * 100, 2) if cost_basis else 0.0
            )
            record.unrealized_return_pct = 0.0

        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def get(self, position_id: str) -> dict[str, Any] | None:
        record = self.db.query(PositionDB).filter(PositionDB.id == position_id).first()
        return self._to_dict(record) if record else None

    def list_all(self) -> list[dict[str, Any]]:
        records = self.db.query(PositionDB).order_by(PositionDB.created_at.desc()).all()
        return [self._to_dict(r) for r in records]

    def count_open(self, source: str | None = None) -> int:
        q = self.db.query(PositionDB).filter(PositionDB.status == "open")
        if source:
            q = q.filter(PositionDB.source == source)
        return q.count()

    def get_open_for_ticker(self, ticker: str, source: str | None = None) -> dict[str, Any] | None:
        q = self.db.query(PositionDB).filter(
            PositionDB.ticker == ticker, PositionDB.status == "open"
        )
        if source:
            q = q.filter(PositionDB.source == source)
        record = q.first()
        return self._to_dict(record) if record else None

    def close(self, position_id: str, exit_price: float, exit_reason: str) -> dict[str, Any] | None:
        record = self.db.query(PositionDB).filter(PositionDB.id == position_id).first()
        if not record or record.status != "open":
            return None
        record.status = "closed"
        record.exit_price = float(exit_price)
        record.exit_date = datetime.now(timezone.utc).isoformat()
        record.exit_reason = exit_reason
        record.return_pct = self._compute_return(record.direction, record.entry_price, exit_price)
        record.unrealized_return_pct = 0.0
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def update_unrealized(self, position_id: str, current_price: float) -> dict[str, Any] | None:
        record = self.db.query(PositionDB).filter(PositionDB.id == position_id).first()
        if not record or record.status != "open":
            return None
        record.unrealized_return_pct = self._compute_return(record.direction, record.entry_price, current_price)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    @staticmethod
    def _compute_return(direction: str, entry_price: float, exit_price: float) -> float:
        if entry_price is None or exit_price is None:
            return 0.0
        if direction == "bullish":
            return round(((exit_price - entry_price) / entry_price) * 100, 2)
        return round(((entry_price - exit_price) / entry_price) * 100, 2)

    @staticmethod
    def _to_dict(record: PositionDB) -> dict[str, Any]:
        return {
            "id": record.id,
            "ticker": record.ticker,
            "interval": record.interval,
            "direction": record.direction,
            "entry_price": record.entry_price,
            "target1": record.target1,
            "invalidation": record.invalidation,
            "entry_date": record.entry_date,
            "status": record.status,
            "exit_price": record.exit_price,
            "exit_date": record.exit_date,
            "exit_reason": record.exit_reason,
            "return_pct": record.return_pct,
            "unrealized_return_pct": record.unrealized_return_pct,
            "source": record.source,
            "quantity": record.quantity,
            "order_id": record.order_id,
            "initial_quantity": record.initial_quantity,
            "realized_pnl": record.realized_pnl,
        }


class OrderRepository:
    """Persistence for the broker order lifecycle (see backend/orders/order_manager.py).

    Deliberately dumb: this layer only reads/writes rows. All state-machine
    logic (what transition is valid, how to normalize a broker response)
    lives in OrderManager so it can be unit tested without a database.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        order_id = payload.get("id") or str(__import__("uuid").uuid4())
        record = OrderDB(
            id=order_id,
            decision_id=payload["decision_id"],
            order_reference_id=payload.get("order_reference_id"),
            broker_order_id=payload.get("broker_order_id"),
            ticker=payload["ticker"],
            exchange=payload["exchange"],
            segment=payload.get("segment", "CASH"),
            side=payload["side"],
            order_type=payload["order_type"],
            product=payload["product"],
            requested_quantity=int(payload["requested_quantity"]),
            filled_quantity=int(payload.get("filled_quantity", 0)),
            remaining_quantity=int(payload.get("remaining_quantity", payload["requested_quantity"])),
            requested_price=payload.get("requested_price"),
            average_fill_price=payload.get("average_fill_price"),
            status=payload.get("status", "CREATED"),
            mode=payload.get("mode", "paper"),
            setup_reference=json.dumps(payload["setup_reference"]) if payload.get("setup_reference") else None,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def get(self, order_id: str) -> dict[str, Any] | None:
        record = self.db.query(OrderDB).filter(OrderDB.id == order_id).first()
        return self._to_dict(record) if record else None

    def get_by_reference(self, order_reference_id: str) -> dict[str, Any] | None:
        record = (
            self.db.query(OrderDB)
            .filter(OrderDB.order_reference_id == order_reference_id)
            .first()
        )
        return self._to_dict(record) if record else None

    def get_open_for_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Any order for this decision that is not yet in a terminal state --
        used as the duplicate-submission guard before creating a new order.
        """
        record = (
            self.db.query(OrderDB)
            .filter(OrderDB.decision_id == decision_id, OrderDB.status.in_(_NON_TERMINAL_STATES))
            .first()
        )
        return self._to_dict(record) if record else None

    def get_in_flight_for_ticker_side(self, ticker: str, side: str) -> dict[str, Any] | None:
        record = (
            self.db.query(OrderDB)
            .filter(
                OrderDB.ticker == ticker,
                OrderDB.side == side,
                OrderDB.status.in_(_NON_TERMINAL_STATES),
            )
            .first()
        )
        return self._to_dict(record) if record else None

    def list_non_terminal(self) -> list[dict[str, Any]]:
        records = self.db.query(OrderDB).filter(OrderDB.status.in_(_NON_TERMINAL_STATES)).all()
        return [self._to_dict(r) for r in records]

    def list_recent(self, limit: int = 50, ticker: str | None = None) -> list[dict[str, Any]]:
        q = self.db.query(OrderDB)
        if ticker:
            q = q.filter(OrderDB.ticker == ticker)
        records = q.order_by(OrderDB.created_at.desc()).limit(limit).all()
        return [self._to_dict(r) for r in records]

    def update(self, order_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        record = self.db.query(OrderDB).filter(OrderDB.id == order_id).first()
        if not record:
            return None
        for key, value in changes.items():
            if key == "setup_reference" and value is not None:
                value = json.dumps(value)
            setattr(record, key, value)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    @staticmethod
    def _to_dict(record: OrderDB) -> dict[str, Any]:
        return {
            "id": record.id,
            "decision_id": record.decision_id,
            "order_reference_id": record.order_reference_id,
            "broker_order_id": record.broker_order_id,
            "ticker": record.ticker,
            "exchange": record.exchange,
            "segment": record.segment,
            "side": record.side,
            "order_type": record.order_type,
            "product": record.product,
            "requested_quantity": record.requested_quantity,
            "filled_quantity": record.filled_quantity,
            "remaining_quantity": record.remaining_quantity,
            "requested_price": record.requested_price,
            "average_fill_price": record.average_fill_price,
            "status": record.status,
            "error_category": record.error_category,
            "error_message": record.error_message,
            "mode": record.mode,
            "setup_reference": json.loads(record.setup_reference) if record.setup_reference else None,
            "position_id": record.position_id,
            "created_at": record.created_at,
            "submitted_at": record.submitted_at,
            "last_checked_at": record.last_checked_at,
            "terminal_at": record.terminal_at,
            "updated_at": record.updated_at,
        }


_NON_TERMINAL_STATES = (
    "CREATED",
    "SUBMITTING",
    "SUBMITTED",
    "PENDING",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "UNKNOWN",
)


class BacktestRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = BacktestRunDB(
            id=payload["id"],
            ticker=payload["ticker"],
            interval=payload["interval"],
            parameters=json.dumps(payload["parameters"]),
            metrics=json.dumps(payload["metrics"]),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        records = (
            self.db.query(BacktestRunDB)
            .order_by(BacktestRunDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [self._to_dict(r) for r in records]

    @staticmethod
    def _to_dict(record: BacktestRunDB) -> dict[str, Any]:
        return {
            "id": record.id,
            "ticker": record.ticker,
            "interval": record.interval,
            "parameters": json.loads(record.parameters),
            "metrics": json.loads(record.metrics),
            "created_at": record.created_at,
        }


class OHLCVCacheRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, ticker: str, interval: str) -> dict[str, Any] | None:
        record = (
            self.db.query(OHLCVCacheDB)
            .filter(OHLCVCacheDB.ticker == ticker, OHLCVCacheDB.interval == interval)
            .order_by(OHLCVCacheDB.fetched_at.desc())
            .first()
        )
        return self._to_dict(record) if record else None

    def put(self, ticker: str, interval: str, data_json: str, source: str) -> dict[str, Any]:
        from datetime import datetime, timezone
        record = OHLCVCacheDB(
            ticker=ticker,
            interval=interval,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            data_json=data_json,
            source=source,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def list_stale(self, max_age_seconds: int) -> list[dict[str, Any]]:
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        cutoff_str = cutoff.isoformat()
        records = (
            self.db.query(OHLCVCacheDB)
            .filter(OHLCVCacheDB.fetched_at < cutoff_str)
            .all()
        )
        return [self._to_dict(r) for r in records]

    @staticmethod
    def _to_dict(record: OHLCVCacheDB) -> dict[str, Any]:
        return {
            "id": record.id,
            "ticker": record.ticker,
            "interval": record.interval,
            "fetched_at": record.fetched_at,
            "data_json": record.data_json,
            "source": record.source,
        }


class ScanHistoryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = ScanHistoryDB(
            scanned_at=payload["scanned_at"],
            results_json=json.dumps(payload["results"]),
            skipped_json=json.dumps(payload.get("skipped", [])),
            count=len(payload["results"]),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        records = (
            self.db.query(ScanHistoryDB)
            .order_by(ScanHistoryDB.scanned_at.desc())
            .limit(limit)
            .all()
        )
        return [self._to_dict(r) for r in records]

    @staticmethod
    def _to_dict(record: ScanHistoryDB) -> dict[str, Any]:
        return {
            "id": record.id,
            "scanned_at": record.scanned_at,
            "results": json.loads(record.results_json),
            "skipped": json.loads(record.skipped_json),
            "count": record.count,
        }


class AutonomousEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = AutonomousEventDB(
            ticker=payload["ticker"],
            event_type=payload["event_type"],
            price=payload.get("price"),
            quantity=payload.get("quantity"),
            buy_probability=payload.get("buy_probability"),
            sell_probability=payload.get("sell_probability"),
            mode=payload.get("mode", "paper"),
            order_id=payload.get("order_id"),
            reason=payload.get("reason"),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_dict(record)

    def list_recent(self, limit: int = 100, ticker: str | None = None) -> list[dict[str, Any]]:
        q = self.db.query(AutonomousEventDB)
        if ticker:
            q = q.filter(AutonomousEventDB.ticker == ticker)
        records = q.order_by(AutonomousEventDB.id.desc()).limit(limit).all()
        return [self._to_dict(r) for r in records]

    @staticmethod
    def _to_dict(record: AutonomousEventDB) -> dict[str, Any]:
        return {
            "id": record.id,
            "ts": record.ts,
            "ticker": record.ticker,
            "event_type": record.event_type,
            "price": record.price,
            "quantity": record.quantity,
            "buy_probability": record.buy_probability,
            "sell_probability": record.sell_probability,
            "mode": record.mode,
            "order_id": record.order_id,
            "reason": record.reason,
        }
