"""Typed order contracts shared by exchange adapters and the coordinator."""

from dataclasses import dataclass, field
from math import isfinite
from typing import TypedDict


class OrderFee(TypedDict):
    currency: str | None
    cost: float | None


class OrderFill(TypedDict):
    id: str | None
    price: float
    amount: float
    timestamp: float | None


@dataclass(frozen=True)
class OrderCapabilities:
    has_client_order_id: bool = False
    time_in_force: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    amount: float
    order_type: str
    price: float | None
    client_order_id: str
    product: str
    time_in_force: str | None = None
    is_reduce_only: bool = False
    expires_at: float | None = None


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str | None
    status: str
    filled_qty_base: float | None
    avg_price: float | None
    fee_usd: float | None = None
    fees: list[OrderFee] = field(default_factory=list)
    fills: list[OrderFill] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in ("closed", "canceled", "cancelled", "expired", "rejected")


def parse_order_snapshot(order: dict, base: str, quote: str) -> OrderSnapshot:
    """Normalize cumulative quantities without inventing fills or fee conversions.

    Fees denominated in a stable quote use the project's USD parity assumption;
    base fees are valued at the actual fill price. Other currencies stay unknown.
    """
    filled = order.get("filled")
    average = order.get("average")
    if average is None and filled and order.get("cost") is not None:
        average = float(order["cost"]) / float(filled)
    filled = float(filled) if filled is not None else None
    average = float(average) if average is not None else None
    if filled is not None and (not isfinite(filled) or filled < 0):
        raise ValueError(f"Invalid filled quantity for order {order.get('id')}")
    if average is not None and (not isfinite(average) or average <= 0):
        raise ValueError(f"Invalid average price for order {order.get('id')}")
    fees: list[OrderFee] = [
        {"currency": fee.get("currency"), "cost": float(fee["cost"]) if fee.get("cost") is not None else None}
        for fee in (order.get("fees") or ([order["fee"]] if order.get("fee") else []))
    ]
    fee_usd = 0.0 if fees else None
    for fee in fees:
        cost, currency = fee.get("cost"), fee.get("currency")
        if cost is None:
            fee_usd = None
            break
        if not isfinite(cost):
            raise ValueError(f"Invalid fee for order {order.get('id')}")
        if currency in ("USD", "USDT", "USDC") and quote in ("USD", "USDT", "USDC"):
            rate = 1.0
        elif currency == base and average is not None and quote in ("USD", "USDT", "USDC"):
            rate = average
        else:
            fee_usd = None
            break
        fee_usd += float(cost) * rate
    return OrderSnapshot(
        order_id=str(order["id"]) if order.get("id") is not None else None,
        status=order.get("status") or "unknown",
        filled_qty_base=filled,
        avg_price=average,
        fee_usd=fee_usd,
        fees=fees,
        fills=[
            {
                "id": str(trade["id"]) if trade.get("id") is not None else None,
                "price": float(trade["price"]),
                "amount": float(trade["amount"]),
                "timestamp": trade.get("timestamp"),
            }
            for trade in order.get("trades") or []
        ],
    )
