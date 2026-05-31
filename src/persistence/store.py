from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from src.core.base_exchange import NetworkType

from .schema import AUDIT_TABLE, INSTRUMENTS_INDEXES, INSTRUMENTS_TABLE, INTENTS_TABLE, LEGS_TABLE

if TYPE_CHECKING:
    from src.market.instrument import Instrument


@dataclass
class IntentRow:
    intent_id: str
    status: str
    raw_intent_json: str
    created_at: str
    updated_at: str


@dataclass
class LegRow:
    leg_id: str
    intent_id: str
    venue: str
    instrument_venue_symbol: str
    instrument_base: str
    instrument_quote: str
    instrument_market_type: str
    quote_preference_matched: str | None = None
    planned_notional_usd: float = 0.0
    planned_qty_base: float = 0.0
    status: str = "PENDING_SEND"
    sent_at: str | None = None
    order_id: str | None = None
    filled_amount: float | None = None
    avg_price: float | None = None
    fee_usd: float | None = None
    error_msg: str | None = None
    compensation_order_id: str | None = None
    compensation_filled_amount: float | None = None
    instrument_selection_log: str | None = None
    funding_rate_at_plan: float | None = None
    next_funding_time_at_plan: float | None = None
    leverage: int = 1


@dataclass
class AuditEvent:
    id: int
    intent_id: str
    timestamp: str
    event_type: str
    payload_json: str


@dataclass
class InstrumentRow:
    venue: str
    network: str
    market_type: str
    base: str
    quote: str
    venue_symbol: str
    min_qty: float = 0.0
    qty_step: float = 0.0
    price_step: float = 0.0
    min_notional: float = 0.0
    taker_fee_rate: float = 0.0
    maker_fee_rate: float = 0.0
    contract_size: float = 1.0
    is_inverse: bool = False
    listing_status: str = "trading"
    cached_at: str = ""


class PersistenceStore:
    """
    Single-writer persistence layer backed by SQLite + JSONL.

    SQLite stores the current state (queryable). JSONL stores the full
    event log (append-only, audit, reconstructable).

    Usage:
        store = PersistenceStore(Path("data/onefill.db"), Path("logs/"))
        await store.initialize()
        await store.create_intent(intent)
        leg_id = await store.create_leg(leg_data, intent_id)
        await store.append_event(intent_id, "leg_sent", {"leg_id": leg_id, ...})
    """

    def __init__(self, sqlite_path: Path, jsonl_dir: Path):
        self._sqlite_path = sqlite_path
        self._jsonl_dir = jsonl_dir
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create/verify directories, open connection, execute DDL, enable WAL."""
        if self._sqlite_path != Path(":memory:"):
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._jsonl_dir.mkdir(parents=True, exist_ok=True)

        # Clean up stale WAL/SHM files from a previous crashed session
        # before connecting — otherwise connect() reopens them and blocks.
        self._cleanup_stale_wal()

        self._db = await aiosqlite.connect(str(self._sqlite_path))
        self._db.row_factory = aiosqlite.Row

        # Migration must happen before WAL mode — DDL in DELETE journal mode
        # is simpler and avoids the EXCLUSIVE-lock issues WAL has with ALTER TABLE.
        await self._migrate_instruments_table()
        await self._migrate_legs_table()

        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys = ON;")

        await self._db.execute(INTENTS_TABLE)
        await self._db.execute(LEGS_TABLE)
        await self._db.execute(AUDIT_TABLE)
        await self._db.execute(INSTRUMENTS_TABLE)
        for idx_sql in INSTRUMENTS_INDEXES:
            await self._db.execute(idx_sql)
        await self._db.commit()

    def _cleanup_stale_wal(self) -> None:
        """Remove leftover -wal and -shm files from a previous crashed session."""
        if self._sqlite_path == Path(":memory:"):
            return
        for suffix in ("-wal", "-shm"):
            p = Path(str(self._sqlite_path) + suffix)
            if p.exists():
                p.unlink()

    async def _migrate_instruments_table(self) -> None:
        """Add network column if missing. Drops and recreates via the new DDL."""
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='instruments'"
        )
        exists = await cursor.fetchone()
        await cursor.close()
        if not exists:
            return  # fresh database, INSTRUMENTS_TABLE will create with correct schema

        cursor = await self._db.execute("PRAGMA table_info(instruments)")
        columns = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        if "network" in columns:
            return  # already migrated

        # In DELETE journal mode (WAL not yet enabled), DROP TABLE is reliable.
        # The subsequent INSTRUMENTS_TABLE CREATE TABLE IF NOT EXISTS will
        # recreate it with the new schema including the network column.
        await self._db.execute("DROP TABLE instruments")

    async def _migrate_legs_table(self) -> None:
        """Add leverage column if missing."""
        cursor = await self._db.execute("PRAGMA table_info(legs)")
        columns = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        if not columns:
            return  # fresh database, LEGS_TABLE will create with correct schema
        if "leverage" in columns:
            return

        await self._db.execute(
            "ALTER TABLE legs ADD COLUMN leverage INTEGER NOT NULL DEFAULT 1"
        )

    # ── Intent CRUD ──────────────────────────────────────────

    async def create_intent(self, intent, status: str = "PENDING") -> None:
        """Insert a new row into intents table."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        now = datetime.now(timezone.utc).isoformat()
        if isinstance(intent, str):
            raw_json = intent
        elif is_dataclass(intent):
            raw_json = json.dumps(asdict(intent))
        else:
            raw_json = json.dumps(intent)

        try:
            await self._db.execute(
                "INSERT INTO intents (intent_id, status, raw_intent_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (intent.intent_id, status, raw_json, now, now),
            )
            await self._db.commit()
        except aiosqlite.IntegrityError as e:
            raise ValueError(f"Intent with intent_id '{intent.intent_id}' already exists") from e

        await self.append_event(intent.intent_id, "intent_created", {"status": status})

    async def get_intent(self, intent_id: str) -> IntentRow | None:
        """Return the intent row or None."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        cursor = await self._db.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return IntentRow(
            intent_id=row["intent_id"],
            status=row["status"],
            raw_intent_json=row["raw_intent_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def update_intent_status(self, intent_id: str, status: str) -> None:
        """
        Update the intent's status and updated_at timestamp.
        Calls append_event() internally after updating.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE intents SET status = ?, updated_at = ? WHERE intent_id = ?",
            (status, now, intent_id),
        )
        await self._db.commit()
        await self.append_event(intent_id, "intent_status_updated", {"status": status})

    async def list_intents(self, *, status: str | None = None, limit: int = 50) -> list[IntentRow]:
        """Return recent intents, newest first. Optionally filter by status."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        if status is not None:
            cursor = await self._db.execute(
                "SELECT * FROM intents WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM intents ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            IntentRow(
                intent_id=r["intent_id"],
                status=r["status"],
                raw_intent_json=r["raw_intent_json"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ── Leg CRUD ─────────────────────────────────────────────

    async def create_leg(
        self,
        *,
        leg_id: str | None = None,
        intent_id: str,
        venue: str,
        instrument_venue_symbol: str,
        instrument_base: str,
        instrument_quote: str,
        instrument_market_type: str,
        quote_preference_matched: str | None = None,
        planned_notional_usd: float = 0.0,
        planned_qty_base: float = 0.0,
        funding_rate_at_plan: float | None = None,
        next_funding_time_at_plan: float | None = None,
        leverage: int = 1,
    ) -> str:
        """
        Insert a leg row. Accepts individual fields from the Executor.
        Returns the leg_id. Generates one if not provided.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        if leg_id is None:
            leg_id = str(uuid.uuid4())

        await self._db.execute(
            """INSERT INTO legs (
                leg_id, intent_id, venue, instrument_venue_symbol,
                instrument_base, instrument_quote, instrument_market_type,
                quote_preference_matched, planned_notional_usd, planned_qty_base,
                funding_rate_at_plan, next_funding_time_at_plan,
                leverage, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                leg_id,
                intent_id,
                venue,
                instrument_venue_symbol,
                instrument_base,
                instrument_quote,
                instrument_market_type,
                quote_preference_matched,
                planned_notional_usd,
                planned_qty_base,
                funding_rate_at_plan,
                next_funding_time_at_plan,
                leverage,
                "PENDING_SEND",
            ),
        )
        await self._db.commit()
        await self.append_event(intent_id, "leg_created", {"leg_id": leg_id, "status": "PENDING_SEND"})
        return leg_id

    async def get_leg(self, leg_id: str) -> LegRow | None:
        """Return the leg row or None."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        cursor = await self._db.execute("SELECT * FROM legs WHERE leg_id = ?", (leg_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_legrow(row)

    async def get_legs_for_intent(self, intent_id: str) -> list[LegRow]:
        """Return all legs for a given intent."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        cursor = await self._db.execute("SELECT * FROM legs WHERE intent_id = ?", (intent_id,))
        rows = await cursor.fetchall()
        return [self._row_to_legrow(r) for r in rows]

    async def update_leg(self, leg_id: str, **fields) -> None:
        """
        Update any subset of leg fields. Only updates fields provided
        as keyword arguments. Updates intent updated_at too.
        Calls append_event() internally after updating.

        Raises ValueError if leg_id does not exist.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        # Verify leg exists
        existing = await self.get_leg(leg_id)
        if existing is None:
            raise ValueError(f"Leg with leg_id '{leg_id}' does not exist")

        if not fields:
            return

        set_clauses = []
        values = []
        for column, value in fields.items():
            set_clauses.append(f"{column} = ?")
            values.append(value)

        values.append(leg_id)
        await self._db.execute(
            f"UPDATE legs SET {', '.join(set_clauses)} WHERE leg_id = ?",
            tuple(values),
        )

        # Update the parent intent's updated_at
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE intents SET updated_at = ? WHERE intent_id = ?",
            (now, existing.intent_id),
        )

        await self._db.commit()
        await self.append_event(existing.intent_id, "leg_updated", {"leg_id": leg_id, "fields": dict(fields)})

    # ── Audit ────────────────────────────────────────────────

    async def append_event(self, intent_id: str, event_type: str, payload: dict) -> None:
        """
        Insert into audit_events table AND append a line to today's JSONL.
        JSONL line format: {"ts": iso_now, "intent_id": ..., "event_type": ..., "payload": ...}
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        now = datetime.now(timezone.utc)
        ts = now.isoformat()
        payload_json = json.dumps(payload)

        # Insert into SQLite audit_events
        await self._db.execute(
            "INSERT INTO audit_events (intent_id, timestamp, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (intent_id, ts, event_type, payload_json),
        )
        await self._db.commit()

        # Append to JSONL file
        jsonl_filename = f"audit-{now.strftime('%Y-%m-%d')}.jsonl"
        jsonl_path = self._jsonl_dir / jsonl_filename

        jsonl_line = json.dumps({
            "ts": ts,
            "intent_id": intent_id,
            "event_type": event_type,
            "payload": payload,
        })
        with open(jsonl_path, "a") as f:
            f.write(jsonl_line + "\n")

    # ── Blocking check ───────────────────────────────────────

    async def is_blocked_by_needs_manual(self) -> bool:
        """
        Return True if any intent is in the blocking state (ROLLED_BACK_FAILED).
        The Coordinator MUST check this before executing any new Intent.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM intents WHERE status = 'ROLLED_BACK_FAILED'"
        )
        row = await cursor.fetchone()
        return row["cnt"] > 0

    # ── Instruments Cache ────────────────────────────────────

    async def save_instruments(self, instruments: list[Instrument]) -> int:
        """Upsert instruments into the cache. Returns count saved."""
        if self._db is None:
            raise RuntimeError("store not initialized")
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                inst.venue,
                inst.network.value,
                inst.market_type,
                inst.base.symbol,
                inst.quote.symbol,
                inst.venue_symbol,
                inst.min_qty,
                inst.qty_step,
                inst.price_step,
                inst.min_notional,
                inst.taker_fee_rate,
                inst.maker_fee_rate,
                inst.contract_size,
                int(inst.is_inverse),
                inst.listing_status,
                now,
            )
            for inst in instruments
        ]
        await self._db.executemany(
            """INSERT OR REPLACE INTO instruments
               (venue, network, market_type, base, quote, venue_symbol,
                min_qty, qty_step, price_step, min_notional,
                taker_fee_rate, maker_fee_rate, contract_size,
                is_inverse, listing_status, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await self._db.commit()
        return len(rows)

    async def load_instruments(self) -> list[Instrument]:
        """Load all cached instruments, rebuilt into Instrument objects."""
        if self._db is None:
            raise RuntimeError("store not initialized")
        from src.market.asset import Asset
        from src.market.instrument import Instrument

        cursor = await self._db.execute("SELECT * FROM instruments ORDER BY venue, market_type, base, quote")
        results = []
        async for row in cursor:
            results.append(
                Instrument(
                    venue=row["venue"],
                    network=NetworkType(row["network"]),
                    market_type=row["market_type"],
                    base=Asset(row["base"]),
                    quote=Asset(row["quote"]),
                    venue_symbol=row["venue_symbol"],
                    min_qty=row["min_qty"],
                    qty_step=row["qty_step"],
                    price_step=row["price_step"],
                    min_notional=row["min_notional"],
                    taker_fee_rate=row["taker_fee_rate"],
                    maker_fee_rate=row["maker_fee_rate"],
                    contract_size=row["contract_size"],
                    is_inverse=bool(row["is_inverse"]),
                    listing_status=row["listing_status"],
                )
            )
        return results

    async def load_instruments_by_query(
        self,
        *,
        base: str | None = None,
        venue: str | None = None,
        market_type: str | None = None,
    ) -> list[InstrumentRow]:
        """Query instruments with optional filters."""
        if self._db is None:
            raise RuntimeError("store not initialized")
        clauses = []
        params: list[str] = []
        if base is not None:
            clauses.append("base = ?")
            params.append(base)
        if venue is not None:
            clauses.append("venue = ?")
            params.append(venue)
        if market_type is not None:
            clauses.append("market_type = ?")
            params.append(market_type)

        sql = "SELECT * FROM instruments"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY venue, market_type, base, quote"

        cursor = await self._db.execute(sql, params)
        results = []
        async for row in cursor:
            results.append(
                InstrumentRow(
                    venue=row["venue"],
                    network=row["network"],
                    market_type=row["market_type"],
                    base=row["base"],
                    quote=row["quote"],
                    venue_symbol=row["venue_symbol"],
                    min_qty=row["min_qty"],
                    qty_step=row["qty_step"],
                    price_step=row["price_step"],
                    min_notional=row["min_notional"],
                    taker_fee_rate=row["taker_fee_rate"],
                    maker_fee_rate=row["maker_fee_rate"],
                    contract_size=row["contract_size"],
                    is_inverse=bool(row["is_inverse"]),
                    listing_status=row["listing_status"],
                    cached_at=row["cached_at"],
                )
            )
        return results

    async def clear_instruments(self, venue: str | None = None) -> int:
        """Clear cached instruments, optionally scoped to one venue."""
        if self._db is None:
            raise RuntimeError("store not initialized")
        if venue is not None:
            cursor = await self._db.execute("DELETE FROM instruments WHERE venue = ?", (venue,))
        else:
            cursor = await self._db.execute("DELETE FROM instruments")
        await self._db.commit()
        return cursor.rowcount

    async def instrument_cache_age(self) -> str | None:
        """Return ISO 8601 timestamp of the most recent cache write, or None."""
        if self._db is None:
            return None
        cursor = await self._db.execute("SELECT MAX(cached_at) as latest FROM instruments")
        row = await cursor.fetchone()
        return row["latest"] if row else None

    # ── Cleanup ──────────────────────────────────────────────

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _row_to_legrow(row: aiosqlite.Row) -> LegRow:
        return LegRow(
            leg_id=row["leg_id"],
            intent_id=row["intent_id"],
            venue=row["venue"],
            instrument_venue_symbol=row["instrument_venue_symbol"],
            instrument_base=row["instrument_base"],
            instrument_quote=row["instrument_quote"],
            instrument_market_type=row["instrument_market_type"],
            quote_preference_matched=row["quote_preference_matched"],
            planned_notional_usd=row["planned_notional_usd"],
            planned_qty_base=row["planned_qty_base"],
            status=row["status"],
            sent_at=row["sent_at"],
            order_id=row["order_id"],
            filled_amount=row["filled_amount"],
            avg_price=row["avg_price"],
            fee_usd=row["fee_usd"],
            error_msg=row["error_msg"],
            compensation_order_id=row["compensation_order_id"],
            compensation_filled_amount=row["compensation_filled_amount"],
            instrument_selection_log=row["instrument_selection_log"],
            funding_rate_at_plan=row["funding_rate_at_plan"],
            next_funding_time_at_plan=row["next_funding_time_at_plan"],
            leverage=row["leverage"],
        )
