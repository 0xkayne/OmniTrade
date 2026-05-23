"""Test fixtures for coordinator tests.

Provides FakeExchange (minimal in-process mock), fake InstrumentRegistry,
fake QuoteFetcher, and fake PersistenceStore.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from src.coordinator.intent import Intent
from src.coordinator.plan import Plan, PlannedLeg
from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.quote import EstimatedFill, Quote

# ---------------------------------------------------------------------------
# FakeExchange — minimal BaseExchange implementation used by all tests
# ---------------------------------------------------------------------------

class FakeExchange:
    """In-process mock exchange that returns canned responses.

    Supports orderbook, balance, create_order, cancel_order, fetch_order.
    All methods are async so they work with real coordinator code.
    """

    def __init__(self, name: str):
        self.name = name
        self._orders: dict[str, dict] = {}
        self._order_counter = 0
        self._balances: dict[str, float] = {"USDT": 100_000.0, "USDC": 100_000.0}
        self._orderbooks: dict[str, dict] = {}
        self._listing_statuses: dict[str, str] = {}
        # Whether create_order should raise
        self._fail_create: bool = False
        self._fail_create_message: str = "network error"
        # Whether cancel_order should raise
        self._fail_cancel: bool = False
        # Whether fetch_order should raise
        self._fail_fetch: bool = False

    # -- configuration helpers for tests --------------------------------

    def set_balance(self, asset: str, amount: float) -> None:
        self._balances[asset] = amount

    def set_orderbook(self, symbol: str, bids: list[list[float]], asks: list[list[float]]) -> None:
        self._orderbooks[symbol] = {"bids": bids, "asks": asks}

    def set_listing_status(self, symbol: str, status: str) -> None:
        self._listing_statuses[symbol] = status

    def set_fail_create(self, fail: bool, message: str = "network error") -> None:
        self._fail_create = fail
        self._fail_create_message = message

    def set_fail_cancel(self, fail: bool) -> None:
        self._fail_cancel = fail

    def set_fail_fetch(self, fail: bool) -> None:
        self._fail_fetch = fail

    # -- exchange-like methods -----------------------------------------

    async def connect(self) -> None:
        pass

    async def fetch_balance(self) -> dict:
        return {"free": dict(self._balances)}

    async def fetch_orderbook(self, symbol: str, limit: int = 10) -> dict:
        return self._orderbooks.get(symbol, {"bids": [], "asks": []})

    async def create_order(
        self, symbol: str, order_type: str, side: str, amount: float, price: float | None = None
    ) -> dict:
        if self._fail_create:
            raise RuntimeError(self._fail_create_message)
        self._order_counter += 1
        oid = f"fake-{self.name}-{self._order_counter}"
        self._orders[oid] = {
            "id": oid,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "amount": amount,
            "price": price,
            "status": "open",
            "filled": 0.0,
            "average": None,
            "fee": {"cost": 1.25, "currency": "USDT"},
            "timestamp": time.time(),
        }
        return dict(self._orders[oid])

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> dict:
        if self._fail_cancel:
            raise RuntimeError("cancel failed")
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"order {order_id} not found")
        order["status"] = "canceled"
        return dict(order)

    async def fetch_order(self, order_id: str, symbol: str | None = None) -> dict:
        if self._fail_fetch:
            raise RuntimeError("fetch order failed")
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"order {order_id} not found")
        # Simulate fill on first fetch
        if order["status"] == "open":
            order["status"] = "closed"
            order["filled"] = order["amount"]
            order["average"] = 50000.0
        return dict(order)

    async def close(self) -> None:
        pass

    def get_order(self, order_id: str) -> dict | None:
        return self._orders.get(order_id)


# ---------------------------------------------------------------------------
# Reusable Instrument builders
# ---------------------------------------------------------------------------

BTC = Asset("BTC")
ETH = Asset("ETH")
USDT = Asset("USDT")
USDC = Asset("USDC")


def make_btc_usdt_spot(venue: str = "binance") -> Instrument:
    return Instrument(
        venue=venue,
        market_type="spot",
        base=BTC,
        quote=USDT,
        venue_symbol="BTCUSDT",
        min_qty=0.00001,
        qty_step=0.00001,
        price_step=0.01,
        taker_fee_rate=0.001,
        maker_fee_rate=0.0008,
        listing_status="trading",
    )


def make_btc_usdc_spot(venue: str = "binance") -> Instrument:
    return Instrument(
        venue=venue,
        market_type="spot",
        base=BTC,
        quote=USDC,
        venue_symbol="BTCUSDC",
        min_qty=0.00001,
        qty_step=0.00001,
        price_step=0.01,
        taker_fee_rate=0.001,
        maker_fee_rate=0.0008,
        listing_status="trading",
    )


def make_eth_usdt_spot(venue: str = "binance") -> Instrument:
    return Instrument(
        venue=venue,
        market_type="spot",
        base=ETH,
        quote=USDT,
        venue_symbol="ETHUSDT",
        min_qty=0.0001,
        qty_step=0.0001,
        price_step=0.01,
        taker_fee_rate=0.001,
        maker_fee_rate=0.0008,
        listing_status="trading",
    )


def make_quote(instrument: Instrument, mid: float = 50000.0) -> Quote:
    bids = [(mid - 0.01, 10.0), (mid - 0.50, 20.0), (mid - 1.00, 50.0)]
    asks = [(mid + 0.01, 10.0), (mid + 0.50, 20.0), (mid + 1.00, 50.0)]
    return Quote(
        instrument=instrument,
        fetched_at=time.time(),
        bid_price=bids[0][0],
        bid_size=bids[0][1],
        ask_price=asks[0][0],
        ask_size=asks[0][1],
        mid_price=mid,
        taker_fee_rate=instrument.taker_fee_rate,
        maker_fee_rate=instrument.maker_fee_rate,
        _bids=bids,
        _asks=asks,
    )


def make_shallow_quote(instrument: Instrument, mid: float = 50000.0) -> Quote:
    """A quote with very thin depth — fill estimates will show filled_fully=False."""
    bids = [(mid - 0.01, 0.0001)]
    asks = [(mid + 0.01, 0.0001)]
    return Quote(
        instrument=instrument,
        fetched_at=time.time(),
        bid_price=bids[0][0],
        bid_size=bids[0][1],
        ask_price=asks[0][0],
        ask_size=asks[0][1],
        mid_price=mid,
        taker_fee_rate=instrument.taker_fee_rate,
        maker_fee_rate=instrument.maker_fee_rate,
        _bids=bids,
        _asks=asks,
    )


# ---------------------------------------------------------------------------
# Default Intent builder
# ---------------------------------------------------------------------------

def make_intent(
    intent_id: str = "intent-001",
    base: str = "BTC",
    product: str = "spot",
    side: str = "buy",
    total_notional_usd: float = 1000.0,
    split: dict[str, float] | None = None,
    max_slippage_pct: float | None = None,
    max_fee_usd: float | None = None,
    execute_timeout_seconds: int = 30,
) -> Intent:
    return Intent(
        intent_id=intent_id,
        base=base,
        quote_preference=["USDT", "USDC"],
        product=product,
        side=side,
        order_type="market",
        total_notional_usd=total_notional_usd,
        split=split or {"binance": 0.5, "hyperliquid": 0.5},
        max_slippage_pct=max_slippage_pct,
        max_fee_usd=max_fee_usd,
        execute_timeout_seconds=execute_timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_binance() -> FakeExchange:
    fe = FakeExchange("binance")
    fe.set_orderbook("BTCUSDT", [[49999, 10], [49990, 20]], [[50001, 10], [50010, 20]])
    fe.set_orderbook("BTCUSDC", [[49998, 10], [49980, 20]], [[50002, 10], [50020, 20]])
    fe.set_balance("USDT", 100_000.0)
    fe.set_balance("USDC", 100_000.0)
    return fe


@pytest.fixture
def fake_hyperliquid() -> FakeExchange:
    fe = FakeExchange("hyperliquid")
    fe.set_orderbook("BTCUSDT", [[49998, 10], [49980, 20]], [[50002, 10], [50020, 20]])
    fe.set_balance("USDT", 50_000.0)
    return fe


@pytest.fixture
def fake_exchanges(fake_binance, fake_hyperliquid) -> dict[str, FakeExchange]:
    return {"binance": fake_binance, "hyperliquid": fake_hyperliquid}


@pytest.fixture
def btc_usdt_binance() -> Instrument:
    return make_btc_usdt_spot("binance")


@pytest.fixture
def btc_usdc_binance() -> Instrument:
    return make_btc_usdc_spot("binance")


@pytest.fixture
def btc_usdt_hyperliquid() -> Instrument:
    return make_btc_usdt_spot("hyperliquid")


@pytest.fixture
def sample_registry(btc_usdt_binance, btc_usdc_binance, btc_usdt_hyperliquid) -> InstrumentRegistry:
    from src.market.registry import InstrumentRegistry

    reg = InstrumentRegistry()
    reg.add(btc_usdt_binance)
    reg.add(btc_usdc_binance)
    reg.add(btc_usdt_hyperliquid)
    # Add ETH instruments too
    reg.add(make_eth_usdt_spot("binance"))
    return reg


class FakeQuoteFetcher:
    """Returns canned Quotes without hitting a real exchange."""

    def __init__(self, quotes: dict[tuple, Quote] | None = None):
        self._quotes: dict[tuple, Quote] = quotes or {}

    def set_quote(self, instrument: Instrument, quote: Quote) -> None:
        self._quotes[instrument.instrument_key] = quote

    async def fetch(self, instrument: Instrument) -> Quote:
        key = instrument.instrument_key
        if key in self._quotes:
            return self._quotes[key]
        return make_quote(instrument)


class FakePersistenceStore:
    """In-memory store that implements the PersistenceStore interface for testing."""

    def __init__(self):
        self.intents: dict[str, dict] = {}
        self.legs: dict[str, dict] = {}
        self._blocked: bool = False

    def set_blocked(self, blocked: bool) -> None:
        self._blocked = blocked

    async def is_blocked_by_needs_manual(self) -> bool:
        return self._blocked

    async def create_intent(self, intent: Intent, status: str = "PENDING") -> None:
        self.intents[intent.intent_id] = {
            "intent_id": intent.intent_id,
            "status": status,
            "raw_intent_json": str(intent),
            "created_at": intent.created_at,
            "updated_at": intent.created_at,
        }

    async def update_intent_status(self, intent_id: str, status: str) -> None:
        if intent_id in self.intents:
            self.intents[intent_id]["status"] = status

    async def get_intent_status(self, intent_id: str) -> str | None:
        entry = self.intents.get(intent_id)
        return entry["status"] if entry else None

    async def create_leg(
        self,
        leg_id: str,
        intent_id: str,
        venue: str,
        instrument_venue_symbol: str,
        instrument_base: str,
        instrument_quote: str,
        instrument_market_type: str,
        quote_preference_matched: str | None,
        planned_notional_usd: float,
        planned_qty_base: float,
        funding_rate_at_plan: float | None = None,
        next_funding_time_at_plan: float | None = None,
        instrument_selection_log: str | None = None,
    ) -> None:
        self.legs[leg_id] = {
            "leg_id": leg_id,
            "intent_id": intent_id,
            "venue": venue,
            "instrument_venue_symbol": instrument_venue_symbol,
            "instrument_base": instrument_base,
            "instrument_quote": instrument_quote,
            "instrument_market_type": instrument_market_type,
            "quote_preference_matched": quote_preference_matched,
            "planned_notional_usd": planned_notional_usd,
            "planned_qty_base": planned_qty_base,
            "status": "PENDING_SEND",
            "sent_at": None,
            "order_id": None,
            "filled_amount": None,
            "avg_price": None,
            "fee_usd": None,
            "error_msg": None,
            "compensation_order_id": None,
            "compensation_filled_amount": None,
            "instrument_selection_log": instrument_selection_log,
            "funding_rate_at_plan": funding_rate_at_plan,
            "next_funding_time_at_plan": next_funding_time_at_plan,
        }

    async def update_leg(self, leg_id: str, **kwargs: Any) -> None:
        if leg_id in self.legs:
            self.legs[leg_id].update(kwargs)

    async def get_leg(self, leg_id: str) -> dict | None:
        return self.legs.get(leg_id)


@pytest.fixture
def quote_fetcher(fake_exchanges) -> FakeQuoteFetcher:
    return FakeQuoteFetcher()


@pytest.fixture
def fake_store() -> FakePersistenceStore:
    return FakePersistenceStore()
