"""Tests for the manual live-order endpoint's authentication gate.

Never sets GROWW_ALLOW_REAL_ORDERS=true in a way that could leak past this
test: pytest's monkeypatch fixture always restores the original environment
afterward, and no test here ever reaches a real network call regardless
(place_order would only be reached, if at all, through GrowwClient which
requires real credentials that are never configured in this test env).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import backend.main as main_module


def test_manual_order_blocked_when_real_trading_disabled(monkeypatch):
    monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", "false")
    payload = main_module.GrowwOrderRequest(trading_symbol="RELIANCE", quantity=1)
    with pytest.raises(HTTPException) as exc_info:
        main_module.groww_place_order(payload)
    assert exc_info.value.status_code == 403
    assert "disabled" in exc_info.value.detail.lower()


def test_manual_order_blocked_when_real_trading_enabled_but_no_api_key(monkeypatch):
    monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", "true")
    monkeypatch.setattr(main_module, "_BACKEND_API_KEY", "")
    payload = main_module.GrowwOrderRequest(trading_symbol="RELIANCE", quantity=1)
    with pytest.raises(HTTPException) as exc_info:
        main_module.groww_place_order(payload)
    assert exc_info.value.status_code == 403
    assert "BACKEND_API_KEY" in exc_info.value.detail


def test_manual_order_reaches_broker_call_only_when_both_flag_and_key_set(monkeypatch):
    monkeypatch.setenv("GROWW_ALLOW_REAL_ORDERS", "true")
    monkeypatch.setattr(main_module, "_BACKEND_API_KEY", "some-configured-key")

    class FakeClient:
        def place_order(self, payload):
            raise RuntimeError("reached the broker call (expected in this test)")

    monkeypatch.setattr(main_module, "get_client", lambda: FakeClient())
    payload = main_module.GrowwOrderRequest(trading_symbol="RELIANCE", quantity=1)
    # Both gates pass, so the handler proceeds to the (faked) broker call --
    # proving the gates are the only things standing in front of it, not
    # asserting anything about real order placement.
    with pytest.raises(HTTPException) as exc_info:
        main_module.groww_place_order(payload)
    assert exc_info.value.status_code == 502
    assert "reached the broker call" in exc_info.value.detail
