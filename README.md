# Stock Trading Assistant

A local, browser-connected technical analysis and signal-rating tool for Indian stocks (NSE). It transforms raw OHLCV candle data into structured, scored trade setups (`ENTRY` / `WATCH` / `NO TRADE`) with plain-language explanations, risk management levels, and walk-forward backtesting.

> **Note:** This is a research and validation scaffold, not a live-trading system.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Pipeline Stages](#pipeline-stages)
3. [Installation](#installation)
4. [Running the Backend](#running-the-backend)
5. [Running the Frontend](#running-the-frontend)
6. [Running the Demo](#running-the-demo)
7. [Backtesting](#backtesting)
8. [API Reference](#api-reference)
9. [Frontend Features](#frontend-features)
10. [Signal Validation Status](#signal-validation-status)
11. [Caveats and Limitations](#caveats-and-limitations)
12. [Project Structure](#project-structure)

---

## Architecture

### Backend (FastAPI + Pandas/NumPy)
- **Framework:** FastAPI with CORS enabled (`allow_origins=["*"]` — dev only).
- **Data sources:** Synthetic generator, yfinance, CSV.
- **Core pipeline:** `run_pipeline(df)` in `backend/main.py` chains all engines.
- **API:** Single primary endpoint `/analyze/{source}` plus `/health`.

### Frontend (React + Vite + TypeScript)
- Minimal SPA calling the backend at `http://127.0.0.1:8000`.
- Supports **Synthetic** and **yfinance** modes.
- Renders three card states: **ENTRY** (green), **WATCH** (yellow), **NO TRADE** (gray).
- Includes a "Why?" expander, validation notes, and a recent-context history table.

---

## Pipeline Stages

The analysis pipeline runs in this order:

| Stage | Module | Outputs |
|-------|--------|---------|
| **Candle metrics** | `backend/candles/candle_engine.py` | `body`, `range`, `upper_wick`, `lower_wick`, `is_bullish`, `body_to_range` |
| **Pattern detection** | `backend/candles/candle_engine.py` | `is_doji`, `is_hammer`, `is_shooting_star`, `is_bullish_engulfing`, `is_bearish_engulfing`, `morning_star`, `evening_star` → `pattern` |
| **Indicators** | `backend/indicators/indicator_engine.py` | `ema` (20), `rsi` (14), `atr` (14), `vwap` |
| **Swing points** | `backend/indicators/indicator_engine.py` | `swing_high`, `swing_low` (rolling window ±5 candles) |
| **Trend read** | `backend/indicators/indicator_engine.py` | `trend` ∈ {`uptrend`, `downtrend`, `sideways`} based on consecutive higher highs / higher lows |
| **Support / Resistance** | `backend/indicators/indicator_engine.py` | `support`, `resistance`, `near_support`, `near_resistance` (within 1× ATR) |
| **Scoring** | `backend/signals/scoring_engine.py` | `direction` (bullish/bearish/None), `score` (0–100), `status` (ENTRY / WATCH / NO TRADE) |
| **Risk engine** | `backend/risk/risk_engine.py` | `entry_zone_low/high`, `invalidation`, `target1`, `target2`, `risk_reward`, `status_reason`; downgrades poor R:R setups to NO TRADE |

### Scoring Weights (approximate)

- **Pattern:** hammer (+15), bullish engulfing (+20), shooting star (+10), bearish engulfing (+20)
- **Trend:** agreeing trend (+25), disagreeing trend (−15), no direction in strong trend (±25)
- **Support/Resistance:** near support with bullish direction (+20), near resistance with bearish (+20), near support no direction (+20), near resistance no direction (−15)
- **Volume:** >1.5× 20-period average (+10)

### Risk/Reward Thresholds

- **Minimum risk/reward:** 1.5
- **Entry zone:** ±0.25 × ATR around close
- **Stop:** 1× ATR beyond relevant swing level
- **Target 1:** resistance (bullish) or support (bearish), or ATR projection
- **Target 2:** further ATR projection

---

## Installation

### Python (Backend)

```bash
cd /Users/srujanreddypip install -r requirements.txtgangireddy/Desktop/STOCKS/stock-trading-assistant

```

`requirements.txt` contains:
- `fastapi>=0.110`
- `uvicorn>=0.27`
- `pandas>=2.0`
- `numpy>=1.24`

**Important:** `yfinance` is not listed in `requirements.txt` but is imported lazily in `backend/data_engine/loader.py`. Install it explicitly if you plan to use live market data:

```bash
pip install yfinance
```

### Node (Frontend)

```bash
cd /Users/srujanreddygangireddy/Desktop/STOCKS/stock-trading-assistant/frontend
npm install
```

Dependencies: React 19, react-dom 19, Vite 8, TypeScript ~6.0, Oxlint.

---

## Running the Backend

```bash
cd /Users/srujanreddygangireddy/Desktop/STOCKS/stock-trading-assistant
uvicorn backend.main:app --reload
```

- API available at `http://127.0.0.1:8000`
- Swagger docs at `http://127.0.0.1:8000/docs`

Or using Python directly:

```bash
cd /Users/srujanreddygangireddy/Desktop/STOCKS/stock-trading-assistant
PYTHONPATH=/Users/srujanreddygangireddy/Desktop/STOCKS/stock-trading-assistant python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

---

## Running the Frontend

```bash
cd /Users/srujanreddygangireddy/Desktop/STOCKS/stock-trading-assistant/frontend
npm run dev
```

- Vite dev server (default `http://localhost:5173`)

---

## Autonomous Trading Loop

`backend/autonomous/` is a fully autonomous extension of the pipeline above:
it watches live prices every ~1 second, decides to buy or sell on its own,
and (once explicitly enabled) places real Groww orders with no human in the
loop. **Read this whole section before turning on real orders.**

### How it decides

Two clocks run per ticker:

- **Fast tick clock (default 1s):** pulls the latest price, updates O(1)
  incremental rolling stats (mean/std, min/max, RSI, EMA -- see
  `backend/autonomous/tick_stats.py`), and computes a buy/sell "probability"
  (`backend/autonomous/probability_engine.py`) from how close price is to its
  recent low/high, its z-score, RSI, and momentum. This is a **heuristic
  score in [0,1]**, not a calibrated statistical probability -- treat it as
  "how strongly do several simple signals agree," not a guarantee.
- **Slow pipeline clock (default 60s):** re-runs the existing candle/
  indicator/scoring/risk pipeline to get structural support/resistance,
  `target1`, and `invalidation` levels.

A **buy** only fires when both agree: the slow pipeline shows a real bullish
ENTRY/WATCH setup with valid target/invalidation levels, AND the fast engine
says price is currently near the bottom of its range with oversold/turning
momentum. A **sell** fires the instant price touches the structural target or
invalidation level, or the fast sell-probability crosses its threshold.

### Non-negotiable risk guardrails

These are enforced independently of the strategy logic, so a bad signal
cannot exceed them (`backend/autonomous/risk_manager.py`):

1. **Max capital per trade** -- % of available margin, capped by an absolute
   rupee ceiling too (`AUTOTRADE_MAX_CAPITAL_PCT`, `AUTOTRADE_MAX_CAPITAL_ABS`).
2. **Daily loss kill-switch** -- once today's realized+unrealized loss
   crosses `AUTOTRADE_DAILY_LOSS_LIMIT_PCT` of the day's starting margin, no
   new positions open until you `POST /autonomous/kill-switch/reset`. Open
   positions keep being monitored so target/stop exits still work.
3. **Max concurrent positions** (`AUTOTRADE_MAX_OPEN_POSITIONS`).
4. **Per-ticker cooldown after a close** (`AUTOTRADE_COOLDOWN_MINUTES`) --
   prevents rapid re-entry whipsaw on noisy ticks.

It is also **long-only** (buys and sells, never shorts) and only trades
during the NSE regular session (`AUTOTRADE_MARKET_OPEN`/`_CLOSE`, IST) -- it
does not know about exchange holidays, so double-check the NSE holiday
calendar before relying on it around a holiday.

### Paper mode vs real money

Controlled entirely by the existing `GROWW_ALLOW_REAL_ORDERS` flag:

- `false` (default): every "buy"/"sell" is simulated at the observed price.
  Nothing is sent to Groww. Positions and the full decision audit trail
  (`autonomous_events` table) are recorded exactly as if it were real, so you
  can validate behavior risk-free first.
- `true`: real MARKET orders are placed via the existing `backend/groww`
  client.

**Strongly recommended: run in paper mode across at least one full session
before ever setting `GROWW_ALLOW_REAL_ORDERS=true`.**

### Running it

```bash
# Configure .env first (see .env.example) -- watchlist, thresholds, risk limits.
cd /Users/srujanreddygangireddy/stocks/StockTrade
python -m backend.autonomous.trader
```

This runs as its own long-lived process (not inside the FastAPI request
cycle) so it keeps polling every second independently of whether anyone is
using the web UI. Run it under a process supervisor (e.g. `pm2`, `supervisord`,
a systemd service, or just `tmux`/`screen`) if you want it to survive a
terminal close or restart on crash -- none of that is set up here yet.

### Monitoring it

- `GET /autonomous/status` -- current config, risk limits, kill-switch state,
  open autonomous position count.
- `GET /autonomous/events` -- full decision audit trail (buys, sells, skips
  with the reason, errors), most recent first.
- `POST /autonomous/kill-switch/reset` -- manually clear the daily-loss
  kill-switch.
- `GET /positions` -- includes autonomous positions alongside manual ones
  (see the new `source` field: `"manual"` vs `"autonomous"`).

### Known limitations

- The probability score is a hand-tuned heuristic, not a trained/backtested
  model for this specific autonomous strategy -- it has not been validated
  the way the manual pipeline's ENTRY/WATCH signals have (see
  [Signal Validation Status](#signal-validation-status)).
- No exchange holiday calendar -- see `backend/autonomous/market_hours.py`.
- Groww's live-trading (LTP/order) API rate limits are not explicitly
  handled; a large watchlist polled every second may hit them.
- The kill-switch state file (`AUTOTRADE_STATE_PATH`) is local disk, not
  synced anywhere -- back it up if you care about the day's realized-P&L
  counter surviving a machine loss mid-session.

---

## Deployment

The app is split into a FastAPI backend (deploys to Render) and a Vite/React
frontend (deploys to Vercel). No pipeline code changes are needed — only the
config in this section.

### Backend (Render, free tier)

1. Push this repo to GitHub (the deploy files below are already included):
   - `requirements.txt` — now includes `yfinance`.
   - `render.yaml` — builds with `pip install -r requirements.txt` and starts
     `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
   - `backend/main.py` — binds to `$PORT` (injected by Render) and reads
     `BACKEND_CORS_ORIGINS` for CORS (defaults to `*`).
2. In Render: **New + Web Service → connect the repo**. It auto-reads
   `render.yaml`. Set `PYTHONPATH=.` (done in `render.yaml`) and optionally set
   `BACKEND_CORS_ORIGINS` to your Vercel URL to tighten CORS.
3. Deploy. Your backend URL will be `https://<service>.onrender.com`.

> **Free-tier caveat:** Render free web services spin down when idle, so the
> first request after a pause can take ~30–60s (cold start). The `/scan`
> endpoint fetches 18 tickers from yfinance per call and may exceed a short
> request timeout on a cold start — prefer `/analyze` for quick checks, and
> consider bumping the request timeout or moving `/scan` to a background job
> later.

### Frontend (Vercel, free tier)

1. In Vercel: **Add New Project → import the repo**. Set:
   - **Root Directory:** `frontend` (also encoded in `vercel.json`).
   - **Build Command:** `npm install && npm run build` (also in `vercel.json`).
   - **Build Environment Variable:** `VITE_API_BASE_URL` = your Render URL
     (e.g. `https://<service>.onrender.com`). This is inlined at build time.
2. Deploy. Your frontend URL will be `https://<project>.vercel.app`.

> `VITE_API_BASE_URL` is read at build time, so changing it requires a rebuild.
> For local dev with no var set, the frontend falls back to
> `http://127.0.0.1:8000`. See `.env.example` for the variable names.

---

## Running the Demo

```bash
cd /Users/srujanreddygangireddy/Desktop/STOCKS/stock-trading-assistant
python demo.py
```

This generates 600 synthetic candles, runs the full pipeline, and prints:
- The tail of the analysis DataFrame
- Trend distribution
- Pattern counts
- Swing point counts
- Risk/reward downgrades
- Healthy setups (ENTRY) with valid risk/reward

---

## Backtesting

Backtesting scripts validate the signal logic across historical data. All require `yfinance` to be installed.

### Simple Basket (14 tickers, 15m / 60d)

```bash
python backend/backtesting/run_basket.py
```

### Walk-Forward on 14 Tickers (5y daily, 80/20 train/test split)

```bash
python backend/backtesting/run_walk_forward.py
```

### Walk-Forward Basket + WATCH Direction Check + Losing Trade Gap Analysis (~40 tickers)

```bash
python backend/backtesting/run_walk_forward_basket.py
```

This is the most comprehensive backtest. It:
- Runs walk-forward backtests across ~40 NSE tickers (5 years daily).
- Evaluates **WATCH** rows by checking if price moved in the signaled direction by >1× ATR within the next 10 candles.
- Breaks down bearish WATCH accuracy by trend regime.
- Analyzes losing trades to determine whether losses occurred via overnight gaps or gradual drift.

---

## API Reference

### `GET /health`

Returns:
```json
{
  "status": "ok"
}
```

### `GET /analyze/{source}`

Runs the full pipeline and returns the latest setup plus a 30-candle history.

**Query parameters:**

| Parameter | Default | Notes |
|-----------|---------|-------|
| `source` | (path) | `synthetic` or `yfinance` |
| `n_candles` | `600` | Only used for `synthetic` |
| `interval` | `15m` | yfinance interval (`15m`, `1h`, `1d`, etc.) |
| `ticker` | `RELIANCE.NS` | yfinance ticker symbol |

**Response shape:**

```json
{
  "source": "synthetic",
  "interval": "15m",
  "n_candles": 600,
  "explanation": {
    "date": "2025-07-31 00:00:00",
    "close": 101.16,
    "pattern": "bearish_engulfing",
    "pattern_direction": "bearish",
    "pattern_description": "A bearish candle completely swallowed the previous bullish candle, showing sellers took control.",
    "trend": "downtrend",
    "trend_agrees": true,
    "trend_note": "The broader trend is down (or flat), which supports a bearish setup.",
    "near_support": false,
    "near_resistance": true,
    "near_zone": "resistance",
    "support": 97.2,
    "resistance": 102.47,
    "volume_above_average": true,
    "score": 75.0,
    "entry_zone": [100.6, 101.72],
    "invalidation": 102.47,
    "target1": 95.0,
    "target2": 90.0,
    "risk_reward": 1.5,
    "risk_reward_ok": true,
    "risk_reward_note": "risk/reward acceptable ✓ (1:1.5)",
    "status_reason": null,
    "status": "WATCH",
    "validated": "experimental",
    "active_setup": true,
    "monitoring": false,
    "validation_note": "Bearish signals are still experimental -- backtesting across 40 stocks and 5 years showed no reliable directional accuracy for this signal type yet. Bullish signals have not yet been validated at the same scale."
  },
  "history": [
    {
      "timestamp": "2025-07-24 00:00:00",
      "close": 96.91,
      "status": "NO TRADE",
      "score": 0.0,
      "direction": null
    }
  ]
}
```

---

## Frontend Features

- **Source selector:** Toggle between Synthetic data and yfinance live data.
- **yfinance controls:** Ticker input (default `RELIANCE.NS`), interval dropdown (`15m`, `1h`, `1d`).
- **Setup card** (3 states):
  - **ENTRY / WATCH** (active setup): Shows price, pattern, direction, trend, entry zone, targets, invalidation, risk/reward, and validation note.
  - **WATCH** (monitoring only): Same header but no entry/target levels; shows message that price is near a key level but no confirming pattern yet.
  - **NO TRADE:** Gray card stating insufficient confirmation.
- **Experimental badge:** Shown on any bearish signal.
- **"Why?" expander:** Reveals `trend_note` and `risk_reward_note`.
- **Recent context table:** Last 10 rows from the 30-candle history payload.
- **Error handling:** HTTP errors displayed inline.

---

## Signal Validation Status

| Signal Type | Validation Status |
|-------------|-------------------|
| **Bullish** | `validated: "provisional"`. The backend attaches a `validation_note`: *"Backtesting shows promising directional accuracy (48% on held-out data vs a 25% baseline), but sample size is still limited. Treat as provisional, not proven."* |
| **Bearish** | `validated: "experimental"`. The backend attaches a `validation_note`: *"Bearish signals are still experimental -- backtesting across 40 stocks and 5 years showed no reliable directional accuracy for this signal type yet."* |
| **WATCH / no pattern** | Treated as monitoring; no validation note unless bearish. |

The backtesting scripts (`run_walk_forward_basket.py`) are the validation harness. They run walk-forward backtests across ~40 NSE tickers (5 years daily) and evaluate WATCH rows by checking if price moved in the signaled direction by >1× ATR within the next 10 candles.

---

## Caveats and Limitations

1. **No root README.md (previously):** This README was added to document the project. The only prior README was the default Vite template in `frontend/README.md`.
2. **yfinance missing from requirements.txt:** `yfinance` is imported lazily but not listed in `requirements.txt`. You must `pip install yfinance` to use the `/analyze/yfinance` endpoint or backtesting scripts.
3. **Intraday data limits:** yfinance restricts intraday (`m`/`h`) intervals to ~60 days of history. Daily or longer intervals can pull multiple years.
4. **CORS is wide open:** `allow_origins=["*"]` in `backend/main.py` — acceptable for local dev, but should be tightened before any external deployment.
5. **Bearish signals are unvalidated:** Backtesting across 40 stocks/5 years found no reliable directional accuracy for bearish signals. They are flagged "Experimental" in the UI.
6. **Synthetic data is deterministic:** `demo.py` and `/analyze/synthetic` use `seed=42` with hardcoded trend legs, so outputs are repeatable but not representative of real market noise.
7. **Backtesting is simple:** `backtest_engine.py` uses fixed target/invalidation levels, no slippage, no commissions, no position sizing, and no trailing stops. Results are directional accuracy only.
8. **Small sample warnings:** Both `run_basket.py` and `run_walk_forward.py` warn when total trades < 20–30, noting metrics are not statistically reliable.
9. **No database / persistence:** All state is in-memory DataFrames. Restarting the backend clears everything.
10. **No authentication or rate limiting:** The FastAPI app is unprotected.

---

## Project Structure

```
stock-trading-assistant/
├── demo.py                          # End-to-end pipeline demo on synthetic data
├── requirements.txt                 # Python dependencies
├── backend/
│   ├── main.py                      # FastAPI app
│   ├── __init__.py
│   ├── data_engine/
│   │   ├── __init__.py
│   │   └── loader.py                # yfinance / CSV / synthetic data loaders
│   ├── candles/
│   │   ├── __init__.py
│   │   └── candle_engine.py         # Candle metrics + pattern detection
│   ├── indicators/
│   │   ├── __init__.py
│   │   └── indicator_engine.py      # EMA, RSI, ATR, VWAP + swing points + trend + S/R
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── scoring_engine.py        # Pattern + trend + S/R + volume -> score/status
│   │   └── explanation_engine.py    # Structured plain-language explanation per setup
│   ├── risk/
│   │   ├── __init__.py
│   │   └── risk_engine.py           # Entry zone, invalidation, targets, R:R, downgrade
│   └── backtesting/
│       ├── __init__.py
│       ├── backtest_engine.py       # Trade simulator (target vs invalidation)
│       ├── run_walk_forward.py      # Walk-forward on ~14 NSE tickers (5y daily)
│       ├── run_walk_forward_basket.py  # Walk-forward on ~40 NSE tickers + WATCH direction check + losing trade gap analysis
│       └── run_basket.py            # Simple basket backtest on ~14 NSE tickers (15m intraday)
└── frontend/
    ├── package.json                 # React 19 + Vite 8 + TypeScript + Oxlint
    ├── vite.config.ts
    ├── index.html
    ├── tsconfig*.json
    ├── README.md                    # Default Vite template README
    └── src/
        ├── main.tsx
        ├── App.tsx                  # UI for selecting source, fetching analysis, rendering setup card + history
        ├── App.css                   # Full component styling
        ├── index.css
        ├── types.ts                  # TypeScript interfaces for Explanation, HistoryRow, AnalyzeResponse
        └── assets/
```
