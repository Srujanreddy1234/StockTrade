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
from backend.db.repository import AutonomousEventRepository
from backend.groww.auth import GrowwAuthError
from backend.groww.client import GrowwClientError, get_client
from backend.pipeline import run_pipeline
from backend.positions.position_store import position_store

logger = logging.getLogger("autonomous.trader")


@dataclass
class PipelineSnapshot:
    status: str | None = None
    direction: str | None = None
    target1: float | None = None
    invalidation: float | None = None
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
        self._stop = asyncio.Event()
        logger.info(
            "Autonomous trader initialized in %s mode, watchlist=%s",
            self.execution.mode,
            self.config.watchlist,
        )

    def stop(self) -> None:
        self._stop.set()

    def _fetch_ltp_sync(self, ticker: str) -> float | None:
        try:
            client = get_client()
            return client.get_ltp(ticker, exchange=self.config.exchange)
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
                return float(df["close"].iloc[-1])
        except Exception:
            logger.exception("Fallback yfinance LTP fetch failed for %s", ticker)
        return None

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
            return PipelineSnapshot(
                status=str(last.get("status")),
                direction=(last.get("direction") or None),
                target1=float(last["target1"]) if not _isnan(last.get("target1")) else None,
                invalidation=float(last["invalidation"]) if not _isnan(last.get("invalidation")) else None,
                refreshed_at=time.time(),
            )
        except Exception:
            logger.exception("Pipeline refresh failed for %s", ticker)
            return self.pipeline_cache[ticker]

    async def _tick_one(self, ticker: str, available_margin: float) -> None:
        price = await asyncio.to_thread(self._fetch_ltp_sync, ticker)
        if price is None or price <= 0:
            return

        state = self.tick_states[ticker]
        state.push(price)
        signal: ProbabilitySignal = score_signal(state)

        snapshot = self.pipeline_cache[ticker]
        if time.time() - snapshot.refreshed_at > self.config.pipeline_refresh_seconds:
            snapshot = await asyncio.to_thread(self._refresh_pipeline_sync, ticker)
            self.pipeline_cache[ticker] = snapshot

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

        try:
            fill = await asyncio.to_thread(self.execution.buy, ticker, quantity, price)
        except Exception as exc:
            logger.exception("Buy failed for %s", ticker)
            log_event(
                ticker=ticker, event_type="error", price=price, mode=self.execution.mode, reason=str(exc)
            )
            return

        self.execution.open_position_record(
            ticker, fill, snapshot.target1, snapshot.invalidation, self.config.candle_interval
        )
        log_event(
            ticker=ticker,
            event_type="buy",
            price=fill.price,
            quantity=fill.quantity,
            buy_probability=signal.buy_probability,
            sell_probability=signal.sell_probability,
            mode=fill.mode,
            order_id=fill.order_id,
            reason=f"structural={snapshot.status}/{snapshot.direction}, target1={snapshot.target1}, invalidation={snapshot.invalidation}",
        )
        logger.info("BUY %s x%s @ %.2f (buy_p=%.2f)", ticker, fill.quantity, fill.price, signal.buy_probability)

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

        try:
            fill = await asyncio.to_thread(self.execution.sell, ticker, quantity, price)
        except Exception as exc:
            logger.exception("Sell failed for %s", ticker)
            log_event(
                ticker=ticker, event_type="error", price=price, mode=self.execution.mode, reason=str(exc)
            )
            return

        self.execution.close_position_record(position["id"], fill.price, reason)
        realized_pnl = (fill.price - position["entry_price"]) * quantity
        self.risk.record_realized_pnl(realized_pnl)
        self.risk.record_position_closed(ticker)
        log_event(
            ticker=ticker,
            event_type="sell",
            price=fill.price,
            quantity=fill.quantity,
            buy_probability=signal.buy_probability,
            sell_probability=signal.sell_probability,
            mode=fill.mode,
            order_id=fill.order_id,
            reason=reason,
        )
        logger.info(
            "SELL %s x%s @ %.2f (%s), realized_pnl=%.2f", ticker, fill.quantity, fill.price, reason, realized_pnl
        )

    async def run_forever(self) -> None:
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
