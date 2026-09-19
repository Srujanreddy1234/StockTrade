"""Groww Trading API client."""

from __future__ import annotations

import logging
from typing import Any

from growwapi import GrowwAPI

from backend.groww.auth import get_api_key, get_api_secret, get_access_token, GrowwAuthError
from backend.groww.models import (
    holding_from_api,
    margin_from_api,
    order_from_api,
    position_from_api,
)

logger = logging.getLogger(__name__)


class GrowwClientError(Exception):
    """Raised when a Groww API call fails."""


class GrowwClient:
    """Thin wrapper around the official Groww Python SDK."""

    def __init__(self) -> None:
        self._api: GrowwAPI | None = None

    def _get_api(self) -> GrowwAPI:
        if self._api is None:
            try:
                access_token = get_access_token()
            except GrowwAuthError:
                try:
                    api_key = get_api_key()
                    api_secret = get_api_secret()
                    access_token = GrowwAPI.get_access_token(
                        api_key=api_key, secret=api_secret
                    )
                except GrowwAuthError:
                    raise
                except Exception as exc:
                    raise GrowwClientError(
                        f"Failed to authenticate with Groww: {exc}"
                    ) from exc
            try:
                self._api = GrowwAPI(access_token)
            except Exception as exc:
                raise GrowwClientError(
                    f"Failed to initialize Groww client with access token: {exc}"
                ) from exc
        return self._api

    def get_holdings(self) -> list[dict[str, Any]]:
        try:
            response = self._get_api().get_holdings_for_user(timeout=15)
            return response.get("holdings", [])
        except Exception as exc:
            raise GrowwClientError(f"Failed to fetch holdings: {exc}") from exc

    def get_positions(self) -> list[dict[str, Any]]:
        try:
            response = self._get_api().get_positions_for_user(timeout=15)
            return response.get("positions", [])
        except Exception as exc:
            raise GrowwClientError(f"Failed to fetch positions: {exc}") from exc

    def get_margin(self) -> dict[str, Any]:
        try:
            return self._get_api().get_available_margin_details(timeout=15)
        except Exception as exc:
            raise GrowwClientError(f"Failed to fetch margin: {exc}") from exc

    def get_orders(self) -> list[dict[str, Any]]:
        try:
            response = self._get_api().get_order_list(timeout=15)
            if isinstance(response, dict):
                return response.get("order_list", response.get("orders", []))
            return response or []
        except Exception as exc:
            raise GrowwClientError(f"Failed to fetch orders: {exc}") from exc

    def place_order(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """Place an order. `payload` must match the installed growwapi SDK's
        `place_order()` signature: trading_symbol, exchange, segment, product,
        order_type, transaction_type, quantity, validity, and optionally
        price, trigger_price, order_reference_id. `order_reference_id` is the
        SDK's own broker-side idempotency key (sent through to Groww as-is)
        -- callers should always pass a deterministic one so a lost-response
        retry can be reconciled instead of blindly resubmitted.

        Raises the underlying growwapi exception unmodified (GrowwAPIException
        and its subclasses, or a raw `requests` exception for a genuine
        network failure) so callers can classify the failure by type instead
        of parsing a wrapped string.
        """
        api = self._get_api()
        return api.place_order(timeout=timeout, **payload)

    def modify_order(self, order_id: str, segment: str, payload: dict[str, Any]) -> dict[str, Any]:
        api = self._get_api()
        return api.modify_order(groww_order_id=order_id, segment=segment, **payload)

    def cancel_order(self, order_id: str, segment: str = "CASH") -> dict[str, Any]:
        api = self._get_api()
        return api.cancel_order(groww_order_id=order_id, segment=segment)

    def get_order_status(self, order_id: str, segment: str = "CASH") -> dict[str, Any]:
        """Status of an order by the Groww-assigned order id."""
        api = self._get_api()
        return api.get_order_status(groww_order_id=order_id, segment=segment)

    def get_order_status_by_reference(self, order_reference_id: str, segment: str = "CASH") -> dict[str, Any]:
        """Status of an order by OUR order_reference_id -- the only lookup
        available before a broker_order_id is known, e.g. after a
        lost-response scenario where place_order() raised before returning
        one.
        """
        api = self._get_api()
        return api.get_order_status_by_reference(order_reference_id=order_reference_id, segment=segment)

    def get_order_detail(self, order_id: str, segment: str = "CASH") -> dict[str, Any]:
        api = self._get_api()
        return api.get_order_detail(groww_order_id=order_id, segment=segment)

    def get_ltp(self, trading_symbol: str, exchange: str = "NSE") -> float:
        try:
            api = self._get_api()
            response = api.get_ltp(
                trading_symbol=trading_symbol, exchange=exchange
            )
            if isinstance(response, dict):
                return float(response.get("close", response.get("ltp", 0)) or 0)
            return float(response or 0)
        except Exception as exc:
            raise GrowwClientError(
                f"Failed to fetch LTP for {trading_symbol}: {exc}"
            ) from exc


# Shared client instance (reuse access token across requests within a process).
_client = GrowwClient()


def get_client() -> GrowwClient:
    return _client
