"""Tests for leg CRUD operations."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.persistence.store import LegRow, PersistenceStore


def _leg_kwargs(leg, intent_id: str) -> dict:
    """Convert a FakePlannedLeg to kwargs for PersistenceStore.create_leg()."""
    return {
        "intent_id": intent_id,
        "venue": leg.venue,
        "instrument_venue_symbol": leg.instrument_venue_symbol,
        "instrument_base": leg.instrument_base,
        "instrument_quote": leg.instrument_quote,
        "instrument_market_type": leg.instrument_market_type,
        "quote_preference_matched": leg.quote_preference_matched,
        "planned_notional_usd": leg.planned_notional_usd,
        "planned_qty_base": leg.planned_qty_base,
    }


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


@pytest.fixture
async def store(tmp_path):
    s = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def store_with_intent(store):
    intent = FakeIntent(intent_id="intent-001")
    await store.create_intent(intent)
    return store


@pytest.mark.asyncio
async def test_create_leg_returns_leg_id(store_with_intent):
    """create_leg should return a uuid4 leg_id."""
    leg = FakePlannedLeg(
        venue="binance",
        instrument_venue_symbol="BTCUSDT",
        instrument_base="BTC",
        instrument_quote="USDT",
    )
    leg_id = await store_with_intent.create_leg(**_leg_kwargs(leg, "intent-001"))
    assert leg_id is not None
    assert len(leg_id) == 36  # uuid4 hyphenated format


@pytest.mark.asyncio
async def test_create_and_get_leg_round_trip(store_with_intent):
    """create_leg + get_leg round trip with all fields."""
    leg = FakePlannedLeg(
        venue="hyperliquid",
        instrument_venue_symbol="BTC-USD",
        instrument_base="BTC",
        instrument_quote="USD",
        instrument_market_type="perp",
        quote_preference_matched="USDC",
        planned_notional_usd=750.0,
        planned_qty_base=0.015,
    )
    leg_id = await store_with_intent.create_leg(**_leg_kwargs(leg, "intent-001"))

    row = await store_with_intent.get_leg(leg_id)
    assert row is not None
    assert row.leg_id == leg_id
    assert row.intent_id == "intent-001"
    assert row.venue == "hyperliquid"
    assert row.instrument_venue_symbol == "BTC-USD"
    assert row.instrument_base == "BTC"
    assert row.instrument_quote == "USD"
    assert row.instrument_market_type == "perp"
    assert row.quote_preference_matched == "USDC"
    assert row.planned_notional_usd == 750.0
    assert row.planned_qty_base == 0.015
    assert row.status == "PENDING_SEND"


@pytest.mark.asyncio
async def test_get_nonexistent_leg_returns_none(store_with_intent):
    """get_leg for unknown id returns None."""
    row = await store_with_intent.get_leg("nonexistent-leg-id")
    assert row is None


@pytest.mark.asyncio
async def test_get_legs_for_intent(store_with_intent):
    """get_legs_for_intent returns all legs for an intent."""
    leg1 = FakePlannedLeg(venue="binance", instrument_venue_symbol="BTCUSDT",
                          instrument_base="BTC", instrument_quote="USDT")
    leg2 = FakePlannedLeg(venue="hyperliquid", instrument_venue_symbol="BTC-USD",
                          instrument_base="BTC", instrument_quote="USD")
    await store_with_intent.create_leg(**_leg_kwargs(leg1, "intent-001"))
    await store_with_intent.create_leg(**_leg_kwargs(leg2, "intent-001"))

    legs = await store_with_intent.get_legs_for_intent("intent-001")
    assert len(legs) == 2
    assert all(isinstance(leg_row, LegRow) for leg_row in legs)
    venues = {leg_row.venue for leg_row in legs}
    assert venues == {"binance", "hyperliquid"}


@pytest.mark.asyncio
async def test_get_legs_for_intent_empty(store_with_intent):
    """get_legs_for_intent returns empty list when no legs exist."""
    legs = await store_with_intent.get_legs_for_intent("intent-001")
    assert legs == []


@pytest.mark.asyncio
async def test_update_leg_status(store_with_intent):
    """update_leg should change leg status to SENT."""
    leg = FakePlannedLeg(venue="binance", instrument_venue_symbol="BTCUSDT",
                          instrument_base="BTC", instrument_quote="USDT")
    leg_id = await store_with_intent.create_leg(**_leg_kwargs(leg, "intent-001"))

    await store_with_intent.update_leg(leg_id, status="SENT", sent_at="2024-01-01T00:00:00+00:00")

    row = await store_with_intent.get_leg(leg_id)
    assert row.status == "SENT"
    assert row.sent_at == "2024-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_update_leg_multiple_fields(store_with_intent):
    """update_leg should update multiple fields at once."""
    leg = FakePlannedLeg(venue="binance", instrument_venue_symbol="BTCUSDT",
                          instrument_base="BTC", instrument_quote="USDT")
    leg_id = await store_with_intent.create_leg(**_leg_kwargs(leg, "intent-001"))

    await store_with_intent.update_leg(
        leg_id,
        status="FILLED",
        order_id="abc123",
        filled_amount=0.01,
        avg_price=50000.0,
        fee_usd=5.0,
    )

    row = await store_with_intent.get_leg(leg_id)
    assert row.status == "FILLED"
    assert row.order_id == "abc123"
    assert row.filled_amount == 0.01
    assert row.avg_price == 50000.0
    assert row.fee_usd == 5.0


@pytest.mark.asyncio
async def test_update_leg_nonexistent_raises_valueerror(store_with_intent):
    """update_leg on non-existent leg_id must raise ValueError."""
    with pytest.raises(ValueError, match="does not exist"):
        await store_with_intent.update_leg("nonexistent-leg-id", status="SENT")


@pytest.mark.asyncio
async def test_update_leg_no_fields_is_noop(store_with_intent):
    """update_leg with empty kwargs should not fail and not change anything."""
    leg = FakePlannedLeg(venue="binance", instrument_venue_symbol="BTCUSDT",
                          instrument_base="BTC", instrument_quote="USDT")
    leg_id = await store_with_intent.create_leg(**_leg_kwargs(leg, "intent-001"))

    await store_with_intent.update_leg(leg_id)  # no kwargs

    row = await store_with_intent.get_leg(leg_id)
    assert row.status == "PENDING_SEND"  # unchanged


@pytest.mark.asyncio
async def test_update_leg_updates_intent_updated_at(store_with_intent):
    """update_leg should also update the parent intent's updated_at."""
    leg = FakePlannedLeg(venue="binance", instrument_venue_symbol="BTCUSDT",
                          instrument_base="BTC", instrument_quote="USDT")
    leg_id = await store_with_intent.create_leg(**_leg_kwargs(leg, "intent-001"))

    intent_before = await store_with_intent.get_intent("intent-001")
    original_updated_at = intent_before.updated_at

    await store_with_intent.update_leg(leg_id, status="FILLED")

    intent_after = await store_with_intent.get_intent("intent-001")
    assert intent_after.updated_at != original_updated_at


@pytest.mark.asyncio
async def test_create_leg_for_nonexistent_intent_raises(store):
    """Creating a leg for a non-existent intent should raise IntegrityError
    (foreign key enforcement)."""
    leg = FakePlannedLeg(venue="binance", instrument_venue_symbol="BTCUSDT",
                          instrument_base="BTC", instrument_quote="USDT")
    with pytest.raises(Exception):  # noqa: B017
        await store.create_leg(**_leg_kwargs(leg, "intent-nonexistent"))
