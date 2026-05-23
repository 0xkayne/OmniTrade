import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .schema import AUDIT_TABLE, INTENTS_TABLE, LEGS_TABLE


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


@dataclass
class AuditEvent:
    id: int
    intent_id: str
    timestamp: str
    event_type: str
    payload_json: str


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

        self._db = await aiosqlite.connect(str(self._sqlite_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys = ON;")

        await self._db.execute(INTENTS_TABLE)
        await self._db.execute(LEGS_TABLE)
        await self._db.execute(AUDIT_TABLE)
        await self._db.commit()

    # ── Intent CRUD ──────────────────────────────────────────

    async def create_intent(self, intent: "Intent") -> None:
        """Insert a new row into intents table. Status = 'PENDING'."""
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
                (intent.intent_id, "PENDING", raw_json, now, now),
            )
            await self._db.commit()
        except aiosqlite.IntegrityError:
            raise ValueError(f"Intent with intent_id '{intent.intent_id}' already exists")

        await self.append_event(intent.intent_id, "intent_created", {"status": "PENDING"})

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

    async def create_leg(self, leg: "PlannedLeg", intent_id: str) -> str:
        """
        Insert a leg row. Generate a leg_id (uuid4).
        Returns the new leg_id.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        leg_id = str(uuid.uuid4())

        await self._db.execute(
            """INSERT INTO legs (
                leg_id, intent_id, venue, instrument_venue_symbol,
                instrument_base, instrument_quote, instrument_market_type,
                quote_preference_matched, planned_notional_usd, planned_qty_base,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                leg_id,
                intent_id,
                getattr(leg, "venue", ""),
                getattr(leg, "instrument_venue_symbol", ""),
                getattr(leg, "instrument_base", ""),
                getattr(leg, "instrument_quote", ""),
                getattr(leg, "instrument_market_type", ""),
                getattr(leg, "quote_preference_matched", None),
                getattr(leg, "planned_notional_usd", 0.0),
                getattr(leg, "planned_qty_base", 0.0),
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
        Return True if any intent is in state NEEDS_MANUAL.
        The Coordinator MUST check this before executing any new Intent.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM intents WHERE status = 'NEEDS_MANUAL'"
        )
        row = await cursor.fetchone()
        return row["cnt"] > 0

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
        )
