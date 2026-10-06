"""Default stock universe for the scanner (backend/autonomous/scanner.py).

Kept to liquid NSE F&O names on purpose: the scanner's whole job is to pick
the best few setups out of many, but a setup is only tradeable intraday if
the stock itself has enough volume/tight-enough spreads to enter and exit a
MARKET order without bad slippage. Thinly traded stocks can show a perfect
confluence score and still be a poor real fill.

Override with the AUTOTRADE_UNIVERSE env var (comma-separated tickers) to
use a different list without touching code.
"""

from __future__ import annotations

DEFAULT_UNIVERSE: list[str] = [
    # Nifty 50 + a handful of other liquid large/mid caps.
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL",
    "ITC", "KOTAKBANK", "LT", "AXISBANK", "HINDUNILVR", "BAJFINANCE",
    "MARUTI", "SUNPHARMA", "TITAN", "ASIANPAINT", "ULTRACEMCO", "NTPC",
    "POWERGRID", "ONGC", "TATAMOTORS", "TATASTEEL", "ADANIENT", "ADANIPORTS",
    "WIPRO", "HCLTECH", "TECHM", "BAJAJFINSV", "M&M", "JSWSTEEL", "COALINDIA",
    "NESTLEIND", "GRASIM", "HINDALCO", "DRREDDY", "CIPLA", "DIVISLAB",
    "EICHERMOT", "BAJAJ-AUTO", "HEROMOTOCO", "BRITANNIA", "SBILIFE",
    "HDFCLIFE", "INDUSINDBK", "BPCL", "SHRIRAMFIN", "TRENT", "LTIM",
    "APOLLOHOSP", "PIDILITIND", "DLF", "VEDL", "ZOMATO", "PNB", "BANKBARODA",
    "CANBK", "IOC", "GAIL", "SIEMENS", "HAVELLS", "TATAPOWER", "ABB",
]
