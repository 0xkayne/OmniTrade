from pathlib import Path

import aiosqlite
import pytest

from src.persistence.store import PersistenceStore


async def _store(tmp_path: Path) -> PersistenceStore:
    store = PersistenceStore(tmp_path / "arb.db", tmp_path / "logs")
    await store.initialize()
    return store


def _cycle_fields() -> dict:
    return {
        "cycle_id": "cycle-1",
        "opportunity_id": "opp-1",
        "base": "BTC",
        "market_type": "perp",
        "direction": "buy_arcus_sell_hyperliquid",
        "venue_buy": "arcus",
        "venue_sell": "hyperliquid",
        "symbol_buy": "BTC-USD",
        "symbol_sell": "BTC/USD:USDC",
        "target_qty_base": 0.01,
    }


@pytest.mark.asyncio
async def test_arbitrage_schema_and_cycle_crud(tmp_path):
    store = await _store(tmp_path)
    try:
        tables = {
            row["name"] async for row in await store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"arbitrage_cycles", "arbitrage_cycle_legs", "arbitrage_fills"} <= tables

        await store.create_arbitrage_cycle(**_cycle_fields())
        cycle = await store.get_arbitrage_cycle("cycle-1")
        assert cycle["status"] == "DETECTED"
        assert cycle["target_qty_base"] == pytest.approx(0.01)

        await store.update_arbitrage_cycle("cycle-1", status="OPENING", opened_qty_base=0.01)
        assert (await store.get_arbitrage_cycle("cycle-1"))["status"] == "OPENING"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_arbitrage_leg_and_fill_are_related_and_idempotent(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.create_arbitrage_cycle(**_cycle_fields())
        await store.create_arbitrage_cycle_leg(
            leg_id="leg-buy",
            cycle_id="cycle-1",
            role="BUY",
            venue="arcus",
            symbol="BTC-USD",
            side="buy",
            target_qty_base=0.01,
            client_order_id="arb-cycle-1-buy-1",
        )
        await store.create_arbitrage_cycle_leg(
            leg_id="leg-sell",
            cycle_id="cycle-1",
            role="SELL",
            venue="hyperliquid",
            symbol="BTC/USD:USDC",
            side="sell",
            target_qty_base=0.01,
        )
        assert len(await store.get_arbitrage_cycle_legs("cycle-1")) == 2

        fill = {
            "fill_id": "fill-1",
            "cycle_id": "cycle-1",
            "leg_id": "leg-buy",
            "venue": "arcus",
            "trade_id": "trade-42",
            "quantity": 0.01,
            "price": 100000.0,
        }
        assert await store.insert_arbitrage_fill(**fill)
        assert not await store.insert_arbitrage_fill(**fill)
        assert len(await store.get_arbitrage_fills("cycle-1", "leg-buy")) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_arbitrage_cycle_reopen_is_safe(tmp_path):
    db_path = tmp_path / "arb.db"
    store = PersistenceStore(db_path, tmp_path / "logs")
    await store.initialize()
    await store.create_arbitrage_cycle(**_cycle_fields())
    await store.close()

    reopened = PersistenceStore(db_path, tmp_path / "logs")
    await reopened.initialize()
    try:
        assert (await reopened.get_arbitrage_cycle("cycle-1"))["cycle_id"] == "cycle-1"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_arbitrage_unique_roles_client_ids_and_fill_cycle(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.create_arbitrage_cycle(**_cycle_fields())
        await store.create_arbitrage_cycle(**dict(_cycle_fields(), cycle_id="cycle-2"))
        leg = {
            "leg_id": "leg-buy",
            "cycle_id": "cycle-1",
            "role": "BUY",
            "venue": "arcus",
            "symbol": "BTC-USD",
            "side": "buy",
            "target_qty_base": 0.01,
            "client_order_id": "arb-cycle-1-buy-1",
        }
        await store.create_arbitrage_cycle_leg(**leg)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.create_arbitrage_cycle_leg(**dict(leg, leg_id="duplicate-role", client_order_id=None))
        with pytest.raises(aiosqlite.IntegrityError):
            await store.create_arbitrage_cycle_leg(**dict(leg, leg_id="duplicate-client-id", cycle_id="cycle-2"))
        with pytest.raises(aiosqlite.IntegrityError):
            await store.insert_arbitrage_fill(
                fill_id="invalid-cycle",
                cycle_id="cycle-2",
                leg_id="leg-buy",
                venue="arcus",
                trade_id="trade-42",
                quantity=0.01,
                price=100000.0,
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_arbitrage_recovery_query_and_unknown_update(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.create_arbitrage_cycle(**_cycle_fields())
        await store.create_arbitrage_cycle(**dict(_cycle_fields(), cycle_id="closed", status="CLOSED"))
        assert [row["cycle_id"] for row in await store.list_unfinished_arbitrage_cycles()] == ["cycle-1"]
        with pytest.raises(ValueError, match="Unsupported"):
            await store.update_arbitrage_cycle("cycle-1", accidental_column="bad")
        with pytest.raises(ValueError, match="does not exist"):
            await store.update_arbitrage_cycle("missing", status="OPENING")
        with pytest.raises(ValueError, match="does not exist"):
            await store.update_arbitrage_cycle_leg("missing", status="FILLED")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_arbitrage_schema_upgrade_preserves_existing_rows(tmp_path):
    db_path = tmp_path / "old.db"
    async with aiosqlite.connect(db_path) as old:
        await old.execute(
            "CREATE TABLE intents (intent_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "raw_intent_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        await old.execute("INSERT INTO intents VALUES ('old-intent', 'DONE', '{}', '2026-09-14', '2026-09-14')")
        await old.commit()
    store = PersistenceStore(db_path, tmp_path / "logs")
    await store.initialize()
    try:
        assert (await store.get_intent("old-intent")).status == "DONE"
        await store.create_arbitrage_cycle(**_cycle_fields())
        assert await store.get_arbitrage_cycle("cycle-1")
    finally:
        await store.close()
