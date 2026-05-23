"""Tests for intent CRUD operations."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.persistence.store import PersistenceStore


@dataclass
class FakeIntent:
    intent_id: str
    base: str = "BTC"
    quote_preference: str = "USDT"
    product: str = "spot"
    side: str = "buy"
    total_notional_usd: float = 1000.0
    split: dict = None

    def __post_init__(self):
        if self.split is None:
            self.split = {"binance": 0.5, "hyperliquid": 0.5}


@pytest.fixture
def fake_intent():
    return FakeIntent(intent_id="intent-001")


@pytest.fixture
async def store(tmp_path):
    s = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_and_get_intent(store, fake_intent):
    """create_intent + get_intent round trip."""
    await store.create_intent(fake_intent)

    row = await store.get_intent("intent-001")
    assert row is not None
    assert row.intent_id == "intent-001"
    assert row.status == "PENDING"
    assert row.raw_intent_json is not None


@pytest.mark.asyncio
async def test_create_duplicate_intent_raises(store, fake_intent):
    """create_intent on duplicate intent_id must raise ValueError."""
    await store.create_intent(fake_intent)
    with pytest.raises(ValueError, match="already exists"):
        await store.create_intent(fake_intent)


@pytest.mark.asyncio
async def test_get_nonexistent_intent_returns_none(store):
    """get_intent for unknown id returns None."""
    row = await store.get_intent("nonexistent")
    assert row is None


@pytest.mark.asyncio
async def test_list_intents_all(store):
    """list_intents without status filter returns all intents."""
    for i in range(3):
        intent = FakeIntent(intent_id=f"intent-{i:03d}")
        await store.create_intent(intent)

    results = await store.list_intents()
    assert len(results) == 3


@pytest.mark.asyncio
async def test_list_intents_filter_by_status(store):
    """list_intents with status filter returns only matching intents."""
    await store.create_intent(FakeIntent(intent_id="intent-001"))
    await store.create_intent(FakeIntent(intent_id="intent-002"))
    await store.update_intent_status("intent-002", "VALIDATED")

    all_pending = await store.list_intents(status="PENDING")
    assert len(all_pending) >= 1
    assert all(r.status == "PENDING" for r in all_pending)

    validated = await store.list_intents(status="VALIDATED")
    assert len(validated) == 1
    assert validated[0].intent_id == "intent-002"


@pytest.mark.asyncio
async def test_list_intents_respects_limit(store):
    """list_intents should respect the limit parameter."""
    for i in range(10):
        intent = FakeIntent(intent_id=f"intent-{i:03d}")
        await store.create_intent(intent)

    results = await store.list_intents(limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_list_intents_newest_first(store):
    """list_intents should return newest intents first."""
    await store.create_intent(FakeIntent(intent_id="intent-old"))
    await store.create_intent(FakeIntent(intent_id="intent-new"))

    results = await store.list_intents(limit=2)
    # Most recently created should be first
    assert results[0].intent_id == "intent-new"
    assert results[1].intent_id == "intent-old"
