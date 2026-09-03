"""Groww Trading API authentication."""

from __future__ import annotations

import os


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


def is_real_trading_enabled() -> bool:
    flag = os.environ.get("GROWW_ALLOW_REAL_ORDERS", "false").strip().lower()
    return flag in ("1", "true", "yes", "y")
