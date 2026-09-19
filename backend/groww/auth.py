"""Groww Trading API authentication."""

from __future__ import annotations

import os

from dotenv import load_dotenv

# backend/main.py calls load_dotenv() too, but this module is also imported
# directly by standalone entrypoints that never import main.py -- notably
# `python -m backend.autonomous.trader`, and any ad hoc script that touches
# the Groww client. Without this, GROWW_API_KEY/SECRET/ACCESS_TOKEN are
# correctly present in .env but never make it into os.environ for those
# processes, and every call here fails with a "not set" error that looks
# like a missing key even though the file is fine. load_dotenv() is cheap
# and idempotent, and (by default) never overrides a variable already set
# in the real environment, so calling it again here is safe.
load_dotenv()


class GrowwAuthError(Exception):
    """Raised when Groww authentication fails."""


def get_api_key() -> str:
    key = os.environ.get("GROWW_API_KEY", "").strip()
    if not key:
        raise GrowwAuthError("GROWW_API_KEY is not set.")
    return key


def get_api_secret() -> str:
    secret = os.environ.get("GROWW_API_SECRET", "").strip()
    if not secret:
        raise GrowwAuthError("GROWW_API_SECRET is not set.")
    return secret


def get_access_token() -> str:
    token = os.environ.get("GROWW_ACCESS_TOKEN", "").strip()
    if not token:
        raise GrowwAuthError("GROWW_ACCESS_TOKEN is not set.")
    return token


def is_real_trading_enabled() -> bool:
    flag = os.environ.get("GROWW_ALLOW_REAL_ORDERS", "false").strip().lower()
    return flag in ("1", "true", "yes", "y")
