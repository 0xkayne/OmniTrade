"""Tests for PersistenceStore.initialize()"""

from pathlib import Path

import pytest

from src.persistence.store import PersistenceStore


@pytest.mark.asyncio
async def test_initialize_creates_tables_in_memory(tmp_path):
    """initialize() with :memory: should create all three tables without error."""
    store = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await store.initialize()

    # Verify tables exist by querying sqlite_master
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row["name"] async for row in cursor]
    assert "intents" in tables
    assert "legs" in tables
    assert "audit_events" in tables
    await store.close()


@pytest.mark.asyncio
async def test_initialize_enables_wal(tmp_path):
    """initialize() should enable WAL journal mode."""
    db_path = tmp_path / "test.db"
    store = PersistenceStore(db_path, tmp_path / "logs")
    await store.initialize()

    cursor = await store._db.execute("PRAGMA journal_mode;")
    row = await cursor.fetchone()
    journal_mode = row[0]
    assert journal_mode.lower() == "wal"

    await store.close()


@pytest.mark.asyncio
async def test_initialize_creates_jsonl_dir(tmp_path):
    """initialize() should create the jsonl_dir if it doesn't exist."""
    jsonl_dir = tmp_path / "logs"
    store = PersistenceStore(tmp_path / "test.db", jsonl_dir)
    await store.initialize()

    assert jsonl_dir.exists()
    assert jsonl_dir.is_dir()

    await store.close()


@pytest.mark.asyncio
async def test_initialize_reopenable(tmp_path):
    """Calling initialize() twice on the same path should not crash
    (CREATE TABLE IF NOT EXISTS)."""
    db_path = tmp_path / "test.db"
    jsonl_dir = tmp_path / "logs"

    store1 = PersistenceStore(db_path, jsonl_dir)
    await store1.initialize()
    await store1.close()

    store2 = PersistenceStore(db_path, jsonl_dir)
    await store2.initialize()
    await store2.close()


@pytest.mark.asyncio
async def test_initialize_memory_db_does_not_create_parent(tmp_path):
    """:memory: path should not try to mkdir the parent."""
    store = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await store.initialize()
    # Should not raise — :memory: skips parent mkdir
    await store.close()
