"""Risk levels, targets, and risk/reward validation for each setup."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_risk_levels(
    df: pd.DataFrame,
    stop_atr_mult: float = 1.0,
    target1_atr_mult: float = 1.5,
    target2_atr_mult: float = 2.5,
    min_risk_reward: float = 1.5,
) -> pd.DataFrame:
    """Add entry zone, invalidation, targets, and risk/reward columns.

    Runs AFTER scoring_engine.add_scores (needs 'direction', 'status',
    'support', 'resistance'). Rows whose status is ENTRY/WATCH but whose
    risk/reward is below `min_risk_reward` are downgraded to 'NO TRADE' and
    get a 'status_reason' explaining why.
    """
    df = df.copy()
    atr = df["atr"].astype(float)
    close = df["close"].astype(float)
    direction = df["direction"]

    half = 0.25 * atr
    df["entry_zone_low"] = close - half
    df["entry_zone_high"] = close + half

    support = df["support"].astype(float)
    resistance = df["resistance"].astype(float)

    bull = direction == "bullish"
    bear = direction == "bearish"

    invalidation = pd.Series(np.nan, index=df.index, dtype=float)
    target1 = pd.Series(np.nan, index=df.index, dtype=float)
    target2 = pd.Series(np.nan, index=df.index, dtype=float)

    invalidation[bull] = support[bull] - stop_atr_mult * atr[bull]
    invalidation[bear] = resistance[bear] + stop_atr_mult * atr[bear]

    ezh = df["entry_zone_high"]
    ezl = df["entry_zone_low"]

    # Targets anchor to the real swing level (resistance for bullish, support
    # for bearish) when it sits usefully beyond entry, otherwise fall back to
    # the ATR-multiple projection.
    use_resistance = bull & (resistance > ezh + 0.5 * atr)
    use_support = bear & (support < ezl - 0.5 * atr)

    t1_bull = pd.Series(np.nan, index=df.index, dtype=float)
    t2_bull = pd.Series(np.nan, index=df.index, dtype=float)
    t1_bear = pd.Series(np.nan, index=df.index, dtype=float)
    t2_bear = pd.Series(np.nan, index=df.index, dtype=float)

    t1_bull[use_resistance] = resistance[use_resistance]
    t2_bull[use_resistance] = resistance[use_resistance] + target1_atr_mult * atr[use_resistance]
    t1_bull[~use_resistance & bull] = ezh[~use_resistance & bull] + target1_atr_mult * atr[~use_resistance & bull]
    t2_bull[~use_resistance & bull] = ezh[~use_resistance & bull] + target2_atr_mult * atr[~use_resistance & bull]

    t1_bear[use_support] = support[use_support]
    t2_bear[use_support] = support[use_support] - target1_atr_mult * atr[use_support]
    t1_bear[~use_support & bear] = ezl[~use_support & bear] - target1_atr_mult * atr[~use_support & bear]
    t2_bear[~use_support & bear] = ezl[~use_support & bear] - target2_atr_mult * atr[~use_support & bear]

    target1[bull] = t1_bull[bull]
    target2[bull] = t2_bull[bull]
    target1[bear] = t1_bear[bear]
    target2[bear] = t2_bear[bear]

    df["invalidation"] = invalidation
    df["target1"] = target1
    df["target2"] = target2

    # Risk/reward: reward per unit of risk. Bullish uses the upside to target1
    # over the distance from entry high to invalidation; bearish is mirrored.
    num_bull = (target1 - ezh).to_numpy(dtype=float)
    den_bull = (ezh - invalidation).to_numpy(dtype=float)
    num_bear = (ezl - target1).to_numpy(dtype=float)
    den_bear = (invalidation - ezl).to_numpy(dtype=float)

    rr = np.full(len(df), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        rr_bull = np.where(np.abs(den_bull) > 1e-9, num_bull / den_bull, np.nan)
        rr_bear = np.where(np.abs(den_bear) > 1e-9, num_bear / den_bear, np.nan)
    rr = np.where(~np.isnan(rr_bull), rr_bull, rr_bear)
    rr = np.where(np.isfinite(rr), rr, np.nan)
    df["risk_reward"] = np.round(rr, 1)

    # Downgrade poor risk/reward setups.
    df["status_reason"] = pd.Series([None] * len(df), index=df.index)
    downgrade = (
        df["status"].isin(["ENTRY", "WATCH"])
        & df["risk_reward"].notna()
        & (df["risk_reward"] < min_risk_reward)
    )
    if downgrade.any():
        df.loc[downgrade, "status"] = "NO TRADE"
        df.loc[downgrade, "status_reason"] = [
            f"poor risk/reward (1:{r:.1f})" for r in df.loc[downgrade, "risk_reward"]
        ]

    return df
