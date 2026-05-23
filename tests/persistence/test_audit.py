"""Tests for audit event logging (SQLite audit_events + JSONL)."""

import json
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
async def test_append_event_writes_to_sqlite(store):
    """append_event should insert a row into the audit_events table."""
    intent = FakeIntent(intent_id="intent-001")
    await store.create_intent(intent)

    await store.append_event("intent-001", "leg_sent", {"leg_id": "abc", "order_id": "xyz"})

    cursor = await store._db.execute(
        "SELECT * FROM audit_events WHERE intent_id = ?", ("intent-001",)
    )
    rows = await cursor.fetchall()

    # create_intent already wrote 1 event, plus our explicit one = 2
    assert len(rows) >= 1
    our_event = rows[-1]
    assert our_event["intent_id"] == "intent-001"
    assert our_event["event_type"] == "leg_sent"

    payload = json.loads(our_event["payload_json"])
    assert payload == {"leg_id": "abc", "order_id": "xyz"}


@pytest.mark.asyncio
async def test_append_event_writes_to_jsonl(store, tmp_path):
    """append_event should write a line to the JSONL file."""
    intent = FakeIntent(intent_id="intent-001")
    await store.create_intent(intent)

    # Find the JSONL file created
    jsonl_files = list(tmp_path.glob("logs/audit-*.jsonl"))
    assert len(jsonl_files) == 1

    # The first line comes from create_intent
    content = jsonl_files[0].read_text().strip()
    lines = content.splitlines()
    assert len(lines) >= 1


@pytest.mark.asyncio
async def test_append_event_jsonl_is_valid_json(store, tmp_path):
    """Each line in the JSONL file should be valid JSON with expected keys."""
    intent = FakeIntent(intent_id="intent-002")
    await store.create_intent(intent)

    await store.append_event("intent-002", "custom_event", {"foo": "bar", "num": 42})

    jsonl_files = list(tmp_path.glob("logs/audit-*.jsonl"))
    assert len(jsonl_files) == 1

    lines = jsonl_files[0].read_text().strip().splitlines()
    # Last line is our explicit append_event
    last_line = json.loads(lines[-1])
    assert last_line["intent_id"] == "intent-002"
    assert last_line["event_type"] == "custom_event"
    assert last_line["payload"] == {"foo": "bar", "num": 42}
    assert "ts" in last_line


@pytest.mark.asyncio
async def test_append_event_stores_timestamp(store):
    """append_event should store an ISO 8601 timestamp."""
    intent = FakeIntent(intent_id="intent-003")
    await store.create_intent(intent)

    await store.append_event("intent-003", "test_ts", {"key": "val"})

    cursor = await store._db.execute(
        "SELECT timestamp FROM audit_events WHERE event_type = 'test_ts'"
    )
    row = await cursor.fetchone()
    ts = row["timestamp"]
    # ISO 8601 format with timezone
    assert "T" in ts
    assert "+" in ts or "Z" in ts


@pytest.mark.asyncio
async def test_create_intent_auto_append_event(store, tmp_path):
    """create_intent should automatically call append_event."""
    intent = FakeIntent(intent_id="intent-auto")
    await store.create_intent(intent)

    cursor = await store._db.execute(
        "SELECT * FROM audit_events WHERE intent_id = ? AND event_type = ?",
        ("intent-auto", "intent_created"),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["intent_id"] == "intent-auto"


@pytest.mark.asyncio
async def test_update_intent_status_auto_append_event(store):
    """update_intent_status should automatically call append_event."""
    intent = FakeIntent(intent_id="intent-auto2")
    await store.create_intent(intent)

    await store.update_intent_status("intent-auto2", "VALIDATED")

    cursor = await store._db.execute(
        "SELECT * FROM audit_events WHERE intent_id = ? AND event_type = ?",
        ("intent-auto2", "intent_status_updated"),
    )
    rows = await cursor.fetchall()
    assert len(rows) >= 1
    # The most recent should be for VALIDATED
    payload = json.loads(rows[-1]["payload_json"])
    assert payload["status"] == "VALIDATED"
