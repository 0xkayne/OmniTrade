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


@pytest.mark.asyncio
async def test_two_live_connections_do_not_corrupt_each_other(tmp_path):
    """A second connection (like a backtest) initializing on the SAME file must not corrupt
    the first (the watch daemon). Regression: initialize() used to delete the live -wal/-shm,
    which corrupted a running session — SQLite recovers crashed sessions on its own, so no
    manual WAL cleanup is safe when another process is connected."""
    db = tmp_path / "shared.db"
    s1 = PersistenceStore(db, tmp_path / "logs1")
    await s1.initialize()
    # Write several rows on s1: leaves uncheckpointed data in its live -wal.
    for i in range(10):
        await s1.upsert_watch_candle("SOL", "hyperliquid", f"2026-08-01T00:0{i}:00+00:00", 1.0, 1.0, 1.0, 1.0)
    # A second store initializes on the same file while s1 is still open/writing.
    s2 = PersistenceStore(db, tmp_path / "logs2")
    await s2.initialize()
    await s2.upsert_watch_candle("ETH", "hyperliquid", "2026-08-01T00:00:00+00:00", 1.0, 1.0, 1.0, 1.0)
    # Both connections must still read their data (no "database disk image is malformed").
    assert len(await s1.get_watch_candles("SOL", "hyperliquid", "1970-01-01")) == 10
    assert len(await s2.get_watch_candles("ETH", "hyperliquid", "1970-01-01")) == 1
    await s1.close()
    await s2.close()
