"""Tests for is_blocked_by_needs_manual."""

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
async def test_not_blocked_when_empty(store):
    """Empty database should not be blocked."""
    blocked = await store.is_blocked_by_needs_manual()
    assert blocked is False


@pytest.mark.asyncio
async def test_not_blocked_with_normal_intents(store):
    """Multiple intents in normal states (PENDING, EXECUTING, ALL_FILLED)
    should not cause blocking."""
    for i, status in enumerate(["PENDING", "VALIDATED", "EXECUTING", "ALL_FILLED"]):
        intent = FakeIntent(intent_id=f"intent-{i:03d}")
        await store.create_intent(intent)
        if status != "PENDING":
            await store.update_intent_status(f"intent-{i:03d}", status)

    blocked = await store.is_blocked_by_needs_manual()
    assert blocked is False


@pytest.mark.asyncio
async def test_blocked_when_needs_manual_exists(store):
    """is_blocked_by_needs_manual returns True when any intent
    is in the ROLLED_BACK_FAILED (blocking) state."""
    intent = FakeIntent(intent_id="intent-bad")
    await store.create_intent(intent)
    await store.update_intent_status("intent-bad", "EXECUTING")
    await store.update_intent_status("intent-bad", "PARTIAL_FILLED")
    await store.update_intent_status("intent-bad", "ROLLING_BACK")
    await store.update_intent_status("intent-bad", "ROLLED_BACK_FAILED")

    blocked = await store.is_blocked_by_needs_manual()
    assert blocked is True


@pytest.mark.asyncio
async def test_blocked_remains_after_other_operations(store):
    """Once blocked, subsequent operations should not clear the block."""
    # Create the blocking intent
    intent = FakeIntent(intent_id="intent-bad")
    await store.create_intent(intent)
    await store.update_intent_status("intent-bad", "ROLLED_BACK_FAILED")

    # Create a normal intent
    normal = FakeIntent(intent_id="intent-ok")
    await store.create_intent(normal)
    await store.update_intent_status("intent-ok", "ALL_FILLED")

    blocked = await store.is_blocked_by_needs_manual()
    assert blocked is True


@pytest.mark.asyncio
async def test_multiple_needs_manual(store):
    """Multiple ROLLED_BACK_FAILED intents should still report blocked."""
    for i in range(3):
        intent = FakeIntent(intent_id=f"bad-{i}")
        await store.create_intent(intent)
        await store.update_intent_status(f"bad-{i}", "ROLLED_BACK_FAILED")

    blocked = await store.is_blocked_by_needs_manual()
    assert blocked is True
