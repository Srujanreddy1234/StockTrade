"""End-to-end demo of the trading-assistant pipeline on synthetic data."""

from __future__ import annotations

import pandas as pd

from backend.data_engine.loader import generate_synthetic
from backend.candles.candle_engine import add_candle_metrics, detect_patterns
from backend.indicators.indicator_engine import (
    add_indicators,
    find_swing_points,
    add_trend_read,
    add_support_resistance,
)
from backend.signals.scoring_engine import add_scores
from backend.risk.risk_engine import add_risk_levels
from backend.signals.explanation_engine import explain as explain_row


def run():
    df = generate_synthetic(n_candles=600, seed=42)
    df = add_candle_metrics(df)
    df = detect_patterns(df)
    df = add_indicators(df)
    df = find_swing_points(df, lookback=5)
    df = add_trend_read(df)
    df = add_support_resistance(df)
    df = add_scores(df)        # produces score/status/direction
    df = add_risk_levels(df)   # depends on scoring output; may downgrade status

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)

    print("=== TAIL (last 25 candles) ===")
    cols = [
        "close",
        "direction",
        "trend",
        "support",
        "resistance",
        "pattern",
        "score",
        "risk_reward",
        "status",
        "status_reason",
    ]
    print(df[cols].tail(25).round(2))

    print("\n=== Trend distribution ===")
    print(df["trend"].value_counts())

    print("\n=== Pattern counts ===")
    print(df["pattern"].value_counts(dropna=False))

    print("\n=== Swing point counts ===")
    print("swing_high:", int(df["swing_high"].sum()))
    print("swing_low :", int(df["swing_low"].sum()))

    actionable = df[df["status"].isin(["ENTRY", "WATCH"])]
    print("\n=== Setups still actionable after risk filter ===")
    print("count:", len(actionable))

    downgraded = df[df["status_reason"].notna()]
    print("=== Risk/reward downgrades to NO TRADE ===")
    print("count:", len(downgraded))
    if len(downgraded):
        for loc in downgraded.index[:3]:
            r = downgraded.loc[loc]
            print(f"  {loc.date()}: {r['pattern']} ({r['direction']}) "
                  f"rr={r['risk_reward']} -> {r['status_reason']}")

    # 1) Healthy example: an ENTRY/WATCH with acceptable risk/reward.
    healthy = actionable[actionable["risk_reward"].fillna(0) >= 1.5]
    if len(healthy):
        loc = healthy.index[-1]
        print(f"\n=== HEALTHY setup ({df.loc[loc, 'status']}) on {loc.date()} "
              f"-- risk/reward OK, stays actionable ===")
        _print_risk(df, loc)
    else:
        print("\nNo healthy (rr >= 1.5) ENTRY/WATCH setup in this run.")

    # 2) Downgraded example if one exists.
    if len(downgraded):
        loc = downgraded.index[-1]
        print(f"\n=== DOWNGRADED setup on {loc.date()} "
              f"-- risk/reward too low, flipped to NO TRADE ===")
        _print_risk(df, loc)
    else:
        print("\nNo risk/reward downgrade fired naturally in this synthetic run.")

    # 3) Most recent actionable setup: full structured explanation.
    if len(actionable):
        loc = actionable.index[-1]
        print(f"\n=== EXPLANATION for most recent {df.loc[loc, 'status']} "
              f"setup on {loc.date()} ===")
        import pprint
        pprint.pprint(explain_row(df, loc))


def _print_risk(df, loc):
    r = df.loc[loc]
    print(f"  close        : {r['close']:.2f}")
    print(f"  entry_zone   : [{r['entry_zone_low']:.2f}, {r['entry_zone_high']:.2f}]")
    print(f"  invalidation : {r['invalidation']:.2f}")
    print(f"  target1      : {r['target1']:.2f}")
    print(f"  target2      : {r['target2']:.2f}")
    print(f"  risk_reward  : {r['risk_reward']}")
    print(f"  status       : {r['status']}"
          + (f"  ({r['status_reason']})" if isinstance(r['status_reason'], str) else ""))


if __name__ == "__main__":
    run()
