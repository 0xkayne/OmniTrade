"""Explicit testnet canary execution for one bounded hedge cycle."""

from __future__ import annotations

import time
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

from src.exchange.base import BaseExchange
from src.market.instrument import Instrument, NetworkType

from .executor import HedgedExecutionResult, HedgedExecutor
from .models import ArbCycle, ArbPair

CanaryDirection = Literal["buy_a_sell_b", "buy_b_sell_a"]
CONFIRMATION_TOKEN = "TESTNET_CANARY"
SUPPORTED_VENUES = frozenset({"arcus", "hyperliquid", "binance"})


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


class TestnetCanary:
    """Run exactly one explicitly confirmed testnet cycle, then close it."""

    def __init__(self, exchanges: dict[str, BaseExchange], store: Any, *, timeout_seconds: float = 3.0):
        self.exchanges = exchanges
        self.store = store
        self.timeout_seconds = timeout_seconds

    async def run(self, request: CanaryRequest) -> CanaryResult:
        try:
            self._validate_request(request)
            instruments = await self._resolve_instruments(request)
            await self._validate_adapters(instruments, request)
            buy_price, sell_price = await self._protected_prices(instruments, request.direction)
            closing_direction: CanaryDirection = (
                "buy_b_sell_a" if request.direction == "buy_a_sell_b" else "buy_a_sell_b"
            )
            close_buy_price, close_sell_price = await self._protected_prices(
                instruments, closing_direction
            )
            notional = request.quantity_base * max(buy_price, sell_price)
            if notional > request.max_notional_usd:
                raise ValueError(
                    f"estimated notional ${notional:.2f} exceeds max_notional_usd ${request.max_notional_usd:.2f}"
                )
            pair = ArbPair(
                request.base.upper(), "perp", request.venue_a, instruments[0].venue_symbol,
                request.venue_b, instruments[1].venue_symbol,
                instrument_a=instruments[0], instrument_b=instruments[1],
            )
            cycle = ArbCycle(
                f"canary-{time.time_ns()}", pair, request.direction, request.quantity_base,
            )
            executor = HedgedExecutor(
                self.exchanges, self.store, dry_run=False, execution_mode="testnet",
                testnet_confirmed=True, timeout_seconds=self.timeout_seconds,
            )
            opening = await executor.open_cycle(cycle, buy_price=buy_price, sell_price=sell_price)
            if opening.status != "OPEN":
                return CanaryResult(
                    opening.status, request.direction, request.venue_a, request.venue_b,
                    pair.symbol_a, pair.symbol_b, request.quantity_base, buy_price, sell_price,
                    opening=opening, error=opening.error,
                )
            closing = await executor.close_cycle(
                opening.cycle, buy_price=close_buy_price, sell_price=close_sell_price
            )
            return CanaryResult(
                closing.status, request.direction, request.venue_a, request.venue_b,
                pair.symbol_a, pair.symbol_b, request.quantity_base, buy_price, sell_price,
                opening=opening, closing=closing, error=closing.error,
            )
        except Exception as exc:
            return CanaryResult(
                "REJECTED", request.direction, request.venue_a, request.venue_b,
                request.symbol_a or "", request.symbol_b or "", request.quantity_base, error=str(exc),
            )

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
        for venue, requested_symbol in (
            (request.venue_a, request.symbol_a), (request.venue_b, request.symbol_b)
        ):
            exchange = self.exchanges.get(venue)
            if exchange is None:
                raise ValueError(f"missing initialized exchange: {venue}")
            markets = await exchange.list_markets()
            candidates = [
                instrument for instrument in markets
                if instrument.network is NetworkType.TESTNET
                and instrument.market_type == "perp"
                and instrument.base.symbol.upper() == request.base.upper()
                and instrument.listing_status == "trading"
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
            if not self._has_credentials(exchange):
                raise ValueError(f"{venue} private testnet credentials are not configured")
            try:
                await exchange.fetch_balance()
            except Exception as exc:
                raise ValueError(f"{venue} private testnet account check failed: {exc}") from exc
            capabilities = exchange.order_capabilities(instrument)
            if not capabilities.has_client_order_id or "IOC" not in capabilities.time_in_force:
                raise ValueError(f"{venue} does not support the required client ID + IOC canary order")

    @staticmethod
    def _has_credentials(exchange: BaseExchange) -> bool:
        if exchange.name == "arcus":
            return bool(getattr(exchange, "api_key", None) and getattr(exchange, "_signer", None))
        adapter = getattr(exchange, "ccxt_exchange", None)
        if adapter is None:
            return False
        if exchange.name == "hyperliquid":
            return bool(getattr(adapter, "privateKey", None))
        return bool(getattr(adapter, "apiKey", None) and getattr(adapter, "secret", None))

    async def _protected_prices(
        self, instruments: tuple[Instrument, Instrument], direction: CanaryDirection
    ) -> tuple[float, float]:
        books = await self._books(instruments)
        buy_index, sell_index = (0, 1) if direction == "buy_a_sell_b" else (1, 0)
        buy_asks = books[buy_index].get("asks") or []
        sell_bids = books[sell_index].get("bids") or []
        if not buy_asks or not sell_bids:
            raise ValueError("both venues need a non-empty protected order book")
        buy_price = float(buy_asks[0][0])
        sell_price = float(sell_bids[0][0])
        if not isfinite(buy_price) or not isfinite(sell_price) or buy_price <= 0 or sell_price <= 0:
            raise ValueError("order book contains invalid protected prices")
        return buy_price, sell_price

    async def _books(self, instruments: tuple[Instrument, Instrument]) -> tuple[dict, dict]:
        return (
            await self.exchanges[instruments[0].venue].fetch_orderbook(instruments[0].venue_symbol, limit=5),
            await self.exchanges[instruments[1].venue].fetch_orderbook(instruments[1].venue_symbol, limit=5),
        )


__all__ = ["CONFIRMATION_TOKEN", "CanaryRequest", "CanaryResult", "TestnetCanary"]
