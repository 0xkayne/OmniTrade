"""Tests for concurrent access to PersistenceStore."""

import asyncio
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


@dataclass
class FakePlannedLeg:
    venue: str
    instrument_venue_symbol: str
    instrument_base: str
    instrument_quote: str
    instrument_market_type: str = "spot"
    quote_preference_matched: str | None = None
    planned_notional_usd: float = 500.0
    planned_qty_base: float = 0.01


@pytest.mark.asyncio
async def test_concurrent_intent_creation(tmp_path):
    """Create multiple intents concurrently via asyncio.gather, verify all persisted."""
    store = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await store.initialize()

    num_intents = 20

    async def create_one(i: int):
        intent = FakeIntent(intent_id=f"concurrent-{i:03d}")
        await store.create_intent(intent)

    await asyncio.gather(*[create_one(i) for i in range(num_intents)])

    results = await store.list_intents(limit=num_intents + 10)
    assert len(results) == num_intents

    await store.close()


@pytest.mark.asyncio
async def test_concurrent_leg_creation(tmp_path):
    """Create legs concurrently, verify all persisted correctly."""
    store = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await store.initialize()

    intent = FakeIntent(intent_id="intent-multi")
    await store.create_intent(intent)

    num_legs = 20

    async def create_one(i: int):
        FakePlannedLeg(
            venue=f"venue-{i}",
            instrument_venue_symbol=f"SYM-{i}",
            instrument_base="BTC",
            instrument_quote="USDT",
        )
        return await store.create_leg(
            intent_id="intent-multi",
            venue=f"venue-{i}",
            instrument_venue_symbol=f"SYM-{i}",
            instrument_base="BTC",
            instrument_quote="USDT",
            instrument_market_type="spot",
        )

    leg_ids = await asyncio.gather(*[create_one(i) for i in range(num_legs)])
    assert len(leg_ids) == num_legs
    assert len(set(leg_ids)) == num_legs  # all unique

    legs = await store.get_legs_for_intent("intent-multi")
    assert len(legs) == num_legs

    await store.close()


@pytest.mark.asyncio
async def test_concurrent_mixed_operations(tmp_path):
    """Mix intent creation, leg creation, status updates concurrently."""
    store = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await store.initialize()

    # Create several intents
    for i in range(5):
        intent = FakeIntent(intent_id=f"mixed-{i:03d}")
        await store.create_intent(intent)

    # Concurrently create legs and update statuses
    async def add_leg_and_update(i: int):
        intent_id = f"mixed-{i:03d}"
        FakePlannedLeg(
            venue="binance",
            instrument_venue_symbol="BTCUSDT",
            instrument_base="BTC",
            instrument_quote="USDT",
        )
        await store.create_leg(
            intent_id=intent_id,
            venue="binance",
            instrument_venue_symbol="BTCUSDT",
            instrument_base="BTC",
            instrument_quote="USDT",
            instrument_market_type="spot",
        )
        await store.update_intent_status(intent_id, "VALIDATED")

    await asyncio.gather(*[add_leg_and_update(i) for i in range(5)])

    # Verify all intents got updated
    for i in range(5):
        row = await store.get_intent(f"mixed-{i:03d}")
        assert row.status == "VALIDATED"
        legs = await store.get_legs_for_intent(f"mixed-{i:03d}")
        assert len(legs) == 1

    await store.close()
