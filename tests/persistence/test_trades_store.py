"""Tests for the trades table on PersistenceStore."""

from pathlib import Path

import pytest

from src.persistence.store import PersistenceStore


@pytest.fixture
async def store(tmp_path):
    s = PersistenceStore(Path(":memory:"), tmp_path / "jsonl")
    await s.initialize()
    yield s
    await s.close()


async def test_record_list_get_delete(store):
    await store.record_trade(
        trade_id="t1", symbol="BTC", side="buy", qty=0.01, price=60000, notional_usd=600,
        ts="2026-08-26T00:00:00+00:00", tag="龙头",
    )
    await store.record_trade(
        trade_id="t2", symbol="ETH", side="sell", qty=1, price=3000, notional_usd=3000,
        ts="2026-08-26T01:00:00+00:00", tag="公链",
    )

    rows = await store.list_trades()
    assert [r["id"] for r in rows] == ["t2", "t1"]  # newest first

    got = await store.get_trade("t1")
    assert got["symbol"] == "BTC" and got["tag"] == "龙头"

    tag_rows = await store.list_trades(tag="公链")
    assert [r["id"] for r in tag_rows] == ["t2"]

    assert await store.delete_trade("t1") == 1
    assert await store.get_trade("t1") is None


async def test_subscribers_crud(store):
    await store.add_subscriber("GRP1")
    await store.add_subscriber("MASTER")
    await store.add_subscriber("GRP1")  # idempotent
    assert await store.list_subscribers() == ["GRP1", "MASTER"]
    assert await store.remove_subscriber("GRP1") == 1
    assert await store.list_subscribers() == ["MASTER"]
