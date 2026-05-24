"""Tests for intent state transitions via PersistenceStore."""

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
async def store(tmp_path):
    s = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_full_happy_path_transitions(store):
    """
    PENDING -> VALIDATED -> EXECUTING -> ALL_FILLED
    All via store.update_intent_status. No transition validation expected.
    """
    intent = FakeIntent(intent_id="intent-001")
    await store.create_intent(intent)

    # Verify initial state
    row = await store.get_intent("intent-001")
    assert row.status == "PENDING"

    # PENDING -> VALIDATED
    await store.update_intent_status("intent-001", "VALIDATED")
    row = await store.get_intent("intent-001")
    assert row.status == "VALIDATED"

    # VALIDATED -> EXECUTING
    await store.update_intent_status("intent-001", "EXECUTING")
    row = await store.get_intent("intent-001")
    assert row.status == "EXECUTING"

    # EXECUTING -> ALL_FILLED
    await store.update_intent_status("intent-001", "ALL_FILLED")
    row = await store.get_intent("intent-001")
    assert row.status == "ALL_FILLED"


@pytest.mark.asyncio
async def test_rejection_path(store):
    """PENDING -> REJECTED (Validator rejects)"""
    intent = FakeIntent(intent_id="intent-002")
    await store.create_intent(intent)
    await store.update_intent_status("intent-002", "REJECTED")

    row = await store.get_intent("intent-002")
    assert row.status == "REJECTED"


@pytest.mark.asyncio
async def test_partial_fill_rollback_path(store):
    """
    EXECUTING -> PARTIAL_FILLED -> ROLLING_BACK -> ROLLED_BACK
    """
    intent = FakeIntent(intent_id="intent-003")
    await store.create_intent(intent)
    await store.update_intent_status("intent-003", "VALIDATED")
    await store.update_intent_status("intent-003", "EXECUTING")
    await store.update_intent_status("intent-003", "PARTIAL_FILLED")
    await store.update_intent_status("intent-003", "ROLLING_BACK")
    await store.update_intent_status("intent-003", "ROLLED_BACK")

    row = await store.get_intent("intent-003")
    assert row.status == "ROLLED_BACK"


@pytest.mark.asyncio
async def test_needs_manual_path(store):
    """
    EXECUTING -> PARTIAL_FILLED -> ROLLING_BACK -> ROLLED_BACK_FAILED
    """
    intent = FakeIntent(intent_id="intent-004")
    await store.create_intent(intent)
    await store.update_intent_status("intent-004", "EXECUTING")
    await store.update_intent_status("intent-004", "PARTIAL_FILLED")
    await store.update_intent_status("intent-004", "ROLLING_BACK")
    await store.update_intent_status("intent-004", "ROLLED_BACK_FAILED")

    row = await store.get_intent("intent-004")
    assert row.status == "ROLLED_BACK_FAILED"


@pytest.mark.asyncio
async def test_store_blindly_accepts_any_transition(store):
    """
    The store does NOT validate transitions. Writing an invalid transition
    (e.g. PENDING -> ALL_FILLED) should succeed at the store level.
    Coordinator owns validation.
    """
    intent = FakeIntent(intent_id="intent-005")
    await store.create_intent(intent)
    await store.update_intent_status("intent-005", "ALL_FILLED")  # skip intermediate states

    row = await store.get_intent("intent-005")
    assert row.status == "ALL_FILLED"


@pytest.mark.asyncio
async def test_updated_at_changes_on_transition(store):
    """updated_at should change after each state transition."""
    intent = FakeIntent(intent_id="intent-006")
    await store.create_intent(intent)

    row1 = await store.get_intent("intent-006")
    ts1 = row1.updated_at

    await store.update_intent_status("intent-006", "VALIDATED")
    row2 = await store.get_intent("intent-006")
    ts2 = row2.updated_at

    assert ts2 != ts1
