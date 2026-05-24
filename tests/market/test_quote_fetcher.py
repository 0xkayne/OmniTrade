"""Tests for QuoteFetcher using MockExchange."""

import time

import pytest

from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.mock_backend import MockExchange
from src.market.quote_fetcher import QuoteFetcher

BTC = Asset("BTC")
USDT = Asset("USDT")
ETH = Asset("ETH")


def make_instrument(venue="mock", base=None, quote=None, venue_symbol=None, **kwargs):
    return Instrument(
        venue=venue,
        market_type="spot",
        base=base or BTC,
        quote=quote or USDT,
        venue_symbol=venue_symbol or "BTCUSDT",
        taker_fee_rate=kwargs.pop("taker_fee_rate", 0.001),
        maker_fee_rate=kwargs.pop("maker_fee_rate", 0.0005),
        **kwargs,
    )


@pytest.fixture
def mock_exchange():
    mock = MockExchange("mock")
    mock.set_orderbook("BTCUSDT", bids=[(50000.0, 1.0), (49990.0, 2.0)], asks=[(50010.0, 1.0), (50020.0, 2.0)])
    return mock


@pytest.fixture
def fetcher(mock_exchange):
    return QuoteFetcher({"mock": mock_exchange})


class TestQuoteFetcherFetch:
    """fetch() returns a valid Quote."""

    async def test_fetch_returns_quote_with_top_of_book(self, fetcher):
        instr = make_instrument()
        quote = await fetcher.fetch(instr)
        assert quote is not None
        assert quote.instrument == instr
        assert quote.bid_price == 50000.0
        assert quote.bid_size == 1.0
        assert quote.ask_price == 50010.0
        assert quote.ask_size == 1.0
        assert quote.mid_price == pytest.approx(50005.0)

    async def test_fetch_copies_fee_rates_from_instrument(self, fetcher):
        instr = make_instrument(taker_fee_rate=0.002, maker_fee_rate=0.001)
        quote = await fetcher.fetch(instr)
        assert quote.taker_fee_rate == 0.002
        assert quote.maker_fee_rate == 0.001

    async def test_fetch_sets_fetched_at_to_current_time(self, fetcher):
        instr = make_instrument()
        before = time.time()
        quote = await fetcher.fetch(instr)
        after = time.time()
        assert before <= quote.fetched_at <= after

    async def test_fetch_parses_bids_asks_correctly(self, fetcher):
        instr = make_instrument()
        quote = await fetcher.fetch(instr)
        # _bids and _asks should be list[(float, float)]
        assert isinstance(quote._bids, list)
        assert len(quote._bids) == 2
        assert quote._bids[0] == (50000.0, 1.0)
        assert isinstance(quote._asks, list)
        assert len(quote._asks) == 2
        assert quote._asks[0] == (50010.0, 1.0)


class TestQuoteFetcherFetchMany:
    """fetch_many() concurrency and failure isolation."""

    async def test_fetch_many_returns_correct_count(self, fetcher):
        instrs = [make_instrument(), make_instrument(base=ETH, venue_symbol="ETHUSDT")]
        fetcher._exchanges["mock"].set_orderbook("ETHUSDT", bids=[(3000.0, 1.0)], asks=[(3010.0, 1.0)])
        results = await fetcher.fetch_many(instrs)
        assert len(results) == 2
        assert all(r is not None for r in results)

    async def test_fetch_many_preserves_input_order(self, fetcher):
        instr1 = make_instrument()
        instr2 = make_instrument(base=ETH, venue_symbol="ETHUSDT")
        fetcher._exchanges["mock"].set_orderbook("ETHUSDT", bids=[(3000.0, 1.0)], asks=[(3010.0, 1.0)])
        results = await fetcher.fetch_many([instr1, instr2])
        assert results[0].instrument.base.symbol == "BTC"
        assert results[1].instrument.base.symbol == "ETH"

    async def test_fetch_many_empty_orderbook_does_not_break_batch(self, fetcher):
        instr_good = make_instrument()
        instr_empty = make_instrument(venue_symbol="NONEXISTENT")
        results = await fetcher.fetch_many([instr_good, instr_empty])
        assert len(results) == 2
        assert results[0] is not None
        # Unknown symbol returns empty orderbook (not None) — Quote with zero prices
        assert results[1] is not None
        assert results[1].mid_price == 0.0


class TestQuoteFetcherFetchWithMissingExchange:
    """fetch() raises when exchange not found."""

    async def test_fetch_unknown_venue_raises(self):
        fetcher = QuoteFetcher({})
        instr = make_instrument(venue="nonexistent")
        with pytest.raises(ValueError, match="No exchange adapter"):
            await fetcher.fetch(instr)
