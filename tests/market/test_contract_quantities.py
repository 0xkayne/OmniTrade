"""Native contract quantities and price-dependent inverse execution values."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.market.quote import Quote
from src.market.registry import InstrumentRegistry, instrument_from_row, instrument_to_row
from src.persistence.store import PersistenceStore


@pytest.fixture
def inverse():
    return Instrument(
        "binance",
        NetworkType.TESTNET,
        "perp",
        Asset("BTC"),
        Asset("USD"),
        "BTC/USD:BTC",
        qty_step=1,
        min_qty=1,
        contract_size=100,
        is_inverse=True,
        settlement_asset=Asset("BTC"),
        quantity_unit="contracts",
        max_leverage=20,
    )


@pytest.mark.parametrize("price", [20000, 40000])
def test_inverse_notional_sizing_does_not_change_with_price(inverse, price):
    assert inverse.native_qty_from_notional(1050, price) == 10
    assert inverse.quote_notional(10, price) == 1000
    assert inverse.base_equivalent(10, price) == pytest.approx(1000 / price)
    assert inverse.native_qty_from_notional(99, price) == 0
    assert inverse.required_margin(1000, 10, price) == pytest.approx(1000 / price / 10)


def test_linear_contract_size_converts_between_native_and_base(inverse):
    linear = replace(inverse, is_inverse=False, contract_size=0.01)
    assert linear.native_qty_from_notional(1000, 20000) == 5
    assert linear.base_equivalent(5, 20000) == 0.05
    assert linear.quote_notional(5, 20000) == 1000


def test_inverse_quote_uses_harmonic_average_and_native_depth(inverse):
    quote = Quote(inverse, 0, 19000, 2, 20000, 1, 19500, 0, 0, _bids=[(19000, 2)], _asks=[(20000, 1), (40000, 1)])
    fill = quote.estimate_fill(2, "buy")
    assert fill.filled_qty_native == 2
    assert fill.filled_qty_base == pytest.approx(0.0075)
    assert fill.filled_notional_quote == 200
    assert fill.avg_price == pytest.approx(200 / 0.0075)
    assert fill.filled_fully

    bounded = quote.estimate_fill(2, "buy", limit_price=30000)
    assert bounded.filled_qty_native == 1
    assert bounded.avg_price == 20000
    assert not bounded.filled_fully
    with pytest.raises(ValueError, match="native quantity"):
        quote.estimate_fill(amount_base=0.0075, side="buy")


def test_open_and_close_base_equivalents_do_not_define_contract_residual(inverse):
    assert inverse.base_equivalent(2, 20000) == 0.01
    assert inverse.base_equivalent(2, 25000) == 0.008
    assert inverse.quote_notional(0, 25000) == 0


def test_contract_metadata_round_trips_and_native_symbols_do_not_collide(inverse):
    assert instrument_from_row(instrument_to_row(inverse)) == inverse
    variant = replace(inverse, venue_symbol="BTC/USD:OTHER", settlement_asset=Asset("OTHER"))
    registry = InstrumentRegistry()
    registry.add(inverse)
    registry.add(variant)
    assert registry.instrument_count == 2
    assert registry.find_one(base="BTC", venue="binance", market_type="perp", quote_preference=["USD"]) is None
    assert (
        registry.find_one(
            base="BTC",
            venue="binance",
            market_type="perp",
            quote_preference=["USD"],
            contract_type="inverse",
            settlement_asset="BTC",
        )
        == inverse
    )


@pytest.mark.parametrize("quantity,price", [(-1, 20000), (1, 0), (float("nan"), 20000)])
def test_invalid_contract_conversion_is_rejected(inverse, quantity, price):
    with pytest.raises(ValueError):
        inverse.base_equivalent(quantity, price)


async def test_cached_markets_respect_network_connected_families_and_venues(inverse, tmp_path):
    store = PersistenceStore(Path(":memory:"), tmp_path / "audit")
    await store.initialize()
    linear = replace(
        inverse, is_inverse=False, contract_size=1, settlement_asset=Asset("USD"), venue_symbol="BTC/USD:USD"
    )
    try:
        await store.save_instrument_rows(
            [
                instrument_to_row(inst)
                for inst in (
                    inverse,
                    linear,
                    replace(linear, network=NetworkType.MAINNET),
                    replace(linear, venue="disabled-venue"),
                )
            ]
        )
        exchange = SimpleNamespace(
            network_type=NetworkType.TESTNET,
            market_families=("usdm", "coinm"),
            clients={"usdm": object()},
            list_markets=AsyncMock(return_value=[]),
        )
        registry = InstrumentRegistry()
        await registry.load_all({"binance": exchange}, store=store)
        assert registry.list_instruments() == [linear]
        exchange.list_markets.assert_not_awaited()
    finally:
        await store.close()


async def test_newly_enabled_family_is_fetched_instead_of_using_incomplete_cache(inverse, tmp_path):
    store = PersistenceStore(Path(":memory:"), tmp_path / "audit")
    await store.initialize()
    linear = replace(inverse, is_inverse=False, contract_size=1, venue_symbol="BTC/USD:USD")
    try:
        await store.save_instrument_rows([instrument_to_row(linear)])
        exchange = SimpleNamespace(
            network_type=NetworkType.TESTNET,
            clients={"usdm": object(), "coinm": object()},
            list_markets=AsyncMock(return_value=[linear, inverse]),
        )
        registry = InstrumentRegistry()
        await registry.load_all({"binance": exchange}, store=store)
        assert registry.instrument_count == 2
        exchange.list_markets.assert_awaited_once()
    finally:
        await store.close()
