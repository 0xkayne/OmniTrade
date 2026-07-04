"""Tests for WebSocket orderbook cache behavior."""

import pytest

from src.core.base_exchange import NetworkType
from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.orderbook_cache import OrderbookCache

BTC = Asset("BTC")
USDT = Asset("USDT")


def make_instrument(venue: str) -> Instrument:
    return Instrument(
        venue=venue,
        network=NetworkType.TESTNET,
        market_type="spot",
        base=BTC,
        quote=USDT,
        venue_symbol="BTC/USDT",
    )


def make_cache() -> OrderbookCache:
    cache = OrderbookCache.__new__(OrderbookCache)
    cache._max_staleness_ms = 500
    cache._max_silence_sec = 5.0
    cache._ws_exchanges = {}
    cache._cache = {}
    cache._tasks = {}
    cache._stale_keys = set()
    return cache


def test_orderbook_cache_keys_same_symbol_by_venue():
    cache = make_cache()

    cache._apply_update(
        "binance_spot",
        "BTC/USDT",
        {"symbol": "BTC/USDT", "bids": [[50000.0, 1.0]], "asks": [[50010.0, 1.0]]},
    )
    cache._apply_update(
        "okx_spot",
        "BTC/USDT",
        {"symbol": "BTC/USDT", "bids": [[60000.0, 1.0]], "asks": [[60010.0, 1.0]]},
    )

    binance_quote = cache.get_quote(make_instrument("binance"))
    okx_quote = cache.get_quote(make_instrument("okx"))

    assert binance_quote is not None
    assert okx_quote is not None
    assert binance_quote.bid_price == pytest.approx(50000.0)
    assert okx_quote.bid_price == pytest.approx(60000.0)
