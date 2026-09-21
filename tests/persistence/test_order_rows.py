"""Order ledger migration and database execution exclusion."""

from pathlib import Path

import pytest

from src.persistence.store import PersistenceStore
from tests.coordinator.conftest import make_intent


@pytest.mark.asyncio
async def test_existing_leg_survives_order_ledger_migration(tmp_path):
    store = PersistenceStore(tmp_path / "orders.db", tmp_path / "audit")
    await store.initialize()
    intent = make_intent()
    await store.create_intent(intent)
    leg_id = await store.create_leg(
        intent_id=intent.intent_id,
        venue="binance",
        instrument_venue_symbol="BTCUSDT",
        instrument_base="BTC",
        instrument_quote="USDT",
        instrument_market_type="spot",
    )
    await store._db.execute("ALTER TABLE legs DROP COLUMN execution_context_json")
    await store._db.execute("DROP TABLE orders")
    await store._db.commit()
    await store.close()
    await store.initialize()
    try:
        assert (await store.get_leg(leg_id)).execution_context_json is None
        assert await store.get_orders_for_leg(leg_id) == []
        assert await store.create_order_row("client-1", leg_id, intent.intent_id, "original", "{}")
        assert not await store.create_order_row("client-1", leg_id, intent.intent_id, "original", "{}")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_two_store_connections_cannot_execute_or_recover_together(tmp_path):
    first = PersistenceStore(tmp_path / "orders.db", tmp_path / "audit")
    second = PersistenceStore(tmp_path / "orders.db", tmp_path / "audit")
    await first.initialize()
    await second.initialize()
    try:
        async with first.execution_lock():
            with pytest.raises(BlockingIOError):
                async with second.execution_lock():
                    pytest.fail("second writer acquired execution lock")
        async with second.execution_lock():
            assert True
    finally:
        await first.close()
        await second.close()
