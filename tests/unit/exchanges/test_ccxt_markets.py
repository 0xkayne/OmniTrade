"""Unit tests for CCXTExchange.list_markets() — no network required."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.exchanges.ccxt_exchange import CCXTExchange
from src.market.asset import Asset
from src.market.instrument import Instrument


def _fake_ccxt_markets(*markets):
    """Build a dict that looks like ccxt_exchange.markets."""
    result = {}
    for m in markets:
        result[m["symbol"]] = m
    return result


def _make_ccxt_exchange_with_markets(markets):
    """Create a CCXTExchange with a fake ccxt_exchange.markets attached."""
    exchange = CCXTExchange(
        "testvenue",
        config={
            "type": "ccxt",
            "fees": {"taker": 0.001, "maker": 0.0005},
            "networks": {
                "testnet": {
                    "rest_base_url": "https://test.example.com",
                    "websocket_url": "wss://test.example.com",
                }
            },
        },
        secrets={},
    )
    # Fake the ccxt instance — just need a .markets attribute
    exchange.ccxt_exchange = SimpleNamespace(markets=markets)
    return exchange


class TestListMarketsSpotMapping:
    async def test_active_spot_market_converted(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {"amount": {"min": 0.00001}},
                "precision": {"amount": 0.00001, "price": 0.01},
            }
        ))
        result = await exchange.list_markets()
        assert len(result) == 1
        inst = result[0]
        assert isinstance(inst, Instrument)
        assert inst.venue == "testvenue"
        assert inst.market_type == "spot"
        assert inst.base == Asset("BTC")
        assert inst.quote == Asset("USDT")
        assert inst.venue_symbol == "BTC/USDT"
        assert inst.min_qty == 0.00001
        assert inst.qty_step == 0.00001
        assert inst.price_step == 0.01
        assert inst.taker_fee_rate == 0.001
        assert inst.maker_fee_rate == 0.0005
        assert inst.listing_status == "trading"


class TestListMarketsPerpMapping:
    async def test_active_swap_maps_to_perp(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "ETH/USDT:USDT",
                "base": "ETH",
                "quote": "USDT",
                "type": "swap",
                "active": True,
                "limits": {"amount": {"min": 0.001}},
                "precision": {"amount": 0.001, "price": 0.05},
            }
        ))
        result = await exchange.list_markets()
        assert len(result) == 1
        assert result[0].market_type == "perp"


class TestListMarketsInactiveFiltered:
    async def test_inactive_skipped(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                "active": False,
                "limits": {},
                "precision": {},
            },
            {
                "symbol": "ETH/USDT",
                "base": "ETH",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {},
                "precision": {},
            },
        ))
        result = await exchange.list_markets()
        assert len(result) == 1
        assert result[0].venue_symbol == "ETH/USDT"

    async def test_active_field_missing_treated_as_inactive(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                # no "active" key
                "limits": {},
                "precision": {},
            }
        ))
        result = await exchange.list_markets()
        assert len(result) == 0


class TestListMarketsEdgeCases:
    async def test_empty_when_not_connected(self):
        exchange = CCXTExchange(
            "testvenue",
            config={
                "type": "ccxt",
                "fees": {},
                "networks": {
                    "testnet": {
                        "rest_base_url": "https://test.example.com",
                        "websocket_url": "wss://test.example.com",
                    }
                },
            },
            secrets={},
        )
        # ccxt_exchange is None (not connected)
        result = await exchange.list_markets()
        assert result == []

    async def test_empty_when_no_markets_attr(self):
        exchange = _make_ccxt_exchange_with_markets({})
        result = await exchange.list_markets()
        assert result == []

    async def test_options_and_futures_skipped(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "BTC/USDT:USDT-251226-80000-C",
                "base": "BTC",
                "quote": "USDT",
                "type": "option",
                "active": True,
                "limits": {},
                "precision": {},
            },
            {
                "symbol": "BTC/USDT:USDT-251226",
                "base": "BTC",
                "quote": "USDT",
                "type": "future",
                "active": True,
                "limits": {},
                "precision": {},
            },
        ))
        result = await exchange.list_markets()
        assert len(result) == 0

    async def test_none_precision_fields_default_to_zero(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": None,
                "precision": None,
            }
        ))
        result = await exchange.list_markets()
        assert len(result) == 1
        assert result[0].min_qty == 0.0
        assert result[0].qty_step == 0.0
        assert result[0].price_step == 0.0

    async def test_one_bad_market_does_not_break_entire_load(self):
        """A market entry that raises during conversion should be skipped."""
        # A market missing 'base' key will raise KeyError — should be caught per-market
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "GOOD/USDT",
                "base": "GOOD",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {},
                "precision": {},
            },
            {
                "symbol": "BAD/USDT",
                # missing "base" key → KeyError
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {},
                "precision": {},
            },
        ))
        result = await exchange.list_markets()
        assert len(result) == 1
        assert result[0].venue_symbol == "GOOD/USDT"

    async def test_multiple_active_markets(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {"amount": {"min": 0.00001}},
                "precision": {"amount": 0.00001, "price": 0.01},
            },
            {
                "symbol": "ETH/USDT",
                "base": "ETH",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {"amount": {"min": 0.0001}},
                "precision": {"amount": 0.0001, "price": 0.01},
            },
            {
                "symbol": "BTC/USDT:USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "swap",
                "active": True,
                "limits": {"amount": {"min": 0.001}},
                "precision": {"amount": 0.001, "price": 0.1},
            },
        ))
        result = await exchange.list_markets()
        assert len(result) == 3
        types = {inst.market_type for inst in result}
        assert types == {"spot", "perp"}

    async def test_min_notional_extracted_from_cost_min(self):
        exchange = _make_ccxt_exchange_with_markets(_fake_ccxt_markets(
            {
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {
                    "amount": {"min": 0.00001},
                    "cost": {"min": 5.0},
                },
                "precision": {"amount": 0.00001, "price": 0.01},
            },
            {
                "symbol": "ETH/USDT",
                "base": "ETH",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "limits": {"amount": {"min": 0.0001}},  # no cost.min
                "precision": {"amount": 0.0001, "price": 0.01},
            },
        ))
        result = await exchange.list_markets()
        result_by_symbol = {inst.venue_symbol: inst for inst in result}
        assert result_by_symbol["BTC/USDT"].min_notional_usd == 5.0
        assert result_by_symbol["ETH/USDT"].min_notional_usd == 0.0
