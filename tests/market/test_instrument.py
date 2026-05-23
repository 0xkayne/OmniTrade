"""Tests for Instrument dataclass."""

import pytest

from src.market.asset import Asset
from src.market.instrument import Instrument

BTC = Asset("BTC")
USDT = Asset("USDT")
USDC = Asset("USDC", kind="crypto")


class TestInstrumentRoundQty:
    """round_qty edge cases."""

    def test_round_qty_normal(self):
        instr = Instrument(
            venue="binance", market_type="spot", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT", min_qty=0.00001, qty_step=0.00001,
        )
        assert instr.round_qty(0.000015) == 0.00002  # round(1.5) = 2 (half-even)
        assert instr.round_qty(0.000014) == 0.00001

    def test_round_qty_zero_step_returns_input(self):
        instr = Instrument(
            venue="hyperliquid", market_type="perp", base=BTC, quote=USDC,
            venue_symbol="BTC-USD", qty_step=0.0,
        )
        assert instr.round_qty(0.123456789) == pytest.approx(0.123456789)

    def test_round_qty_respects_min_qty(self):
        instr = Instrument(
            venue="binance", market_type="spot", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT", min_qty=0.001, qty_step=0.00001,
        )
        result = instr.round_qty(0.000001)
        assert result == 0.001

    def test_round_qty_fractional_step(self):
        instr = Instrument(
            venue="binance", market_type="spot", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT", min_qty=0.0, qty_step=0.00005,
        )
        assert instr.round_qty(0.00006) == 0.00005
        assert instr.round_qty(0.000075) == 0.00005
        assert instr.round_qty(0.00008) == 0.00010


class TestInstrumentRoundPrice:
    """round_price edge cases."""

    def test_round_price_normal(self):
        instr = Instrument(
            venue="binance", market_type="spot", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT", price_step=0.01,
        )
        assert instr.round_price(50000.015) == pytest.approx(50000.02)  # round(5000001.5) = 5000002 (half-even)

    def test_round_price_zero_step_returns_input(self):
        instr = Instrument(
            venue="hyperliquid", market_type="perp", base=BTC, quote=USDC,
            venue_symbol="BTC-USD", price_step=0.0,
        )
        assert instr.round_price(50000.12345) == pytest.approx(50000.12345)


class TestInstrumentKey:
    """instrument_key uniqueness tests."""

    def test_instrument_key_returns_tuple(self):
        instr = Instrument(
            venue="binance", market_type="spot", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT",
        )
        key = instr.instrument_key
        assert key == ("binance", "spot", "BTC", "USDT")
        assert isinstance(key, tuple)

    def test_instrument_key_unique_per_venue_symbol_pair(self):
        instr1 = Instrument(
            venue="binance", market_type="spot", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT",
        )
        instr2 = Instrument(
            venue="binance", market_type="perp", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT_PERP",
        )
        assert instr1.instrument_key != instr2.instrument_key

    def test_static_key_constructor_matches(self):
        key1 = Instrument.key("binance", "spot", "BTC", "USDT")
        key2 = Instrument.key("binance", "spot", "BTC", "USDT")
        assert key1 == key2
        assert isinstance(key1, tuple)


class TestInstrumentFrozen:
    """Instrument is a frozen dataclass."""

    def test_frozen_cannot_set_attribute(self):
        instr = Instrument(
            venue="binance", market_type="spot", base=BTC, quote=USDT,
            venue_symbol="BTCUSDT",
        )
        with pytest.raises(Exception):
            instr.venue = "hyperliquid"
