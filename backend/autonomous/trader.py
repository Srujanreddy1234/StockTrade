"""The autonomous trading loop.

Two clocks run side by side per ticker:

1. A ~1-second tick clock that pulls the latest traded price, feeds it into
   the O(1) incremental statistics (backend/autonomous/tick_stats.py), and
   recomputes a buy/sell probability (backend/autonomous/probability_engine.py).
   This is what makes the bot feel "live" -- it reacts within a second of a
   price move.
2. A slower pipeline-refresh clock (default 60s) that re-runs the existing,
   heavier candle/indicator/scoring/risk pipeline (backend/pipeline.py) to
   get structural entry/exit levels (support, resistance, target1,
   invalidation) and a status (ENTRY/WATCH/NO TRADE). Running this every tick
   would be wasteful and pointless -- those levels only change when a new
   candle forms.

A BUY only happens when both clocks agree: the slow pipeline says there is a
real bullish structural setup, AND the fast tick engine says price is
currently near the bottom of its recent range with oversold/turning
momentum. A SELL happens when price hits the structural target/invalidation
level, OR the fast tick engine's sell probability crosses its threshold
(momentum stalling near a local high) -- whichever comes first, since price
can move through those levels within a single pipeline-refresh window.

Every decision (buy, sell, or a skip caused by a risk guardrail) is written
to the autonomous_events table for audit purposes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from backend.autonomous.config import AutonomousConfig, load_config
from backend.autonomous.execution import ExecutionEngine
from backend.autonomous.market_hours import is_market_open
from backend.autonomous.probability_engine import ProbabilitySignal, score as score_signal
from backend.autonomous.risk_manager import RiskManager
from backend.autonomous.tick_stats import TickerTickState, new_state
from backend.data_engine.loader import load_from_yfinance
from backend.db.engine import SessionLocal
from backend.db.repository import AutonomousEventRepository, OrderRepository
from backend.groww.auth import GrowwAuthError
from backend.groww.client import GrowwClientError, get_client
from backend.orders.order_manager import CANCELLED, FAILED, FILLED, PARTIALLY_FILLED, REJECTED
from backend.orders.protective_orders import create_protective_oco
from backend.pipeline import run_pipeline
from backend.positions.position_store import position_store
from backend.reconciliation.reconciliation_service import ReconciliationService

_ORDER_TERMINAL_STATES = (FILLED, PARTIALLY_FILLED, REJECTED, CANCELLED, FAILED)

logger = logging.getLogger("autonomous.trader")


@dataclass
class PipelineSnapshot:
    status: str | None = None
    direction: str | None = None
    target1: float | None = None
    invalidation: float | None = None
    confluence_score: float | None = None
    confluence_reasons: list[str] = field(default_factory=list)
    refreshed_at: float = 0.0


def _to_yfinance_ticker(ticker: str, exchange: str) -> str:
    if "." in ticker:
        return ticker
    suffix = {"NSE": ".NS", "BSE": ".BO"}.get(exchange.upper(), ".NS")
    return f"{ticker}{suffix}"


def log_event(**payload) -> None:
    db = SessionLocal()
    try:
        AutonomousEventRepository(db).create(payload)
    except Exception:
        logger.exception("Failed to write autonomous event")
    finally:
        db.close()


class AutonomousTrader:
    def __init__(self, config: AutonomousConfig | None = None) -> None:
        self.config = config or load_config()
        self.risk = RiskManager(self.config)
        self.execution = ExecutionEngine(self.config)
        self.tick_states: dict[str, TickerTickState] = {
            t: new_state(self.config.rolling_window) for t in self.config.watchlist
        }
        self.pipeline_cache: dict[str, PipelineSnapshot] = {
            t: PipelineSnapshot() for t in self.config.watchlist
        }
        self._last_observed_at: dict[str, float] = {t: 0.0 for t in self.config.watchlist}
        self._last_price_meta: dict[str, tuple[float, str]] = {}
        self.reconciliation = ReconciliationService(
            get_client(), price_tolerance_pct=self.config.reconciliation_price_tolerance_pct
        )
        self._last_reconciliation_at = 0.0
        self._critical_mismatch_tickers: set[str] = set()
        self._stop = asyncio.Event()
        logger.info(
            "Autonomous trader initialized in %s mode, watchlist=%s",
            self.execution.mode,
            self.config.watchlist,
        )

    def stop(self) -> None:
        self._stop.set()

    def _fetch_ltp_sync(self, ticker: str) -> tuple[float, str] | None:
        """Returns (price, source) where source is "groww_ltp" (true live
        broker quote) or "yfinance_fallback" (best-effort, can be minute-
        delayed) -- the distinction matters for stale-data protection below,
        which refuses to let a LIVE order rely on the delayed fallback.
        """
        try:
            client = get_client()
            price = client.get_ltp(ticker, exchange=self.config.exchange)
            if price and price > 0:
                return float(price), "groww_ltp"
        except (GrowwAuthError, GrowwClientError):
            pass
        except Exception:
            logger.exception("Unexpected error fetching LTP for %s via Groww", ticker)
        # Fallback: yfinance, best-effort, minute-delayed.
        try:
            df = load_from_yfinance(
                ticker=_to_yfinance_ticker(ticker, self.config.exchange),
                period="1d",
                interval="1m",
            )
            if not df.empty:
                return float(df["close"].iloc[-1]), "yfinance_fallback"
        except Exception:
            logger.exception("Fallback yfinance LTP fetch failed for %s", ticker)
        return None

    def _market_data_fresh_for_live_order(self, ticker: str) -> tuple[bool, str]:
        """Only enforced for LIVE order submission (see _maybe_enter/_maybe_exit).
        Paper mode intentionally tolerates the yfinance fallback -- it's a
        simulation, not a real order.
        """
        meta = self._last_price_meta.get(ticker)
        if meta is None:
            return False, "no market data observed yet for this ticker"
        observed_at, source = meta
        age = time.time() - observed_at
        if source != "groww_ltp":
            return False, f"latest price came from {source}, not a live broker quote"
        if age > self.config.max_market_data_age_seconds:
            return False, f"latest live quote is {age:.1f}s old (max {self.config.max_market_data_age_seconds}s)"
        return True, "fresh"

    def _pre_live_submission_checks(
        self, ticker: str, signal_price: float, signal_quantity: int
    ) -> tuple[bool, str | None, float, int]:
        """Runs immediately before a LIVE order submission only (never in
        paper mode). Three independent gates, all must pass:

        1. Market data freshness -- refuses a delayed/stale quote.
        2. Price deviation -- re-fetches the current price and aborts if it
           has moved beyond tolerance since the signal was evaluated
           (protects against a fast-moving stock gapping past the level the
           setup/risk engines validated).
        3. Margin re-validation -- refreshes available margin (the loop-level
           value can be up to tick_interval_seconds stale) and re-sizes the
           position against it rather than trusting the earlier snapshot.

        Returns (ok, reason_if_blocked, verified_price, verified_quantity).
        """
        fresh, reason = self._market_data_fresh_for_live_order(ticker)
        if not fresh:
            return False, f"stale market data ({reason})", signal_price, signal_quantity

        fetched = self._fetch_ltp_sync(ticker)
        if fetched is None:
            return False, "could not verify current price immediately before submission", signal_price, signal_quantity
        current_price, current_source = fetched
        if current_source != "groww_ltp":
            return False, "verification quote came from the delayed fallback, not a live broker quote", signal_price, signal_quantity

        deviation_pct = abs(current_price - signal_price) / signal_price * 100 if signal_price else 0.0
        if deviation_pct > self.config.max_entry_price_deviation_pct:
            return (
                False,
                f"price moved {deviation_pct:.2f}% since signal (max {self.config.max_entry_price_deviation_pct}%)",
                current_price, signal_quantity,
            )

        try:
            fresh_margin = self.execution.get_available_margin()
        except Exception as exc:
            return False, f"could not refresh margin before submission: {exc}", current_price, signal_quantity

        verified_quantity = self.risk.size_position(fresh_margin, current_price)
        if verified_quantity <= 0:
            return False, "insufficient margin on re-check immediately before submission", current_price, 0

        return True, None, current_price, min(signal_quantity, verified_quantity)

    def _refresh_pipeline_sync(self, ticker: str) -> PipelineSnapshot:
        try:
            df = load_from_yfinance(
                ticker=_to_yfinance_ticker(ticker, self.config.exchange),
                period=self.config.candle_period,
                interval=self.config.candle_interval,
            )
            if df.empty:
                return self.pipeline_cache[ticker]
            df = run_pipeline(df)
            last = df.iloc[-1]
            # Use the confluence engine's verdict (Step 15), not the raw
            # scoring_engine status/direction directly: confluence can only
            # confirm or downgrade the base setup after checking market
            # structure, S/R zones, breakouts, pullback/reversal, volume and
            # VWAP against it, so an ENTRY here has independent corroboration
            # from those engines rather than resting on the pattern+trend
            # score alone.
            return PipelineSnapshot(
                status=str(last.get("confluence_status")),
                direction=(last.get("confluence_direction") or None),
                target1=float(last["target1"]) if not _isnan(last.get("target1")) else None,
                invalidation=float(last["invalidation"]) if not _isnan(last.get("invalidation")) else None,
                confluence_score=(
                    float(last["confluence_score"]) if not _isnan(last.get("confluence_score")) else None
                ),
                confluence_reasons=list(last.get("confluence_reasons") or []),
                refreshed_at=time.time(),
            )
        except Exception:
            logger.exception("Pipeline refresh failed for %s", ticker)
            return self.pipeline_cache[ticker]

    async def _tick_one(self, ticker: str, available_margin: float) -> None:
        fetched = await asyncio.to_thread(self._fetch_ltp_sync, ticker)
        if fetched is None:
            return
        price, price_source = fetched
        if price is None or price <= 0:
            return
        self._last_price_meta[ticker] = (time.time(), price_source)

        state = self.tick_states[ticker]
        state.push(price)
        signal: ProbabilitySignal = score_signal(state)

        snapshot = self.pipeline_cache[ticker]
        if time.time() - snapshot.refreshed_at > self.config.pipeline_refresh_seconds:
            snapshot = await asyncio.to_thread(self._refresh_pipeline_sync, ticker)
            self.pipeline_cache[ticker] = snapshot

        now = time.time()
        if signal.ready and now - self._last_observed_at[ticker] > self.config.observe_log_seconds:
            self._last_observed_at[ticker] = now
            log_event(
                ticker=ticker,
                event_type="observe",
                price=price,
                buy_probability=signal.buy_probability,
                sell_probability=signal.sell_probability,
                mode=self.execution.mode,
                reason=(
                    f"structural={snapshot.status}/{snapshot.direction} "
                    f"(confluence={snapshot.confluence_score}, {'; '.join(snapshot.confluence_reasons) or 'no corroboration'}), "
                    f"range_position={signal.range_position}, rsi={signal.rsi:.1f}, "
                    f"target1={snapshot.target1}, invalidation={snapshot.invalidation}"
                ),
            )

        open_position = position_store.get_open_for_ticker(ticker, source="autonomous")

        if open_position:
            await self._maybe_exit(ticker, price, signal, open_position)
        elif signal.ready:
            await self._maybe_enter(ticker, price, signal, snapshot, available_margin)

    async def _maybe_enter(
        self,
        ticker: str,
        price: float,
        signal: ProbabilitySignal,
        snapshot: PipelineSnapshot,
        available_margin: float,
    ) -> None:
        structural_ok = (
            snapshot.status in ("ENTRY", "WATCH")
            and snapshot.direction == "bullish"
            and snapshot.target1 is not None
            and snapshot.invalidation is not None
            and snapshot.invalidation < price < snapshot.target1
        )
        if not structural_ok or signal.buy_probability < self.config.buy_probability_threshold:
            return

        # Persisted duplicate-order guard: survives a process restart because
        # it queries the orders table, not in-memory state. A ticker with an
        # already-working BUY (any non-terminal state, including one left
        # UNKNOWN by a lost response) is never given a second one.
        if self.execution.has_in_flight_order(ticker, "BUY"):
            return

        # A critical local-vs-broker mismatch on this ticker (local position
        # the broker doesn't show, a broker position we don't track, or a
        # quantity disagreement) means StockTrade's view of what it already
        # holds here cannot be trusted -- refuse a NEW entry until a human
        # reviews it. Existing positions are still monitored for exits
        # regardless (see _tick_one), so this never stops a real position
        # from being managed, only from being added to.
        if ticker in self._critical_mismatch_tickers:
            log_event(
                ticker=ticker, event_type="skip", price=price, mode=self.execution.mode,
                reason="blocked: unresolved broker reconciliation mismatch on this ticker",
            )
            return

        open_count = position_store.count_open(source="autonomous")
        can_open, reason = self.risk.can_open_new_position(ticker, open_count)
        if not can_open:
            log_event(
                ticker=ticker,
                event_type="skip",
                price=price,
                buy_probability=signal.buy_probability,
                sell_probability=signal.sell_probability,
                mode=self.execution.mode,
                reason=reason,
            )
            return

        quantity = self.risk.size_position(available_margin, price)
        if quantity <= 0:
            return

        if self.execution.mode == "live":
            ok, reason, price, quantity = await asyncio.to_thread(
                self._pre_live_submission_checks, ticker, price, quantity
            )
            if not ok:
                log_event(
                    ticker=ticker, event_type="skip", price=price, mode=self.execution.mode,
                    reason=f"blocked before live submission: {reason}",
                )
                return

        # decision_id ties this specific structural setup (identified by
        # which pipeline refresh produced it) to its order, so a retried
        # call for the SAME setup is idempotent (create_order returns the
        # existing non-terminal order instead of submitting a second one).
        decision_id = f"{ticker}:BUY:{int(snapshot.refreshed_at)}"
        setup_reference = {
            "confluence_status": snapshot.status,
            "confluence_score": snapshot.confluence_score,
            "confluence_reasons": snapshot.confluence_reasons,
            "target1": snapshot.target1,
            "invalidation": snapshot.invalidation,
            "interval": self.config.candle_interval,
        }

        try:
            order = await asyncio.to_thread(
                self.execution.submit_and_confirm,
                decision_id, ticker, "BUY", quantity, price, setup_reference,
            )
        except Exception as exc:
            logger.exception("Buy order flow failed for %s", ticker)
            log_event(
                ticker=ticker, event_type="error", price=price, mode=self.execution.mode, reason=str(exc)
            )
            return

        log_event(
            ticker=ticker,
            event_type="order_submitted",
            price=price,
            buy_probability=signal.buy_probability,
            sell_probability=signal.sell_probability,
            mode=order["mode"],
            order_id=order.get("broker_order_id") or order["id"],
            reason=(
                f"status={order['status']}, structural={snapshot.status}/{snapshot.direction}, "
                f"confluence_score={snapshot.confluence_score}, "
                f"confirmed_by=[{'; '.join(snapshot.confluence_reasons)}]"
            ),
        )
        self._finalize_order(order)

    async def _maybe_exit(self, ticker: str, price: float, signal: ProbabilitySignal, position: dict) -> None:
        reason = None
        if price >= position["target1"]:
            reason = "target_hit"
        elif price <= position["invalidation"]:
            reason = "invalidated"
        elif signal.ready and signal.sell_probability >= self.config.sell_probability_threshold:
            reason = "sell_probability_threshold"

        if reason is None:
            return

        quantity = int(position.get("quantity") or 0)
        if quantity <= 0:
            return

        if self.execution.has_in_flight_order(ticker, "SELL"):
            return

        decision_id = f"{ticker}:SELL:{position['id']}"
        setup_reference = {"reason": reason}

        try:
            order = await asyncio.to_thread(
                self.execution.submit_and_confirm,
                decision_id, ticker, "SELL", quantity, price, setup_reference,
            )
        except Exception as exc:
            logger.exception("Sell order flow failed for %s", ticker)
            log_event(
                ticker=ticker, event_type="error", price=price, mode=self.execution.mode, reason=str(exc)
            )
            return

        # Link the order to the position it is meant to exit BEFORE
        # finalizing, so a later reconciliation pass (if this order didn't
        # reach a terminal state within the bounded poll above) still knows
        # which position to reduce once it does.
        db = SessionLocal()
        try:
            order = OrderRepository(db).update(order["id"], {"position_id": position["id"]})
        finally:
            db.close()

        log_event(
            ticker=ticker,
            event_type="order_submitted",
            price=price,
            buy_probability=signal.buy_probability,
            sell_probability=signal.sell_probability,
            mode=order["mode"],
            order_id=order.get("broker_order_id") or order["id"],
            reason=f"status={order['status']}, exit_reason={reason}",
        )
        self._finalize_order(order)

    def _finalize_order(self, order: dict) -> None:
        """Apply a (possibly just-reconciled) order's confirmed fill to the
        position layer. Only ever acts on FILLED/PARTIALLY_FILLED orders
        with filled_quantity > 0 -- a bare "place_order() didn't raise" is
        never sufficient here, only a broker-confirmed fill is. Safe to call
        more than once for the same order: a BUY only creates a position the
        first time (guarded by order['position_id'] already being set), and
        a SELL is only processed while its linked position is still open.
        """
        status = order.get("status")
        ticker = order["ticker"]

        if status in (REJECTED, CANCELLED, FAILED):
            log_event(
                ticker=ticker,
                event_type=f"order_{status.lower()}",
                mode=order["mode"],
                order_id=order.get("broker_order_id") or order["id"],
                reason=order.get("error_message") or status,
            )
            return

        if status not in (FILLED, PARTIALLY_FILLED) or int(order.get("filled_quantity") or 0) <= 0:
            return

        setup_reference = order.get("setup_reference") or {}

        if order["side"] == "BUY":
            if order.get("position_id"):
                return  # already finalized
            fill_price = order.get("average_fill_price")
            if fill_price is None:
                return
            position = self.execution.open_position_record(
                ticker, order,
                setup_reference.get("target1"), setup_reference.get("invalidation"),
                setup_reference.get("interval", self.config.candle_interval),
            )
            db = SessionLocal()
            try:
                OrderRepository(db).update(order["id"], {"position_id": position["id"]})
            finally:
                db.close()
            log_event(
                ticker=ticker,
                event_type="buy",
                price=fill_price,
                quantity=order["filled_quantity"],
                mode=order["mode"],
                order_id=order.get("broker_order_id") or order["id"],
                reason=f"order {status}; confluence_score={setup_reference.get('confluence_score')}",
            )
            logger.info("BUY %s x%s @ %.2f (order %s)", ticker, order["filled_quantity"], fill_price, status)

            if self.config.use_protective_orders and order["mode"] == "live":
                self._create_protective_order(order, position, setup_reference)
            return

        # SELL
        position_id = order.get("position_id")
        if not position_id:
            return
        before = position_store.get(position_id)
        if not before or before["status"] != "open":
            return
        updated = self.execution.apply_exit_fill(position_id, order, reason=setup_reference.get("reason", "exit"))
        if updated is None:
            return
        pnl_delta = float(updated.get("realized_pnl") or 0.0) - float(before.get("realized_pnl") or 0.0)
        self.risk.record_realized_pnl(pnl_delta)
        if updated["status"] == "closed":
            self.risk.record_position_closed(ticker)
        log_event(
            ticker=ticker,
            event_type="sell",
            price=order.get("average_fill_price"),
            quantity=order["filled_quantity"],
            mode=order["mode"],
            order_id=order.get("broker_order_id") or order["id"],
            reason=f"{setup_reference.get('reason', 'exit')} (position {updated['status']}, realized_pnl_delta={pnl_delta:.2f})",
        )
        logger.info(
            "SELL %s x%s (order %s), position now %s, realized_pnl_delta=%.2f",
            ticker, order["filled_quantity"], status, updated["status"], pnl_delta,
        )

    def _run_reconciliation(self, source: str) -> None:
        """Compare local vs broker position state and update the ticker
        block-list. Only meaningful in live mode -- paper positions have no
        real broker counterpart, so this would just report every open paper
        position as LOCAL_ONLY noise.
        """
        if self.execution.mode != "live":
            return
        try:
            results = self.reconciliation.run(source)
            self._critical_mismatch_tickers = self.reconciliation.critical_tickers(results)
            if self.reconciliation.has_critical_mismatch(results):
                logger.warning(
                    "Reconciliation (%s) found a critical mismatch: %s",
                    source, [r.detail for r in results if r.classification != "MATCHED"],
                )
        except Exception:
            logger.exception("Reconciliation pass failed (%s)", source)

    def _create_protective_order(self, order: dict, position: dict, setup_reference: dict) -> None:
        """Best-effort, opt-in (AUTOTRADE_USE_PROTECTIVE_ORDERS) broker-side
        OCO covering the ACTUAL filled quantity of a just-confirmed entry.
        Failure here never undoes the entry or the position -- the position
        simply falls back to client-side-only monitoring, exactly as before
        this feature existed, and the failure is audited so a human can
        create protection manually if desired.
        """
        target1 = setup_reference.get("target1")
        invalidation = setup_reference.get("invalidation")
        if target1 is None or invalidation is None:
            return
        try:
            result = create_protective_oco(
                self.execution.order_manager.client,
                ticker=order["ticker"],
                exchange=order["exchange"],
                segment=order["segment"],
                product=order["product"],
                quantity=order["filled_quantity"],
                target_price=float(target1),
                stop_price=float(invalidation),
                exit_transaction_type="SELL",
                reference_id=order.get("order_reference_id"),
            )
            smart_order_id = result.get("smart_order_id") or result.get("id")
            log_event(
                ticker=order["ticker"], event_type="protective_order_created", mode=order["mode"],
                order_id=smart_order_id, quantity=order["filled_quantity"],
                reason=f"OCO target={target1} stop={invalidation} for position {position['id']}",
            )
        except Exception as exc:
            logger.exception("Failed to create protective OCO for %s", order["ticker"])
            log_event(
                ticker=order["ticker"], event_type="protective_order_failed", mode=order["mode"],
                reason=f"position {position['id']} remains on client-side-only monitoring: {exc}",
            )

    async def run_forever(self) -> None:
        # Startup reconciliation: before anything else, find out whether
        # what StockTrade thinks it holds actually matches Groww. A stale
        # local DB must not be trusted as-is for a fresh process start.
        await asyncio.to_thread(self._run_reconciliation, "startup")
        self._last_reconciliation_at = time.time()

        while not self._stop.is_set():
            if not is_market_open(self.config):
                await asyncio.sleep(30)
                continue

            available_margin = await asyncio.to_thread(self.execution.get_available_margin)
            self.risk.ensure_day_started(available_margin)

            if self.risk.check_kill_switch():
                logger.warning("Kill switch active: %s", self.risk.state.kill_switch_reason)
                await asyncio.sleep(self.config.tick_interval_seconds)
                continue

            if time.time() - self._last_reconciliation_at > self.config.reconciliation_interval_seconds:
                await asyncio.to_thread(self._run_reconciliation, "periodic")
                self._last_reconciliation_at = time.time()

            # Advance any order left non-terminal by a previous iteration
            # (bounded poll timeout, or a lost response marked UNKNOWN) --
            # this is what recovers state across a stuck request without
            # blocking the per-ticker tick loop below, and what makes order
            # state recoverable across a full process restart, since these
            # rows come from the database, not in-memory state.
            advanced = await asyncio.to_thread(self.execution.reconcile_pending_orders)
            for order in advanced:
                if order.get("status") in _ORDER_TERMINAL_STATES:
                    self._finalize_order(order)

            start = time.time()
            await asyncio.gather(
                *(self._tick_one(t, available_margin) for t in self.config.watchlist)
            )
            elapsed = time.time() - start
            await asyncio.sleep(max(0.0, self.config.tick_interval_seconds - elapsed))


def _isnan(value) -> bool:
    try:
        return value is None or value != value  # NaN != NaN
    except Exception:
        return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    trader = AutonomousTrader()
    try:
        asyncio.run(trader.run_forever())
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    main()
