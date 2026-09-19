"""Startup + periodic reconciliation between StockTrade's local position
state and Groww's actual broker-reported positions.

This exists because the local SQLite database was previously the ONLY
source of truth the autonomous trader ever consulted -- if a real position
existed at the broker that StockTrade didn't know about (a lost response
that actually filled, a manual trade placed outside the bot, a crash
between broker fill and local position creation), it would simply never be
seen. This module makes Groww's own get_positions() the authority for
"what is actually held" and compares it against the local `positions`
table (source="autonomous", status="open") every reconciliation cycle.

It never silently overwrites local state to match the broker (or vice
versa) -- every comparison result is recorded as an audit event, and any
CRITICAL classification (LOCAL_ONLY, BROKER_ONLY, QUANTITY_MISMATCH) is
surfaced via `has_critical_mismatch()` so the trader can refuse to open
NEW positions on that ticker until a human reviews it. Existing positions
keep being monitored for exits either way -- a mismatch should not also
strand a real position with no exit path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.db.engine import SessionLocal
from backend.db.models import PositionDB
from backend.db.repository import AutonomousEventRepository
from backend.groww.models import GrowwPosition, position_from_api

logger = logging.getLogger("reconciliation.service")

MATCHED = "MATCHED"
LOCAL_ONLY = "LOCAL_ONLY"
BROKER_ONLY = "BROKER_ONLY"
QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
PRICE_MISMATCH = "PRICE_MISMATCH"
RECONCILIATION_FAILED = "RECONCILIATION_FAILED"

# Mismatches serious enough to block new entries on that ticker until a
# human reviews it. PRICE_MISMATCH alone does not block -- a broker's
# average price can legitimately differ from the bot's recorded entry price
# for reasons that aren't safety-relevant (corporate actions, brokerage
# rounding), so it is recorded but not treated as a hard stop.
CRITICAL_CLASSIFICATIONS = {LOCAL_ONLY, BROKER_ONLY, QUANTITY_MISMATCH}


@dataclass
class ReconciliationResult:
    ticker: str
    classification: str
    local_quantity: int
    broker_quantity: int
    local_avg_price: float | None
    broker_avg_price: float | None
    detail: str


def _log_event(**payload: Any) -> None:
    db = SessionLocal()
    try:
        AutonomousEventRepository(db).create(payload)
    except Exception:
        logger.exception("Failed to write reconciliation audit event")
    finally:
        db.close()


class ReconciliationService:
    def __init__(self, client: Any, price_tolerance_pct: float = 1.0) -> None:
        self.client = client
        self.price_tolerance_pct = price_tolerance_pct

    def _broker_positions_by_ticker(self) -> dict[str, GrowwPosition]:
        raw = self.client.get_positions()
        out: dict[str, GrowwPosition] = {}
        for item in raw or []:
            pos = position_from_api(item)
            net_qty = pos.quantity + pos.overnight_quantity
            if net_qty == 0 or not pos.symbol:
                continue
            out[pos.symbol] = pos
        return out

    def _local_open_positions(self) -> dict[str, PositionDB]:
        db = SessionLocal()
        try:
            records = (
                db.query(PositionDB)
                .filter(PositionDB.status == "open", PositionDB.source == "autonomous")
                .all()
            )
            # Detach values we need before closing the session.
            return {r.ticker: (int(r.quantity or 0), float(r.entry_price)) for r in records}
        finally:
            db.close()

    def run(self, source: str = "periodic") -> list[ReconciliationResult]:
        """Run one reconciliation pass and record every result as an audit
        event. `source` is "startup" or "periodic", purely for the audit
        trail.
        """
        try:
            local_by_ticker = self._local_open_positions()
        except Exception as exc:
            logger.exception("Failed to read local positions for reconciliation")
            _log_event(
                ticker="*", event_type="reconciliation_failed", mode="reconciliation",
                reason=f"[{source}] could not read local positions: {exc}",
            )
            return [ReconciliationResult("*", RECONCILIATION_FAILED, 0, 0, None, None, str(exc))]

        try:
            broker_by_ticker = self._broker_positions_by_ticker()
        except Exception as exc:
            logger.warning("Broker reconciliation failed (%s): %s", source, exc)
            _log_event(
                ticker="*", event_type="reconciliation_failed", mode="reconciliation",
                reason=f"[{source}] could not fetch broker positions: {exc}",
            )
            # We cannot compare against a broker we can't reach -- this is
            # NOT the same as "everything matched". Callers must treat a
            # failed reconciliation conservatively (see has_critical_mismatch).
            return [ReconciliationResult("*", RECONCILIATION_FAILED, 0, 0, None, None, str(exc))]

        tickers = set(local_by_ticker) | set(broker_by_ticker)
        results: list[ReconciliationResult] = []
        for ticker in sorted(tickers):
            local = local_by_ticker.get(ticker)
            broker = broker_by_ticker.get(ticker)
            local_qty, local_price = local if local else (0, None)
            broker_qty = int(broker.quantity + broker.overnight_quantity) if broker else 0
            broker_price = broker.average_price if broker else None

            if local and not broker:
                classification = LOCAL_ONLY
                detail = f"Local open position of {local_qty} shares, but Groww reports none held."
            elif broker and not local:
                classification = BROKER_ONLY
                detail = f"Groww reports {broker_qty} shares held, but no matching local open position."
            elif local_qty != broker_qty:
                classification = QUANTITY_MISMATCH
                detail = f"Local quantity {local_qty} != broker quantity {broker_qty}."
            elif (
                local_price and broker_price
                and abs(local_price - broker_price) / local_price * 100 > self.price_tolerance_pct
            ):
                classification = PRICE_MISMATCH
                detail = (
                    f"Local entry price {local_price:.2f} vs broker average price {broker_price:.2f} "
                    f"differ by more than {self.price_tolerance_pct}%."
                )
            else:
                classification = MATCHED
                detail = "Local and broker state agree."

            result = ReconciliationResult(
                ticker=ticker, classification=classification,
                local_quantity=local_qty, broker_quantity=broker_qty,
                local_avg_price=local_price, broker_avg_price=broker_price, detail=detail,
            )
            results.append(result)
            if classification != MATCHED:
                _log_event(
                    ticker=ticker,
                    event_type=f"reconciliation_{classification.lower()}",
                    mode="reconciliation",
                    reason=f"[{source}] {detail}",
                )
        return results

    def has_critical_mismatch(self, results: list[ReconciliationResult] | None = None) -> bool:
        if results is None:
            results = self.run("check")
        return any(
            r.classification in CRITICAL_CLASSIFICATIONS or r.classification == RECONCILIATION_FAILED
            for r in results
        )

    def critical_tickers(self, results: list[ReconciliationResult]) -> set[str]:
        return {
            r.ticker for r in results
            if r.classification in CRITICAL_CLASSIFICATIONS and r.ticker != "*"
        }
