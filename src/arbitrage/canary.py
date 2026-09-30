"""Explicit testnet canary execution for one bounded hedge cycle."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from math import isfinite
from typing import Any, Literal

from src.exchange.base import BaseExchange
from src.exchange.order import OrderPositionSnapshot
from src.market.instrument import Instrument, NetworkType

from .executor import HedgedExecutionResult, HedgedExecutor, LegResult
from .models import ArbCycle, ArbPair

CanaryDirection = Literal["buy_a_sell_b", "buy_b_sell_a"]
CONFIRMATION_TOKEN = "TESTNET_CANARY"
SUPPORTED_VENUES = frozenset({"arcus", "hyperliquid", "binance"})
_PRICE_TOLERANCE = Decimal("0.005")
_LIMIT_OFFSET = Decimal("0.004")
_MAX_LEG_NOTIONAL_USD = 100.0


@dataclass(frozen=True)
class CanaryRequest:
    base: str
    venue_a: str
    venue_b: str
    quantity_base: float
    direction: CanaryDirection = "buy_a_sell_b"
    symbol_a: str | None = None
    symbol_b: str | None = None
    max_notional_usd: float = 25.0
    confirmation: str = ""


@dataclass(frozen=True)
class CanaryResult:
    status: str
    direction: str
    venue_a: str
    venue_b: str
    symbol_a: str
    symbol_b: str
    quantity_base: float
    buy_price: float | None = None
    sell_price: float | None = None
    opening: HedgedExecutionResult | None = None
    closing: HedgedExecutionResult | None = None
    error: str | None = None
    cycle_id: str | None = None


class TestnetCanary:
    """Run exactly one explicitly confirmed testnet cycle, then close it."""

    def __init__(self, exchanges: dict[str, BaseExchange], store: Any, *, timeout_seconds: float = 3.0):
        self.exchanges = exchanges
        self.store = store
        self.timeout_seconds = timeout_seconds

    async def run(self, request: CanaryRequest) -> CanaryResult:
        result = CanaryResult(
            "REJECTED",
            request.direction,
            request.venue_a,
            request.venue_b,
            request.symbol_a or "",
            request.symbol_b or "",
            request.quantity_base,
        )
        cycle = None
        close_attempted = False
        try:
            self._validate_request(request)
            await self._assert_unblocked()
            instruments = await self._resolve_instruments(request)
            result = replace(result, symbol_a=instruments[0].venue_symbol, symbol_b=instruments[1].venue_symbol)
            await self._validate_adapters(instruments, request)
            buy_price, sell_price = await self._protected_prices(
                instruments,
                request.direction,
                (request.quantity_base, request.quantity_base),
                request.max_notional_usd,
            )
            result = replace(result, buy_price=buy_price, sell_price=sell_price)
            pair = ArbPair(
                request.base.upper(),
                "perp",
                request.venue_a,
                instruments[0].venue_symbol,
                request.venue_b,
                instruments[1].venue_symbol,
                instrument_a=instruments[0],
                instrument_b=instruments[1],
            )
            cycle = ArbCycle(
                f"canary-{time.time_ns()}",
                pair,
                request.direction,
                request.quantity_base,
            )
            result = replace(result, cycle_id=cycle.cycle_id)
            executor = HedgedExecutor(
                self.exchanges,
                self.store,
                dry_run=False,
                execution_mode="testnet",
                testnet_confirmed=True,
                timeout_seconds=self.timeout_seconds,
                verify_flat_on_close=True,
            )
            opening = await executor.open_cycle(cycle, buy_price=buy_price, sell_price=sell_price)
            result = replace(result, opening=opening, status=opening.status, error=opening.error)
            if opening.status != "OPEN":
                return result
            closing_direction: CanaryDirection = (
                "buy_b_sell_a" if request.direction == "buy_a_sell_b" else "buy_a_sell_b"
            )
            close_buy_price, close_sell_price = await self._protected_prices(
                instruments,
                closing_direction,
                (opening.cycle.filled_qty_a, opening.cycle.filled_qty_b),
                request.max_notional_usd,
            )
            close_attempted = True
            closing = await executor.close_cycle(opening.cycle, buy_price=close_buy_price, sell_price=close_sell_price)
            result = replace(result, status=closing.status, closing=closing, error=closing.error)
            if closing.status == "REJECTED":
                return await self._recover(result, cycle, closing.error or "closing was rejected", close_attempted)
            return result
        except asyncio.CancelledError:
            if cycle is not None:
                await asyncio.shield(self._recover(result, cycle, "canary execution was cancelled", close_attempted))
            raise
        except Exception as exc:
            if cycle is not None:
                return await self._recover(result, cycle, str(exc), close_attempted)
            return replace(result, error=str(exc))

    async def _assert_unblocked(self) -> None:
        if self.store is None:
            raise ValueError("canary requires a durable persistence store")
        unfinished = await self.store.list_unfinished_arbitrage_cycles()
        manual = await self.store.list_arbitrage_cycles(status="MANUAL_REVIEW", limit=1)
        if unfinished or manual:
            blocked = (unfinished or manual)[0]
            raise ValueError(f"canary blocked by persisted cycle {blocked['cycle_id']} ({blocked['status']})")

    async def _recover(self, result: CanaryResult, cycle: ArbCycle, error: str, close_attempted: bool) -> CanaryResult:
        """Retain durable order identities after any possibly submitted opening."""
        result = replace(result, status="RECOVERY", error=f"{cycle.cycle_id}: {error}")
        try:
            saved = await self.store.get_arbitrage_cycle(cycle.cycle_id)
            if saved is None:
                return replace(result, error=f"{result.error}; persisted cycle is unavailable for recovery")
            rows = await self.store.get_arbitrage_cycle_legs(cycle.cycle_id)
            current = result.closing.cycle if result.closing else result.opening.cycle if result.opening else cycle
            current = replace(current, status="manual_review" if saved["status"] == "MANUAL_REVIEW" else "recovery")
            if result.opening is None:
                result = replace(result, opening=self._saved_result(current, rows, closing=False, error=error))
            if close_attempted and result.closing is None:
                result = replace(result, closing=self._saved_result(current, rows, closing=True, error=error))
            if saved["status"] != "MANUAL_REVIEW":
                await self.store.update_arbitrage_cycle(cycle.cycle_id, status="RECOVERY", failure_reason=error)
        except Exception as persistence_error:
            result = replace(result, error=f"{result.error}; recovery persistence failed: {persistence_error}")
        return result

    @staticmethod
    def _saved_result(cycle: ArbCycle, rows: list[dict], *, closing: bool, error: str) -> HedgedExecutionResult:
        legs = []
        for row in rows:
            if (":close:" in row["role"]) != closing:
                continue
            status = row["status"].lower()
            filled = row["filled_qty_base"]
            if filled == 0 and status in {"unknown", "pending_send"}:
                filled = None
            legs.append(
                LegResult(
                    row["role"].split(":", 1)[0],
                    row["venue"],
                    row["client_order_id"],
                    row["venue_order_id"],
                    status,
                    filled,
                    row["avg_price"],
                    row["fee_usd"],
                )
            )
        return HedgedExecutionResult(cycle, tuple(legs), "RECOVERY", error)

    @staticmethod
    def _validate_request(request: CanaryRequest) -> None:
        if request.confirmation != CONFIRMATION_TOKEN:
            raise ValueError(f"confirmation must equal {CONFIRMATION_TOKEN}")
        if request.venue_a == request.venue_b:
            raise ValueError("canary requires two different venues")
        if {request.venue_a, request.venue_b} - SUPPORTED_VENUES:
            raise ValueError("canary supports only Arcus, Hyperliquid, and Binance")
        if request.direction not in {"buy_a_sell_b", "buy_b_sell_a"}:
            raise ValueError("direction must be buy_a_sell_b or buy_b_sell_a")
        if not isfinite(request.quantity_base) or request.quantity_base <= 0:
            raise ValueError("quantity_base must be positive and finite")
        if not isfinite(request.max_notional_usd) or request.max_notional_usd <= 0:
            raise ValueError("max_notional_usd must be positive and finite")

    async def _resolve_instruments(self, request: CanaryRequest) -> tuple[Instrument, Instrument]:
        resolved: list[Instrument] = []
        for venue, requested_symbol in ((request.venue_a, request.symbol_a), (request.venue_b, request.symbol_b)):
            exchange = self.exchanges.get(venue)
            if exchange is None:
                raise ValueError(f"missing initialized exchange: {venue}")
            markets = await exchange.list_markets()
            candidates = [
                instrument
                for instrument in markets
                if instrument.network is NetworkType.TESTNET
                and instrument.venue == venue
                and instrument.market_type == "perp"
                and instrument.base.symbol.upper() == request.base.upper()
                and instrument.listing_status == "trading"
                and not instrument.is_inverse
                and instrument.contract_size == 1
                and instrument.quote.symbol in {"USD", "USDT", "USDC"}
            ]
            if requested_symbol:
                candidates = [item for item in candidates if item.venue_symbol == requested_symbol]
            if not candidates:
                raise ValueError(f"no active testnet {request.base.upper()} perp on {venue}")
            resolved.append(candidates[0])
        return resolved[0], resolved[1]

    async def _validate_adapters(self, instruments: tuple[Instrument, Instrument], request: CanaryRequest) -> None:
        for venue, instrument in zip((request.venue_a, request.venue_b), instruments, strict=True):
            exchange = self.exchanges[venue]
            if exchange.network_type is not NetworkType.TESTNET:
                raise ValueError(f"{venue} is not connected to testnet")
            if not exchange.has_credentials(instrument):
                raise ValueError(f"{venue} private testnet credentials are not configured")
            try:
                await asyncio.wait_for(
                    exchange.fetch_balance(params=exchange.account_params(instrument)), self.timeout_seconds
                )
            except Exception as exc:
                raise ValueError(f"{venue}:{instrument.venue_symbol} testnet account read failed: {exc}") from exc
            capabilities = exchange.order_capabilities(instrument)
            if not capabilities.has_client_order_id or "IOC" not in capabilities.time_in_force:
                raise ValueError(f"{venue} does not support the required client ID + IOC canary order")
            try:
                position = await asyncio.wait_for(exchange.fetch_order_position(instrument), self.timeout_seconds)
                if (
                    not isinstance(position, OrderPositionSnapshot)
                    or position.symbol != instrument.venue_symbol
                    or not isfinite(position.qty_native)
                    or position.qty_native != 0
                ):
                    raise ValueError("a verified zero position is required")
                orders = await asyncio.wait_for(
                    exchange.fetch_open_orders(instrument.venue_symbol, params=exchange.account_params(instrument)),
                    self.timeout_seconds,
                )
                if not isinstance(orders, list) or orders:
                    raise ValueError("a verified empty open-order list is required")
            except Exception as exc:
                raise ValueError(f"{venue}:{instrument.venue_symbol} baseline check failed: {exc}") from exc

    async def _protected_prices(
        self,
        instruments: tuple[Instrument, Instrument],
        direction: CanaryDirection,
        quantities: tuple[float, float],
        max_notional_usd: float,
    ) -> tuple[float, float]:
        books = await self._books(instruments)
        buy_index, sell_index = (0, 1) if direction == "buy_a_sell_b" else (1, 0)
        prices = []
        for index, side in ((buy_index, "buy"), (sell_index, "sell")):
            instrument = instruments[index]
            try:
                prices.append(
                    self._protected_leg_price(instrument, books[index], side, quantities[index], max_notional_usd)
                )
            except Exception as exc:
                raise ValueError(
                    f"{instrument.venue}:{instrument.venue_symbol} {side} protection failed: {exc}"
                ) from exc
        return prices[0], prices[1]

    def _protected_leg_price(
        self, instrument: Instrument, book: dict, side: str, qty_native: float, max_notional_usd: float
    ) -> float:
        quantity = Decimal(str(qty_native))
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("quantity must be positive and finite")
        for value in (instrument.qty_step, instrument.price_step, instrument.min_qty, instrument.min_notional):
            if not isfinite(value) or value < 0:
                raise ValueError("invalid instrument precision or minimum")
        if quantity < Decimal(str(instrument.min_qty)):
            raise ValueError("quantity is below instrument min_qty")
        if instrument.qty_step and quantity % Decimal(str(instrument.qty_step)):
            raise ValueError("quantity does not align with instrument qty_step")
        bids, asks = book.get("bids"), book.get("asks")
        if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
            raise ValueError("a non-empty protected order book is required")
        bid, ask = Decimal(str(bids[0][0])), Decimal(str(asks[0][0]))
        if any(not value.is_finite() or value <= 0 for value in (bid, ask)) or bid >= ask:
            raise ValueError("order book contains invalid or crossed prices")
        levels = asks if side == "buy" else bids
        parsed = [(Decimal(str(level[0])), Decimal(str(level[1]))) for level in levels]
        if any(not value.is_finite() or value <= 0 for level in parsed for value in level):
            raise ValueError("order book contains invalid prices or quantities")
        mid = (bid + ask) / 2
        price = mid * (1 + _LIMIT_OFFSET if side == "buy" else 1 - _LIMIT_OFFSET)
        exchange = self.exchanges[instrument.venue]
        price_step = instrument.price_step
        if instrument.venue == "arcus":
            price_step = exchange.price_tick(instrument.venue_symbol, float(price))
        if not isfinite(price_step) or price_step < 0 or (instrument.venue == "arcus" and price_step == 0):
            raise ValueError("invalid effective price step")
        if price_step:
            step = Decimal(str(price_step))
            rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
            price = (price / step).to_integral_value(rounding=rounding) * step
        if not mid * (1 - _PRICE_TOLERANCE) <= price <= mid * (1 + _PRICE_TOLERANCE):
            raise ValueError("effective price step cannot fit the 0.5% price protection")
        if (side == "buy" and price < ask) or (side == "sell" and price > bid):
            raise ValueError("spread exceeds the 0.5% price protection")
        depth = sum(
            (size for level_price, size in parsed if (level_price <= price if side == "buy" else level_price >= price)),
            Decimal(0),
        )
        if price <= 0 or depth < quantity:
            raise ValueError("insufficient order-book depth inside the 0.5% price protection")
        notional = instrument.quote_notional(qty_native, float(price))
        cap = min(max_notional_usd, _MAX_LEG_NOTIONAL_USD)
        if notional > cap:
            raise ValueError(f"order notional ${notional:.2f} exceeds max_notional_usd ${cap:.2f}")
        if notional < instrument.min_notional:
            raise ValueError("order notional is below instrument min_notional")
        return float(price)

    async def _books(self, instruments: tuple[Instrument, Instrument]) -> tuple[dict, dict]:
        books = []
        for instrument in instruments:
            try:
                books.append(
                    await asyncio.wait_for(
                        self.exchanges[instrument.venue].fetch_orderbook(instrument.venue_symbol, limit=5),
                        self.timeout_seconds,
                    )
                )
            except Exception as exc:
                raise ValueError(f"{instrument.venue}:{instrument.venue_symbol} order-book read failed: {exc}") from exc
        return books[0], books[1]


__all__ = ["CONFIRMATION_TOKEN", "CanaryRequest", "CanaryResult", "TestnetCanary"]
