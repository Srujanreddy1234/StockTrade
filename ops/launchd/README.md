# Persistent paper-trading via launchd (macOS)

Runs the autonomous trader as a background user agent that survives this
Claude Code session ending, keeps running after market close, and
restarts automatically after a machine restart (if loaded with
`RunAtLoad`) -- always forced into PAPER mode, never capable of
submitting a real order, regardless of what `.env`'s
`GROWW_ALLOW_REAL_ORDERS` says.

**Nothing here has been installed automatically.** Run the commands below
yourself when you're ready.

## Safety design
- `scripts/run_paper_trader.sh` exports `GROWW_ALLOW_REAL_ORDERS=false`
  before launching the trader. `load_dotenv()` (called from
  `backend/__init__.py`) does not override an already-set environment
  variable by default, so this wins over `.env` without ever editing it.
- The launchd plist ALSO sets `GROWW_ALLOW_REAL_ORDERS=false` directly
  (`EnvironmentVariables`), so even if the wrapper script were edited,
  the launchd-launched process still can't go live.
- Your real `.env` and its `GROWW_ALLOW_REAL_ORDERS` value are never
  touched by any of this.

## Install

```bash
cp ops/launchd/com.stocktrade.papertrader.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.stocktrade.papertrader.plist
```

`RunAtLoad` means it starts immediately on `load`, and again automatically
the next time you log in / the machine restarts (since the plist stays in
`~/Library/LaunchAgents/`).

## Start / stop (without unloading)

```bash
launchctl start com.stocktrade.papertrader
launchctl stop com.stocktrade.papertrader
```

A `stop` is clean -- `KeepAlive.SuccessfulExit = false` means launchd will
NOT auto-restart it after an explicit stop, only after a crash.

## Status

```bash
launchctl list | grep com.stocktrade.papertrader
```

Shows the PID (if running) and last exit code (if not).

## Logs

```bash
tail -f backtest_results/_paper_run/launchd_stdout.log
tail -f backtest_results/_paper_run/launchd_stderr.log
```

Paper trading state (kill-switch/daily P&L) and the scanner's current
watchlist picks persist to:
```
backtest_results/_paper_run/paper_state.json
backtest_results/_paper_run/paper_watchlist.json
```
Every candidate evaluation and simulated trade is recorded in the
`autonomous_events`/`orders`/`positions` tables in `stocktrade.db` --
the same database the rest of the app reads -- tagged `mode="paper"`,
so it survives process restarts, computer restarts, and market close
exactly like any other row in that database.

## Uninstall / fully remove

```bash
launchctl unload ~/Library/LaunchAgents/com.stocktrade.papertrader.plist
rm ~/Library/LaunchAgents/com.stocktrade.papertrader.plist
```

## Restart behavior
- Crashes: launchd restarts it after `ThrottleInterval` (30s), indefinitely.
- Explicit `launchctl stop`: stays stopped.
- Machine restart: starts again automatically (plist has `RunAtLoad`),
  as long as it's still in `~/Library/LaunchAgents/`.
- `.env` changes (including `GROWW_ALLOW_REAL_ORDERS`) have NO effect on
  this process -- it is permanently paper-mode by design, independent of
  live-trading configuration.
