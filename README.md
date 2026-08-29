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
    "validated": false,
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
| **Bullish** | Marked as `validated: true` in the explanation engine, but the backend note says *"Bullish signals have not yet been validated at the same scale"* as bearish. |
| **Bearish** | `validated: false`. The backend attaches a `validation_note`: *"Bearish signals are still experimental -- backtesting across 40 stocks and 5 years showed no reliable directional accuracy for this signal type yet."* |
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
