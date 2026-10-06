#!/bin/bash
# Launches the autonomous trader forced into PAPER mode, regardless of
# whatever GROWW_ALLOW_REAL_ORDERS is set to in .env. This is the ONLY
# wrapper the launchd agent (ops/launchd/com.stocktrade.papertrader.plist)
# should ever point at -- it exists specifically so a persistent
# background trading process can never accidentally end up in LIVE mode.
#
# python-dotenv's load_dotenv() (called from backend/__init__.py) does
# NOT override an already-set environment variable by default, so
# exporting GROWW_ALLOW_REAL_ORDERS=false here wins over whatever .env
# says, without ever editing .env itself.
set -euo pipefail

PROJECT_DIR="/Users/srujanreddygangireddy/stocks/StockTrade"
cd "$PROJECT_DIR"

export GROWW_ALLOW_REAL_ORDERS=false
export AUTOTRADE_STATE_PATH="$PROJECT_DIR/backtest_results/_paper_run/paper_state.json"
export AUTOTRADE_WATCHLIST_STATE_PATH="$PROJECT_DIR/backtest_results/_paper_run/paper_watchlist.json"

mkdir -p "$PROJECT_DIR/backtest_results/_paper_run"

exec /usr/bin/python3 -m backend.autonomous.trader
