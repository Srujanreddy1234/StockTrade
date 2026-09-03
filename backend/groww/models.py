"""Data models for Groww API responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GrowwHolding:
    symbol: str
    name: str
    quantity: float
    avg_price: float
    invested_value: float
    source: str = "groww"


@dataclass
class GrowwPosition:
    symbol: str
    exchange: str
    segment: str
    product: str
    quantity: float
    average_price: float
    overnight_quantity: float
    overnight_average_price: float
    source: str = "groww"


@dataclass
class GrowwMargin:
    available_cash: float
    used_margin: float
    available_margin: float
    source: str = "groww"


@dataclass
class GrowwOrder:
    order_id: str
    trading_symbol: str
    exchange: str
    segment: str
    product: str
    order_type: str
    transaction_type: str
    quantity: float
    price: float | None
    trigger_price: float | None
    status: str
    placed_at: str | None
    source: str = "groww"


def holding_from_api(data: dict[str, Any]) -> GrowwHolding:
    return GrowwHolding(
        symbol=data.get("trading_symbol", ""),
        name=data.get("company_name", data.get("trading_symbol", "")),
        quantity=float(data.get("quantity", 0) or 0),
        avg_price=float(data.get("average_price", 0) or 0),
        invested_value=float(data.get("invested_value", 0) or 0),
    )


def position_from_api(data: dict[str, Any]) -> GrowwPosition:
    return GrowwPosition(
        symbol=data.get("trading_symbol", ""),
        exchange=data.get("exchange", ""),
        segment=data.get("segment", ""),
        product=data.get("product", ""),
        quantity=float(data.get("quantity", 0) or 0),
        average_price=float(data.get("average_price", 0) or 0),
        overnight_quantity=float(data.get("overnight_quantity", 0) or 0),
        overnight_average_price=float(data.get("overnight_average_price", 0) or 0),
    )


def margin_from_api(data: dict[str, Any]) -> GrowwMargin:
    return GrowwMargin(
        available_cash=float(data.get("available_cash", 0) or 0),
        used_margin=float(data.get("used_margin", 0) or 0),
        available_margin=float(data.get("available_margin", 0) or 0),
    )


def order_from_api(data: dict[str, Any]) -> GrowwOrder:
    return GrowwOrder(
        order_id=data.get("order_id", data.get("id", "")),
        trading_symbol=data.get("trading_symbol", ""),
        exchange=data.get("exchange", ""),
        segment=data.get("segment", ""),
        product=data.get("product", ""),
        order_type=data.get("order_type", ""),
        transaction_type=data.get("transaction_type", ""),
        quantity=float(data.get("quantity", 0) or 0),
        price=_safe_float(data.get("price")),
        trigger_price=_safe_float(data.get("trigger_price")),
        status=data.get("status", ""),
        placed_at=data.get("placed_at", data.get("created_at")),
    )


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
