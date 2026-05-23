"""Tests for InstrumentRegistry."""

import pytest

from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.registry import InstrumentRegistry


BTC = Asset("BTC")
ETH = Asset("ETH")
USDT = Asset("USDT")
USDC = Asset("USDC")


def make_binance_spot_btc():
    return Instrument(
        venue="binance", market_type="spot", base=BTC, quote=USDT,
        venue_symbol="BTCUSDT",
    )


def make_binance_perp_btc():
    return Instrument(
        venue="binance", market_type="perp", base=BTC, quote=USDT,
        venue_symbol="BTCUSDT_PERP",
    )


def make_hyperliquid_perp_btc():
    return Instrument(
        venue="hyperliquid", market_type="perp", base=BTC, quote=USDC,
        venue_symbol="BTC-USD",
    )


def make_binance_spot_eth():
    return Instrument(
        venue="binance", market_type="spot", base=ETH, quote=USDT,
        venue_symbol="ETHUSDT",
    )


def make_binance_spot_btc_usdc():
    return Instrument(
        venue="binance", market_type="spot", base=BTC, quote=USDC,
        venue_symbol="BTCUSDC",
    )


class TestListInstruments:
    """Filter combinations for list_instruments."""

    @pytest.fixture
    def registry(self):
        reg = InstrumentRegistry()
        reg._instruments = {
            make_binance_spot_btc().instrument_key: make_binance_spot_btc(),
            make_binance_perp_btc().instrument_key: make_binance_perp_btc(),
            make_hyperliquid_perp_btc().instrument_key: make_hyperliquid_perp_btc(),
            make_binance_spot_eth().instrument_key: make_binance_spot_eth(),
            make_binance_spot_btc_usdc().instrument_key: make_binance_spot_btc_usdc(),
        }
        return reg

    def test_list_all_no_filters(self, registry):
        result = registry.list_instruments()
        assert len(result) == 5

    def test_filter_by_venue(self, registry):
        result = registry.list_instruments(venue="binance")
        assert len(result) == 4
        assert all(i.venue == "binance" for i in result)

    def test_filter_by_market_type(self, registry):
        result = registry.list_instruments(market_type="perp")
        assert len(result) == 2
        assert all(i.market_type == "perp" for i in result)

    def test_filter_by_base(self, registry):
        result = registry.list_instruments(base="ETH")
        assert len(result) == 1
        assert result[0].base.symbol == "ETH"

    def test_filter_by_venue_and_market_type_and_base(self, registry):
        result = registry.list_instruments(venue="binance", market_type="spot", base="BTC")
        assert len(result) == 2  # BTCUSDT + BTCUSDC
        assert all(i.venue == "binance" for i in result)
        assert all(i.market_type == "spot" for i in result)
        assert all(i.base.symbol == "BTC" for i in result)

    def test_empty_registry_returns_empty_list(self):
        reg = InstrumentRegistry()
        result = reg.list_instruments()
        assert result == []


class TestFindOne:
    """find_one with quote preference matching and fallback."""

    @pytest.fixture
    def registry(self):
        reg = InstrumentRegistry()
        reg._instruments = {
            make_binance_spot_btc().instrument_key: make_binance_spot_btc(),
            make_binance_spot_btc_usdc().instrument_key: make_binance_spot_btc_usdc(),
            make_binance_perp_btc().instrument_key: make_binance_perp_btc(),
        }
        return reg

    def test_find_one_prefers_first_quote(self, registry):
        result = registry.find_one(
            base="BTC", venue="binance", market_type="spot",
            quote_preference=["USDT", "USDC"],
        )
        assert result is not None
        assert result.quote.symbol == "USDT"

    def test_find_one_falls_back_to_second_quote(self, registry):
        # Only USDC instrument, no USDT spot
        reg = InstrumentRegistry()
        reg._instruments = {
            make_binance_spot_btc_usdc().instrument_key: make_binance_spot_btc_usdc(),
        }
        result = reg.find_one(
            base="BTC", venue="binance", market_type="spot",
            quote_preference=["USDT", "USDC"],
        )
        assert result is not None
        assert result.quote.symbol == "USDC"

    def test_find_one_no_match_returns_none(self, registry):
        result = registry.find_one(
            base="BTC", venue="binance", market_type="spot",
            quote_preference=["DAI"],
        )
        assert result is None

    def test_find_one_empty_registry_returns_none(self):
        reg = InstrumentRegistry()
        result = reg.find_one(
            base="BTC", venue="binance", market_type="spot",
            quote_preference=["USDT"],
        )
        assert result is None


class TestStaleDetection:
    """is_stale behavior."""

    def test_not_loaded_is_stale(self):
        reg = InstrumentRegistry(ttl_hours=24)
        assert reg.is_stale() is True

    def test_recently_loaded_is_not_stale(self):
        import time
        reg = InstrumentRegistry(ttl_hours=24)
        reg._loaded_at = time.time()
        assert reg.is_stale() is False

    def test_expired_is_stale(self):
        import time
        reg = InstrumentRegistry(ttl_hours=24)
        # Simulate load 25 hours ago
        reg._loaded_at = time.time() - (25 * 3600)
        assert reg.is_stale() is True


class TestVenueCount:
    """venue_count and instrument_count properties."""

    def test_venue_count_empty(self):
        reg = InstrumentRegistry()
        assert reg.venue_count == 0
        assert reg.instrument_count == 0

    def test_venue_count_multiple_venues(self):
        reg = InstrumentRegistry()
        reg._instruments = {
            make_binance_spot_btc().instrument_key: make_binance_spot_btc(),
            make_hyperliquid_perp_btc().instrument_key: make_hyperliquid_perp_btc(),
        }
        assert reg.venue_count == 2
        assert reg.instrument_count == 2
