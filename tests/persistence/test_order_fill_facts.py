"""Exact execution facts, idempotent accounting and cache-only migration."""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from src.persistence.store import InstrumentRow, OrderFillRow, PersistenceStore


@dataclass
class _Intent:
    intent_id: str = "native-intent"


@pytest.fixture
async def store(tmp_path):
    value = PersistenceStore(tmp_path / "native.db", tmp_path / "audit")
    await value.initialize()
    await value.create_intent(_Intent())
    await value.create_leg(
        leg_id="native-leg",
        intent_id="native-intent",
        venue="binance",
        instrument_venue_symbol="BTC/USD:BTC",
        instrument_base="BTC",
        instrument_quote="USD",
        instrument_market_type="perp",
        planned_qty_native="2",
        quantity_unit="contracts",
    )
    await value.create_order_row("native-order", "native-leg", "native-intent", "original", "{}")
    yield value
    await value.close()


@pytest.fixture
def fill():
    return OrderFillRow(
        "testnet",
        "coinm",
        "binance",
        "BTC/USD:BTC",
        "trade-1",
        "native-order",
        "native-leg",
        "native-intent",
        "2",
        "20000",
        "0.01",
        "200",
        datetime.now(timezone.utc).isoformat(),
        settlement_asset="BTC",
        fee_usd="0.1",
        realized_pnl_settlement="0",
        realized_pnl_usd="0",
        valuation_price="20000",
    )


async def test_fill_idempotency_exact_quantity_and_cost_enrichment(store, fill):
    incomplete = replace(fill, fee_usd=None, realized_pnl_usd=None)
    assert await store.upsert_order_fill(incomplete)
    await store.update_leg("native-leg", filled_qty_native="2")
    assert await store.has_incomplete_order_accounting()
    assert await store.get_daily_pnl() is None
    assert not await store.upsert_order_fill(fill)
    assert not await store.upsert_order_fill(fill)
    rows = await store.get_order_fills(leg_id="native-leg")
    assert len(rows) == 1 and rows[0].qty_native == "2"
    assert not await store.has_incomplete_order_accounting()
    assert await store.get_daily_pnl() == pytest.approx(-0.1)
    with pytest.raises(ValueError, match="conflicting"):
        await store.upsert_order_fill(replace(fill, qty_native="3"))


async def test_same_trade_id_is_separate_by_network_product_and_symbol(store, fill):
    for variant in (
        fill,
        replace(fill, network="mainnet"),
        replace(fill, product_family="usdm"),
        replace(fill, symbol="ETH/USD:ETH"),
    ):
        assert await store.upsert_order_fill(variant)
    assert len(await store.get_order_fills(client_order_id="native-order")) == 4


async def test_concurrent_observations_insert_one_fill(store, fill):
    results = await asyncio.gather(*(store.upsert_order_fill(fill) for _ in range(4)))
    assert results.count(True) == 1
    assert len(await store.get_order_fills()) == 1


async def test_native_fills_exclude_legacy_price_difference_accounting(store, fill):
    await store.upsert_order_fill(replace(fill, realized_pnl_settlement="0.002", realized_pnl_usd="50"))
    await store.update_leg(
        "native-leg",
        status="COMPENSATED",
        filled_amount=0.01,
        avg_price=20000,
        compensation_filled_amount=0.008,
        compensation_avg_price=25000,
        fee_usd=999,
    )
    assert await store.get_daily_pnl() == pytest.approx(49.9)


async def test_native_quantity_larger_than_float_integer_precision_is_preserved(store, fill):
    precise = "9007199254740993"
    await store.upsert_order_fill(replace(fill, qty_native=precise))
    await store.update_leg("native-leg", filled_qty_native=precise, reason="confirmed")
    assert (await store.get_order_fills())[0].qty_native == precise
    leg = await store.get_leg("native-leg")
    assert leg.filled_qty_native == precise and leg.reason == "confirmed"


async def test_old_instrument_cache_is_rebuilt_without_business_history_loss(store, fill):
    await store.upsert_order_fill(fill)
    await store._db.execute("DROP TABLE instruments")
    await store._db.execute(
        "CREATE TABLE instruments (venue TEXT, network TEXT, market_type TEXT, base TEXT, quote TEXT, "
        "PRIMARY KEY(venue, network, market_type, base, quote))"
    )
    await store._db.execute("INSERT INTO instruments VALUES ('binance','testnet','perp','BTC','USD')")
    await store._db.execute("ALTER TABLE legs DROP COLUMN planned_qty_native")
    await store._db.commit()
    await store.close()
    await store.initialize()
    assert await store.load_instruments_by_query() == []
    assert (await store.get_leg("native-leg")).planned_qty_native is None
    assert (await store.get_intent("native-intent")).status == "PENDING"
    assert len(await store.get_order_fills()) == 1
    await store.save_instrument_rows(
        [
            InstrumentRow(
                "binance",
                "testnet",
                "perp",
                "BTC",
                "USD",
                "BTC/USD:BTC",
                contract_size=100,
                is_inverse=True,
                settlement_asset="BTC",
                quantity_unit="contracts",
            ),
            InstrumentRow(
                "binance",
                "mainnet",
                "perp",
                "BTC",
                "USD",
                "BTC/USD:BTC",
                contract_size=100,
                is_inverse=True,
                settlement_asset="BTC",
                quantity_unit="contracts",
            ),
        ]
    )
    rows = await store.load_instruments_by_query(network="testnet", contract_type="inverse", settlement_asset="BTC")
    assert len(rows) == 1 and rows[0].contract_size == 100
