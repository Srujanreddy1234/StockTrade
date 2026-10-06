"""Historical backtesting engine.

Replays the SAME pipeline the live trader uses (backend/pipeline.run_pipeline
-> the Step-15 confluence engine) bar-by-bar in chronological order, so this
is not a separate "backtest strategy" that can silently diverge from live
behavior -- it is the live entry/exit logic (status/direction/target1/
invalidation + square-off/no-new-entries cutoffs + capital ceiling/position
limits/daily-loss kill-switch) replayed against history.

Known simplifications vs. live trading (documented, not hidden):
  - The live trader's fast tick-level buy/sell probability engine
    (backend/autonomous/probability_engine.py) has no historical tick-by-
    tick replay here; entries use the structural confluence signal only
    (status in ENTRY/WATCH, bullish, price between invalidation/target1).
    This makes the backtest a lower bound on signal selectivity, not an
    exact replay of the live decision.
  - Entries/exits execute at the NEXT bar's open after the signal bar's
    close (no look-ahead), with a flat slippage model -- not a market-depth
    simulation. See backend/backtest/costs.py.
  - Exits assume target1/invalidation are touched if the bar's high/low
    crosses them, which can slightly overstate fill quality intrabar (the
    real order might not have gotten exactly that price).
  - Data availability: yfinance only serves ~60 days of history for
    sub-daily intervals (5m/15m/etc). A genuine multi-year walk-forward
    backtest at intraday resolution needs a paid historical data provider
    or Groww's own historical-candle API (not wired up here) -- this
    engine works at whatever period/interval you can actually fetch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pandas as pd

from backend.autonomous.config import AutonomousConfig
from backend.backtest.costs import DEFAULT_COST_MODEL, DEFAULT_SLIPPAGE_MODEL, CostModel, SlippageModel
from backend.data_engine.loader import load_from_yfinance
from backend.pipeline import run_pipeline

logger = logging.getLogger("backtest.engine")
IST = ZoneInfo("Asia/Kolkata")

# Reporting-only tag (never read by the decision logic below) so every
# trade in the log can be traced to the exact entry/exit rules that
# produced it, per the audit-trail requirement.
STRATEGY_VERSION = "v1-scanner+confluence"


def _parse_hhmm(value: str) -> dt_time:
    hh, mm = value.split(":")
    return dt_time(int(hh), int(mm))


def _to_yfinance_ticker(ticker: str, exchange: str) -> str:
    if "." in ticker:
        return ticker
    suffix = {"NSE": ".NS", "BSE": ".BO"}.get(exchange.upper(), ".NS")
    return f"{ticker}{suffix}"


@dataclass
class Trade:
    ticker: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    quantity: int
    exit_reason: str
    gross_pnl: float
    costs: float
    net_pnl: float
    target1: float
    invalidation: float
    signal_score: float | None
    confluence_reasons: list[str] = field(default_factory=list)
    strategy_version: str = STRATEGY_VERSION


@dataclass
class _OpenPosition:
    ticker: str
    entry_time: pd.Timestamp
    entry_price: float
    quantity: int
    target1: float
    invalidation: float
    buy_turnover: float
    buy_cost: float
    signal_score: float | None
    confluence_reasons: list[str] = field(default_factory=list)


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    skipped_tickers: list[str] = field(default_factory=list)
    starting_capital: float = 0.0
    # Diagnostics-only, not used by the simulation loop above:
    # every bar meeting the raw structural condition (status/direction/
    # price-band), independent of portfolio gating (capital/positions/
    # cooldown/kill-switch) -- lets a diagnostic pass compare the full
    # signal population against the (smaller) set that became trades.
    raw_signals: list[dict] = field(default_factory=list)
    # ticker -> its annotated OHLC+confluence frame, indexed by timestamp,
    # kept around so a diagnostic pass can compute MFE/MAE without a
    # second network fetch.
    ticker_frames: dict[str, pd.DataFrame] = field(default_factory=dict)


def _load_and_annotate(ticker: str, config: AutonomousConfig, period: str, interval: str) -> pd.DataFrame | None:
    try:
        df = load_from_yfinance(
            ticker=_to_yfinance_ticker(ticker, config.exchange), period=period, interval=interval
        )
        if df.empty:
            return None
        df = run_pipeline(df, timeframe=interval)
    except Exception:
        logger.exception("Backtest data/pipeline load failed for %s", ticker)
        return None
    out = df[["open", "high", "low", "close", "confluence_status", "confluence_direction",
              "confluence_score", "confluence_reasons", "target1", "invalidation"]].copy()
    out["ticker"] = ticker
    out["timestamp"] = out.index
    return out.reset_index(drop=True)


def fetch_universe_frames(
    config: AutonomousConfig,
    tickers: list[str],
    period: str | None = None,
    interval: str | None = None,
) -> tuple[list[pd.DataFrame], list[str]]:
    """The expensive step (network fetch + pipeline run per ticker).

    Split out from run_backtest/simulate so a caller running multiple
    experiments against the SAME historical window (e.g. testing several
    entry-gate variants) can fetch once and re-simulate many times instead
    of re-fetching per experiment.
    """
    period = period or config.candle_period
    interval = interval or config.candle_interval
    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    for ticker in tickers:
        frame = _load_and_annotate(ticker, config, period, interval)
        if frame is None:
            skipped.append(ticker)
            continue
        frames.append(frame)
    return frames, skipped


def simulate(
    frames: list[pd.DataFrame],
    skipped: list[str],
    config: AutonomousConfig,
    cost_model: CostModel = DEFAULT_COST_MODEL,
    slippage_model: SlippageModel = DEFAULT_SLIPPAGE_MODEL,
    allowed_statuses: tuple[str, ...] = ("ENTRY", "WATCH"),
    min_score: float = 0.0,
    confirmation_bars: int = 0,
) -> BacktestResult:
    """Run the entry/exit/risk simulation against already-fetched frames.

    `allowed_statuses`, `min_score`, and `confirmation_bars` default to
    EXACTLY the current live/baseline entry gate (status in ENTRY/WATCH, no
    score minimum, fill on the very next bar) -- passing anything else is
    an explicit, opt-in experiment, never the default path run_backtest()
    takes.
    """
    no_new_entries_after = _parse_hhmm(config.no_new_entries_after)
    square_off_time = _parse_hhmm(config.square_off_time)

    result = BacktestResult(starting_capital=config.allocated_capital, skipped_tickers=skipped)
    if not frames:
        return result

    result.ticker_frames = {
        f["ticker"].iloc[0]: f.set_index("timestamp")[["open", "high", "low", "close"]]
        for f in frames
    }

    events = pd.concat(frames, ignore_index=True).sort_values("timestamp", kind="stable")
    events = events.reset_index(drop=True)

    # Diagnostics: the full raw-signal population meeting THIS experiment's
    # status/score gate, independent of portfolio-level gating (see
    # BacktestResult.raw_signals docstring).
    raw_mask = (
        events["confluence_status"].isin(list(allowed_statuses))
        & (events["confluence_direction"] == "bullish")
        & (events["invalidation"] < events["close"])
        & (events["close"] < events["target1"])
        & (events["confluence_score"] >= min_score)
    )
    result.raw_signals = events.loc[raw_mask, ["ticker", "timestamp", "confluence_score", "confluence_status"]].to_dict("records")

    open_positions: dict[str, _OpenPosition] = {}
    pending_entries: dict[str, dict] = {}  # ticker -> {target1, invalidation, signal_time}
    awaiting_confirmation: dict[str, dict] = {}  # ticker -> {count, target1, invalidation, ...}
    last_closed_at: dict[str, pd.Timestamp] = {}
    current_date = None
    realized_pnl_today = 0.0
    kill_switch_active = False
    equity = config.allocated_capital

    def _capital_in_use() -> float:
        return sum(p.entry_price * p.quantity for p in open_positions.values())

    for row in events.itertuples(index=False):
        ts = row.timestamp
        ts_ist = ts.tz_convert(IST) if ts.tzinfo is not None else ts.tz_localize(IST)
        row_date = ts_ist.date()
        if row_date != current_date:
            current_date = row_date
            realized_pnl_today = 0.0
            kill_switch_active = False

        ticker = row.ticker

        # 1) Fill any entry queued from a PREVIOUS bar's close signal, at
        # THIS bar's open -- the earliest a decision made on the prior
        # bar's close could actually have been executed.
        if ticker in pending_entries and ticker not in open_positions:
            intent = pending_entries.pop(ticker)
            fill_price = slippage_model.entry_price(row.open)
            quantity = intent["quantity"]
            if quantity > 0 and fill_price > 0:
                turnover = fill_price * quantity
                buy_cost = cost_model.leg_cost(turnover, "BUY")
                open_positions[ticker] = _OpenPosition(
                    ticker=ticker, entry_time=ts, entry_price=fill_price, quantity=quantity,
                    target1=intent["target1"], invalidation=intent["invalidation"],
                    buy_turnover=turnover, buy_cost=buy_cost, signal_score=intent.get("signal_score"),
                    confluence_reasons=intent.get("confluence_reasons") or [],
                )

        # 2) Manage an existing position: square-off, target, or
        # invalidation -- same priority order as trader._maybe_exit.
        position = open_positions.get(ticker)
        if position is not None:
            exit_price = None
            reason = None
            if ts_ist.time() >= square_off_time:
                exit_price = slippage_model.exit_price(row.open)
                reason = "square_off"
            elif row.high >= position.target1:
                exit_price = slippage_model.exit_price(position.target1)
                reason = "target_hit"
            elif row.low <= position.invalidation:
                exit_price = slippage_model.exit_price(position.invalidation)
                reason = "invalidated"

            if reason is not None:
                sell_turnover = exit_price * position.quantity
                sell_cost = cost_model.leg_cost(sell_turnover, "SELL")
                total_cost = position.buy_cost + sell_cost
                gross_pnl = sell_turnover - position.buy_turnover
                net_pnl = gross_pnl - total_cost
                result.trades.append(Trade(
                    ticker=ticker, entry_time=position.entry_time, entry_price=position.entry_price,
                    exit_time=ts, exit_price=exit_price, quantity=position.quantity,
                    exit_reason=reason, gross_pnl=gross_pnl, costs=total_cost, net_pnl=net_pnl,
                    target1=position.target1, invalidation=position.invalidation,
                    signal_score=position.signal_score, confluence_reasons=position.confluence_reasons,
                ))
                realized_pnl_today += net_pnl
                equity += net_pnl
                result.equity_curve.append((ts, equity))
                last_closed_at[ticker] = ts
                del open_positions[ticker]
                position = None

        if kill_switch_active or realized_pnl_today <= -(config.allocated_capital * config.daily_loss_limit_pct):
            kill_switch_active = True

        # 3) Look for a fresh entry signal on this bar's close, to be
        # filled on the NEXT bar this ticker appears (see step 1).
        if (
            position is None
            and ticker not in pending_entries
            and not kill_switch_active
            and ts_ist.time() < no_new_entries_after
            and len(open_positions) < config.max_open_positions
        ):
            cooldown_ok = True
            if ticker in last_closed_at:
                elapsed_min = (ts - last_closed_at[ticker]).total_seconds() / 60.0
                cooldown_ok = elapsed_min >= config.cooldown_minutes

            structural_ok = (
                cooldown_ok
                and row.confluence_status in allowed_statuses
                and row.confluence_direction == "bullish"
                and row.invalidation is not None and row.target1 is not None
                and row.invalidation < row.close < row.target1
                and row.confluence_score >= min_score
            )

            if confirmation_bars <= 0:
                if structural_ok:
                    quantity = _size_entry(config, row.close, _capital_in_use())
                    if quantity > 0:
                        pending_entries[ticker] = {
                            "target1": row.target1, "invalidation": row.invalidation,
                            "signal_time": ts, "quantity": quantity,
                            "signal_score": row.confluence_score,
                            "confluence_reasons": row.confluence_reasons,
                        }
            else:
                # D3-style confirmation entry: the signal must still hold
                # `confirmation_bars` bars later (using THAT later bar's
                # own target1/invalidation/score, not the original signal
                # bar's) before it is queued for a next-bar-open fill.
                if structural_ok:
                    awaiting = awaiting_confirmation.get(ticker)
                    if awaiting is None:
                        awaiting_confirmation[ticker] = {
                            "count": 1, "target1": row.target1, "invalidation": row.invalidation,
                            "signal_score": row.confluence_score, "confluence_reasons": row.confluence_reasons,
                        }
                    else:
                        awaiting["count"] += 1
                        awaiting["target1"] = row.target1
                        awaiting["invalidation"] = row.invalidation
                        awaiting["signal_score"] = row.confluence_score
                        awaiting["confluence_reasons"] = row.confluence_reasons
                        if awaiting["count"] >= confirmation_bars:
                            quantity = _size_entry(config, row.close, _capital_in_use())
                            if quantity > 0:
                                pending_entries[ticker] = {
                                    "target1": awaiting["target1"], "invalidation": awaiting["invalidation"],
                                    "signal_time": ts, "quantity": quantity,
                                    "signal_score": awaiting["signal_score"],
                                    "confluence_reasons": awaiting["confluence_reasons"],
                                }
                            del awaiting_confirmation[ticker]
                else:
                    awaiting_confirmation.pop(ticker, None)

    return result


def _size_entry(config: AutonomousConfig, price: float, capital_in_use: float) -> int:
    available_margin = config.allocated_capital - capital_in_use
    pct_cap = available_margin * config.max_capital_per_trade_pct
    cap = min(pct_cap, config.max_capital_per_trade_abs) if config.max_capital_per_trade_abs > 0 else pct_cap
    return max(0, int(cap // price)) if price > 0 else 0


def run_backtest(
    config: AutonomousConfig,
    tickers: list[str],
    period: str | None = None,
    interval: str | None = None,
    cost_model: CostModel = DEFAULT_COST_MODEL,
    slippage_model: SlippageModel = DEFAULT_SLIPPAGE_MODEL,
    allowed_statuses: tuple[str, ...] = ("ENTRY", "WATCH"),
    min_score: float = 0.0,
    confirmation_bars: int = 0,
) -> BacktestResult:
    """Convenience wrapper: fetch + simulate in one call, with the exact
    current live/baseline entry gate by default. Prefer calling
    fetch_universe_frames() once + simulate() multiple times when running
    several experiments against the same historical window.
    """
    frames, skipped = fetch_universe_frames(config, tickers, period, interval)
    return simulate(
        frames, skipped, config, cost_model=cost_model, slippage_model=slippage_model,
        allowed_statuses=allowed_statuses, min_score=min_score, confirmation_bars=confirmation_bars,
    )
