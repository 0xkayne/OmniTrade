"""Test fixtures for coordinator tests.

Provides MockExchange (canonical test double), real QuoteFetcher, real
PersistenceStore(:memory:), and reusable helper builders.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from src.coordinator.intent import Intent
from src.coordinator.plan import Plan, PlannedLeg
from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.mock_backend import MockExchange
from src.market.quote import EstimatedFill, Quote
from src.market.quote_fetcher import QuoteFetcher
from src.persistence.store import PersistenceStore

# ---------------------------------------------------------------------------
# Reusable Asset constants
# ---------------------------------------------------------------------------

BTC = Asset("BTC")
ETH = Asset("ETH")
USDT = Asset("USDT")
USDC = Asset("USDC")


# ---------------------------------------------------------------------------
# Reusable Instrument builders
# ---------------------------------------------------------------------------

def make_btc_usdt_spot(venue: str = "binance", min_notional_usd: float = 0.0) -> Instrument:
    return Instrument(
        venue=venue,
        market_type="spot",
        base=BTC,
        quote=USDT,
        venue_symbol="BTCUSDT",
        min_qty=0.00001,
        qty_step=0.00001,
        price_step=0.01,
        min_notional_usd=min_notional_usd,
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


# ---------------------------------------------------------------------------
# Quote builders
# ---------------------------------------------------------------------------

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


def set_quote_via_orderbook(exchange: MockExchange, symbol: str, mid: float = 50000.0, spread: float = 0.01) -> None:
    """Configure exchange orderbook to produce Quotes equivalent to make_quote(instrument, mid=mid)."""
    exchange.set_orderbook(
        symbol,
        bids=[(mid - spread, 10.0), (mid - 0.50, 20.0), (mid - 1.00, 50.0)],
        asks=[(mid + spread, 10.0), (mid + 0.50, 20.0), (mid + 1.00, 50.0)],
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
def fake_binance() -> MockExchange:
    m = MockExchange("binance")
    m.set_orderbook("BTCUSDT", [[49999, 10], [49990, 20]], [[50001, 10], [50010, 20]])
    m.set_orderbook("BTCUSDC", [[49998, 10], [49980, 20]], [[50002, 10], [50020, 20]])
    m.set_balance("USDT", 100_000.0)
    m.set_balance("USDC", 100_000.0)
    return m


@pytest.fixture
def fake_hyperliquid() -> MockExchange:
    m = MockExchange("hyperliquid")
    m.set_orderbook("BTCUSDT", [[49998, 10], [49980, 20]], [[50002, 10], [50020, 20]])
    m.set_balance("USDT", 50_000.0)
    return m


@pytest.fixture
def fake_exchanges(fake_binance, fake_hyperliquid) -> dict[str, MockExchange]:
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
def sample_registry(btc_usdt_binance, btc_usdc_binance, btc_usdt_hyperliquid):
    from src.market.registry import InstrumentRegistry

    reg = InstrumentRegistry()
    reg.add(btc_usdt_binance)
    reg.add(btc_usdc_binance)
    reg.add(btc_usdt_hyperliquid)
    reg.add(make_eth_usdt_spot("binance"))
    return reg


@pytest.fixture
def quote_fetcher(fake_exchanges) -> QuoteFetcher:
    return QuoteFetcher(fake_exchanges)


@pytest.fixture
async def fake_store(tmp_path) -> PersistenceStore:
    store = PersistenceStore(Path(":memory:"), tmp_path / "jsonl")
    await store.initialize()
    yield store
    await store.close()
