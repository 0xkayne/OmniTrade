"""Typed order contracts shared by exchange adapters and the coordinator."""

from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from src.market.instrument import Instrument


class OrderFee(TypedDict):
    currency: str | None
    cost: float | None


class _OrderFill(TypedDict):
    id: str | None
    price: float
    amount: float
    timestamp: float | None


class OrderFill(_OrderFill, total=False):
    quantity_unit: str
    amount_base: float
    notional_quote: float
    fees: list[OrderFee]
    realized_pnl: float | None
    realized_pnl_currency: str | None


@dataclass(frozen=True)
class OrderCapabilities:
    has_client_order_id: bool = False
    time_in_force: tuple[str, ...] = ()
    has_position_validation: bool = False


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    amount: float  # Native venue units: base for spot, contracts for derivatives.
    order_type: str
    price: float | None
    client_order_id: str
    product: str
    time_in_force: str | None = None
    is_reduce_only: bool = False
    expires_at: float | None = None
    quantity_unit: str = "base"
    account_family: str | None = None


@dataclass(frozen=True)
class OrderAccountSnapshot:
    family: str
    available: dict[str, float]
    position_mode: str = "oneway"
    margin_mode: str = "single_asset"
    is_portfolio_margin: bool = False
    timestamp: float = 0.0


@dataclass(frozen=True)
class OrderPositionSnapshot:
    symbol: str
    qty_native: float
    entry_price: float | None
    mark_price: float | None
    leverage: float | None
    margin_mode: str | None
    timestamp: float
    max_notional_quote: float | None = None


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str | None
    status: str
    filled_qty_base: float | None
    avg_price: float | None
    fee_usd: float | None = None
    fees: list[OrderFee] = field(default_factory=list)
    fills: list[OrderFill] = field(default_factory=list)
    filled_qty_native: float | None = None
    filled_notional_quote: float | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("closed", "canceled", "cancelled", "expired", "rejected")


def _order_fees(raw: dict) -> list[OrderFee]:
    return [
        {"currency": fee.get("currency"), "cost": float(fee["cost"]) if fee.get("cost") is not None else None}
        for fee in (raw.get("fees") or ([raw["fee"]] if raw.get("fee") else []))
    ]


def _fee_value(fees: list[OrderFee], base: str, quote: str, price: float | None) -> float | None:
    value = 0.0 if fees else None
    for fee in fees:
        cost, currency = fee.get("cost"), fee.get("currency")
        if cost is None:
            return None
        if not isfinite(cost):
            raise ValueError("Invalid non-finite order fee")
        if currency in ("USD", "USDT", "USDC") and quote in ("USD", "USDT", "USDC"):
            rate = 1.0
        elif currency == base and price is not None and quote in ("USD", "USDT", "USDC"):
            rate = price
        else:
            return None
        value += cost * rate
    return value


def parse_order_snapshot(order: dict, base: "str | Instrument", quote: str | None = None) -> OrderSnapshot:
    """Normalize native fills, preserving unknown facts and inverse quantity semantics.

    The legacy ``(order, base, quote)`` form remains valid for unit linear/spot
    callers. Contract-aware adapters must supply the complete Instrument.
    """
    instrument = None if isinstance(base, str) else base
    base_symbol = base if instrument is None else instrument.base.symbol
    quote_symbol = quote if instrument is None else instrument.quote.symbol
    if quote_symbol is None:
        raise ValueError(f"Missing quote asset while parsing order {order.get('id')}")
    filled = float(order["filled"]) if order.get("filled") is not None else None
    average = float(order["average"]) if order.get("average") is not None else None
    if filled is not None and (not isfinite(filled) or filled < 0):
        raise ValueError(f"Invalid filled quantity for order {order.get('id')}")
    if average is not None and (not isfinite(average) or average <= 0):
        # Binance ACKs use zero average before fills have become available.
        if average == 0:
            average = None
        else:
            raise ValueError(f"Invalid average price for order {order.get('id')}")

    fills: list[OrderFill] = []
    seen: set[str] = set()
    for trade in order.get("trades") or []:
        trade_id = str(trade["id"]) if trade.get("id") is not None else None
        if trade_id is not None and trade_id in seen:
            continue
        if trade_id is not None:
            seen.add(trade_id)
        price, qty = float(trade["price"]), float(trade["amount"])
        if not isfinite(price) or price <= 0 or not isfinite(qty) or qty <= 0:
            raise ValueError(f"Invalid trade {trade_id} for order {order.get('id')}")
        info = trade.get("info") or {}
        realized = info.get("realizedPnl", info.get("realizedProfit", info.get("rp")))
        fills.append(
            {
                "id": trade_id,
                "price": price,
                "amount": qty,
                "timestamp": trade.get("timestamp"),
                "quantity_unit": instrument.quantity_unit if instrument else "base",
                "amount_base": instrument.base_equivalent(qty, price) if instrument else qty,
                "notional_quote": instrument.quote_notional(qty, price) if instrument else qty * price,
                "fees": _order_fees(trade),
                "realized_pnl": float(realized) if realized is not None else None,
                "realized_pnl_currency": (
                    instrument.settlement_asset.symbol
                    if instrument and instrument.settlement_asset
                    else info.get("marginAsset")
                ),
            }
        )
    native_total = sum((Decimal(str(fill["amount"])) for fill in fills), Decimal(0))
    has_complete_fills = (
        bool(fills) and filled is not None and abs(native_total - Decimal(str(filled))) <= Decimal("1e-12")
    )
    if fills and filled is not None and native_total > Decimal(str(filled)) + Decimal("1e-12"):
        raise ValueError(f"Trade quantity exceeds cumulative fill for order {order.get('id')}")
    filled_base = None
    filled_quote = None
    if has_complete_fills:
        filled_base = float(sum((Decimal(str(fill["amount_base"])) for fill in fills), Decimal(0)))
        filled_quote = float(sum((Decimal(str(fill["notional_quote"])) for fill in fills), Decimal(0)))
        average = filled_quote / filled_base
    else:
        if instrument and instrument.is_inverse and filled:
            # Inverse average is harmonic. A raw arithmetic average cannot
            # substitute for the complete native fills or cumulative base.
            cumulative_base = (order.get("info") or {}).get("cumBase")
            average = None
            if cumulative_base is not None:
                cumulative_base = float(cumulative_base)
                if not isfinite(cumulative_base) or cumulative_base <= 0:
                    raise ValueError(f"Invalid cumulative base for order {order.get('id')}")
                average = filled * instrument.contract_size / cumulative_base
        if average is None and filled and order.get("cost") is not None and not (instrument and instrument.is_inverse):
            multiplier = instrument.contract_size if instrument and instrument.quantity_unit == "contracts" else 1
            average = float(order["cost"]) / (filled * multiplier)
        if filled == 0:
            filled_base = filled_quote = 0.0
        elif filled is not None and average is not None:
            filled_base = instrument.base_equivalent(filled, average) if instrument else filled
            filled_quote = instrument.quote_notional(filled, average) if instrument else filled * average
        elif filled is not None and not (instrument and instrument.is_inverse):
            multiplier = instrument.contract_size if instrument and instrument.quantity_unit == "contracts" else 1
            filled_base = filled * multiplier
        elif filled is not None and instrument and instrument.is_inverse:
            filled_quote = filled * instrument.contract_size
    if average is not None and (not isfinite(average) or average <= 0):
        raise ValueError(f"Invalid average price for order {order.get('id')}")
    fees = _order_fees(order)
    fee_usd = _fee_value(fees, base_symbol, quote_symbol, average)
    if has_complete_fills and all(fill["fees"] for fill in fills):
        fees = [fee for fill in fills for fee in fill["fees"]]
        values = [_fee_value(fill["fees"], base_symbol, quote_symbol, fill["price"]) for fill in fills]
        fee_usd = sum(values) if all(value is not None for value in values) else None
    return OrderSnapshot(
        order_id=str(order["id"]) if order.get("id") is not None else None,
        status=order.get("status") or "unknown",
        filled_qty_base=filled_base,
        avg_price=average,
        fee_usd=fee_usd,
        fees=fees,
        fills=fills,
        filled_qty_native=filled,
        filled_notional_quote=filled_quote,
    )
