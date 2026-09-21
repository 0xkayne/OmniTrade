import time

import pytest

from src.arbitrage.scanner import CrossVenueArbitrageScanner, ScannerConfig
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.market.pair_matcher import CrossVenuePair
from src.market.quote import Quote


def _instrument(venue: str) -> Instrument:
    return Instrument(
        venue=venue,
        network=NetworkType.TESTNET,
        market_type="perp",
        base=Asset("BTC"),
        quote=Asset("USD"),
        venue_symbol="BTC-USD",
        taker_fee_rate=0.0001,
    )


def _quote(instrument: Instrument, bid: float, ask: float) -> Quote:
    now = time.time()
    return Quote(
        instrument=instrument,
        fetched_at=now,
        exchange_at=now,
        bid_price=bid,
        bid_size=1.0,
        ask_price=ask,
        ask_size=1.0,
        mid_price=(bid + ask) / 2,
        taker_fee_rate=instrument.taker_fee_rate,
        maker_fee_rate=instrument.maker_fee_rate,
        _bids=[(bid, 1.0)],
        _asks=[(ask, 1.0)],
    )


class _QuoteFetcher:
    def __init__(self, quotes: dict[str, Quote]):
        self.quotes = quotes
        self.calls: list[str] = []

    async def fetch(self, instrument: Instrument, depth: int) -> Quote:
        self.calls.append(instrument.venue)
        return self.quotes[instrument.venue]


@pytest.mark.asyncio
async def test_scanner_fetches_both_venues_and_returns_best_direction() -> None:
    arcus = _instrument("arcus")
    binance = _instrument("binance")
    pair = CrossVenuePair("BTC", "arcus", "binance", arcus, binance)
    fetcher = _QuoteFetcher({"arcus": _quote(arcus, 100, 101), "binance": _quote(binance, 104, 105)})

    opportunities = await CrossVenueArbitrageScanner(
        fetcher,
        ScannerConfig(quantity_base=1, latency_reserve_bps=0),
    ).scan([pair])

    assert fetcher.calls == ["arcus", "binance"]
    assert len(opportunities) == 1
    assert opportunities[0].direction == "buy_a_sell_b"
    assert opportunities[0].buy_venue == "arcus"
    assert opportunities[0].sell_venue == "binance"


@pytest.mark.asyncio
async def test_scanner_sorts_multiple_pairs_by_net_edge() -> None:
    arcus = _instrument("arcus")
    binance = _instrument("binance")
    hyperliquid = _instrument("hyperliquid")
    fetcher = _QuoteFetcher(
        {
            "arcus": _quote(arcus, 100, 101),
            "binance": _quote(binance, 101, 102),
            "hyperliquid": _quote(hyperliquid, 106, 107),
        }
    )
    pairs = [
        CrossVenuePair("BTC", "arcus", "binance", arcus, binance),
        CrossVenuePair("BTC", "arcus", "hyperliquid", arcus, hyperliquid),
    ]

    opportunities = await CrossVenueArbitrageScanner(
        fetcher,
        ScannerConfig(quantity_base=1, latency_reserve_bps=0),
    ).scan(pairs)

    assert [item.sell_venue for item in opportunities] == ["hyperliquid", "binance"]
