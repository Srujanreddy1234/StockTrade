"""Transaction-cost model for NSE intraday (MIS) equity trades.

These rates are APPROXIMATE, based on publicly documented NSE/SEBI/discount-
broker intraday equity charges at the time this was written. They are NOT
pulled live from Groww's tariff sheet and will drift out of date as rates
change. Treat absolute rupee numbers from this model as indicative, not
exact -- but relative comparisons (strategy A costs more than strategy B)
remain valid even if the exact rates are slightly off. Verify against your
actual Groww contract note before trusting absolute net P&L figures.

A backtest that ignores these costs reports GROSS profit, which can look
profitable while actually losing money net of costs -- see Section 60/61
of the specification this module was built against.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    # Discount-broker flat fee per executed order (buy and sell each count
    # as one order), capped at a percentage of turnover for very large
    # orders (irrelevant at this bot's capital scale, included for
    # correctness). Groww charges ~Rs 20 flat per executed intraday equity
    # order; adjust via brokerage_flat/brokerage_pct if your plan differs.
    brokerage_flat: float = 20.0
    brokerage_pct: float = 0.0003  # 0.03%

    # Securities Transaction Tax: intraday equity STT applies on the SELL
    # leg only (unlike delivery, which taxes both legs).
    stt_sell_pct: float = 0.00025  # 0.025%

    # NSE transaction charges, both legs.
    exchange_txn_pct: float = 0.0000297  # ~0.00297%

    # SEBI turnover fee, both legs.
    sebi_pct: float = 0.000001  # Rs 10 per crore

    # Stamp duty, BUY leg only (state-average approximation).
    stamp_duty_buy_pct: float = 0.00003  # 0.003%

    # GST applies on (brokerage + exchange txn charges + SEBI charges).
    gst_pct: float = 0.18

    def _brokerage(self, turnover: float) -> float:
        return min(self.brokerage_flat, turnover * self.brokerage_pct) if turnover > 0 else 0.0

    def leg_cost(self, turnover: float, side: str) -> float:
        """Total regulatory + broker cost for ONE leg (buy or sell) of a
        trade, given that leg's turnover (price * quantity)."""
        if turnover <= 0:
            return 0.0
        brokerage = self._brokerage(turnover)
        exchange_txn = turnover * self.exchange_txn_pct
        sebi = turnover * self.sebi_pct
        gst = self.gst_pct * (brokerage + exchange_txn + sebi)
        stt = turnover * self.stt_sell_pct if side == "SELL" else 0.0
        stamp_duty = turnover * self.stamp_duty_buy_pct if side == "BUY" else 0.0
        return brokerage + exchange_txn + sebi + gst + stt + stamp_duty

    def round_trip_cost(self, buy_turnover: float, sell_turnover: float) -> float:
        return self.leg_cost(buy_turnover, "BUY") + self.leg_cost(sell_turnover, "SELL")


DEFAULT_COST_MODEL = CostModel()


@dataclass(frozen=True)
class SlippageModel:
    """Simple fixed-bps slippage, applied against the bot (worse price on
    both entry and exit). Real slippage depends on order size vs available
    liquidity at that moment, which this model does not attempt to
    simulate -- it is a conservative flat assumption, not a market-depth
    simulation.
    """

    entry_bps: float = 5.0  # 0.05%
    exit_bps: float = 5.0

    def entry_price(self, reference_price: float) -> float:
        return reference_price * (1 + self.entry_bps / 10_000.0)

    def exit_price(self, reference_price: float) -> float:
        return reference_price * (1 - self.exit_bps / 10_000.0)


DEFAULT_SLIPPAGE_MODEL = SlippageModel()
