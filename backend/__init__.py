"""Loads .env before any submodule of `backend` is imported.

Several modules (notably backend/autonomous/config.py) read environment
variables as plain dataclass field defaults, which Python evaluates once,
immediately, when the class body executes at import time -- not lazily per
instantiation. If .env hadn't been loaded yet at that point, those fields
would silently capture their hardcoded fallback values instead of whatever
is actually in .env, no error raised.

Previously only backend/main.py called load_dotenv(), so any other
entrypoint -- notably `python -m backend.autonomous.trader`, which imports
backend.autonomous.config before backend.groww.auth (the only other module
that called load_dotenv()) -- could silently run with the wrong risk
limits/watchlist/capital caps. Putting the call here, in the package's own
__init__.py, guarantees it runs before any submodule of `backend` does,
regardless of which one is imported first.
"""

from dotenv import load_dotenv

load_dotenv()
