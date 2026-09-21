from __future__ import annotations

import fcntl
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .schema import (
    ARBITRAGE_CYCLE_LEGS_INDEXES,
    ARBITRAGE_CYCLE_LEGS_TABLE,
    ARBITRAGE_CYCLES_INDEXES,
    ARBITRAGE_CYCLES_TABLE,
    ARBITRAGE_FILLS_INDEXES,
    ARBITRAGE_FILLS_TABLE,
    AUDIT_TABLE,
    DERIVED_CANDLES_INDEXES,
    DERIVED_CANDLES_TABLE,
    FUNDING_RATE_SNAPSHOTS_TABLE,
    HEDGED_POSITIONS_TABLE,
    INSTRUMENTS_INDEXES,
    INSTRUMENTS_TABLE,
    INTENTS_INDEXES,
    INTENTS_TABLE,
    LEGS_INDEXES,
    LEGS_TABLE,
    ORDERS_TABLE,
    TELEGRAM_SUBSCRIBERS_TABLE,
    TRADES_INDEXES,
    TRADES_TABLE,
    WATCH_CANDLES_INDEXES,
    WATCH_CANDLES_TABLE,
)


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
    compensation_avg_price: float | None = None
    compensation_fee_usd: float | None = None
    instrument_selection_log: str | None = None
    funding_rate_at_plan: float | None = None
    next_funding_time_at_plan: float | None = None
    leverage: int = 1
    filled_at: str | None = None
    compensated_at: str | None = None
    execution_context_json: str | None = None


@dataclass
class OrderRow:
    client_order_id: str
    leg_id: str
    intent_id: str
    purpose: str
    request_json: str
    status: str
    snapshot_json: str | None
    error_msg: str | None
    created_at: float
    updated_at: float


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
        self._is_executing = False

    @asynccontextmanager
    async def execution_lock(self):
        """Exclude concurrent execution/recovery on this store and database file."""
        if self._is_executing:
            raise RuntimeError("An execution or recovery is already active")
        self._is_executing = True
        handle = None
        try:
            if self._sqlite_path != Path(":memory:"):
                handle = self._sqlite_path.resolve().with_suffix(".execution.lock").open("a")
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            if handle:
                handle.close()
            self._is_executing = False

    async def initialize(self) -> None:
        """Create/verify directories, open connection, execute DDL, enable WAL.

        WAL mode supports many concurrent readers plus a single writer, so the watch
        daemon and a backtest can share this store: concurrent writers are serialized by
        SQLite. A generous ``busy_timeout`` makes them WAIT for the write lock rather than
        fail. There is deliberately NO manual -wal/-shm cleanup — deleting a *live* session's
        WAL corrupts the database (the root cause of an earlier "database disk image is
        malformed"), and SQLite recovers from a crashed session's WAL on the next open.
        """
        if self._sqlite_path != Path(":memory:"):
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._jsonl_dir.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self._sqlite_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA busy_timeout = 30000;")

        # Migration must happen before WAL mode — DDL in DELETE journal mode
        # is simpler and avoids the EXCLUSIVE-lock issues WAL has with ALTER TABLE.
        await self._migrate_instruments_table()
        await self._migrate_legs_table()
        await self._migrate_arbitrage_cycles_table()
        await self._migrate_trades_table()
        await self._migrate_watch_candles_table()

        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys = ON;")

        await self._db.execute(INTENTS_TABLE)
        await self._db.execute(LEGS_TABLE)
        await self._db.execute(AUDIT_TABLE)
        await self._db.execute(ORDERS_TABLE)
        await self._db.execute(INSTRUMENTS_TABLE)
        await self._db.execute(FUNDING_RATE_SNAPSHOTS_TABLE)
        await self._db.execute(HEDGED_POSITIONS_TABLE)
        await self._db.execute(ARBITRAGE_CYCLES_TABLE)
        await self._db.execute(ARBITRAGE_CYCLE_LEGS_TABLE)
        await self._db.execute(ARBITRAGE_FILLS_TABLE)
        await self._db.execute(WATCH_CANDLES_TABLE)
        await self._db.execute(DERIVED_CANDLES_TABLE)
        await self._db.execute(TRADES_TABLE)
        await self._db.execute(TELEGRAM_SUBSCRIBERS_TABLE)
        for idx_sql in INSTRUMENTS_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in LEGS_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in INTENTS_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in WATCH_CANDLES_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in DERIVED_CANDLES_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in TRADES_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in ARBITRAGE_CYCLES_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in ARBITRAGE_CYCLE_LEGS_INDEXES:
            await self._db.execute(idx_sql)
        for idx_sql in ARBITRAGE_FILLS_INDEXES:
            await self._db.execute(idx_sql)
        await self._db.commit()

    async def _migrate_arbitrage_cycles_table(self) -> None:
        """Preserve legacy cycles while adding nullable recovery context."""
        cursor = await self._db.execute("PRAGMA table_info(arbitrage_cycles)")
        columns = {row["name"] for row in await cursor.fetchall()}
        if columns and "execution_context_json" not in columns:
            await self._db.execute("ALTER TABLE arbitrage_cycles ADD COLUMN execution_context_json TEXT")
            await self._db.commit()

    async def _migrate_instruments_table(self) -> None:
        """Add network column if missing. Drops and recreates via the new DDL."""
        cursor = await self._db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='instruments'")
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
        """Add columns introduced after the initial legs schema."""
        cursor = await self._db.execute("PRAGMA table_info(legs)")
        columns = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        if not columns:
            return  # fresh database, LEGS_TABLE will create with correct schema

        migrations = {
            "execution_context_json": "ALTER TABLE legs ADD COLUMN execution_context_json TEXT",
            "leverage": "ALTER TABLE legs ADD COLUMN leverage INTEGER NOT NULL DEFAULT 1",
            "compensation_avg_price": "ALTER TABLE legs ADD COLUMN compensation_avg_price REAL",
            "compensation_fee_usd": "ALTER TABLE legs ADD COLUMN compensation_fee_usd REAL",
            "filled_at": "ALTER TABLE legs ADD COLUMN filled_at TEXT",
            "compensated_at": "ALTER TABLE legs ADD COLUMN compensated_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                await self._db.execute(statement)

        # Backfill existing FILLED/COMPENSATED legs with PnL timestamps.
        # Without this, pre-existing legs would have NULL filled_at/compensated_at
        # and would be invisible to get_daily_pnl().
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE legs SET filled_at = COALESCE(sent_at, ?) "
            "WHERE status IN ('FILLED', 'COMPENSATED') AND filled_at IS NULL",
            (now,),
        )
        await self._db.execute(
            "UPDATE legs SET compensated_at = ? WHERE status = 'COMPENSATED' AND compensated_at IS NULL",
            (now,),
        )
        await self._db.commit()

    async def _migrate_trades_table(self) -> None:
        """Add the matched_buy_id column added after the initial trades schema."""
        cursor = await self._db.execute("PRAGMA table_info(trades)")
        columns = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        if not columns or "matched_buy_id" in columns:
            return
        await self._db.execute("ALTER TABLE trades ADD COLUMN matched_buy_id TEXT")

    async def _migrate_watch_candles_table(self) -> None:
        """Add the ``interval`` column and widen the unique key to ``(asset, venue, interval, ts)``.

        Runs in DELETE journal mode (before WAL), where table DDL is reliable. Existing
        rows were all written in the single-timeframe era, so they get ``interval='5m'``.
        The unique-key change forces a table rebuild (SQLite can't alter a table-level
        UNIQUE constraint), so we copy the old rows into the new schema and drop the old.
        """
        cursor = await self._db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='watch_candles'")
        exists = await cursor.fetchone()
        await cursor.close()
        if not exists:
            return  # fresh database, WATCH_CANDLES_TABLE will create with the new schema

        cursor = await self._db.execute("PRAGMA table_info(watch_candles)")
        columns = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        if "interval" in columns:
            return  # already migrated

        await self._db.execute("DROP TABLE IF EXISTS watch_candles_old")
        await self._db.execute("ALTER TABLE watch_candles RENAME TO watch_candles_old")
        await self._db.execute(WATCH_CANDLES_TABLE)  # new schema: interval column + widened UNIQUE
        await self._db.execute(
            "INSERT INTO watch_candles (asset, venue, interval, ts, open, high, low, close) "
            "SELECT asset, venue, '5m', ts, open, high, low, close FROM watch_candles_old"
        )
        await self._db.execute("DROP TABLE watch_candles_old")
        await self._db.commit()

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
        as keyword arguments. Updates intent updated_at on PnL-relevant
        transitions (FILLED, COMPENSATED).
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

        # Track PnL-relevant timestamps
        now = datetime.now(timezone.utc).isoformat()
        pnl_event = False
        if fields.get("status") == "FILLED" and not existing.filled_at:
            set_clauses.append("filled_at = ?")
            values.append(now)
            fields["filled_at"] = now
            pnl_event = True
        if fields.get("status") == "COMPENSATED" and not existing.compensated_at:
            set_clauses.append("compensated_at = ?")
            values.append(now)
            fields["compensated_at"] = now
            pnl_event = True

        values.append(leg_id)
        await self._db.execute(
            f"UPDATE legs SET {', '.join(set_clauses)} WHERE leg_id = ?",
            tuple(values),
        )

        # Only bump intent updated_at for PnL-relevant transitions
        if pnl_event:
            await self._db.execute(
                "UPDATE intents SET updated_at = ? WHERE intent_id = ?",
                (now, existing.intent_id),
            )

        await self._db.commit()
        await self.append_event(existing.intent_id, "leg_updated", {"leg_id": leg_id, "fields": dict(fields)})

    # ── Audit ────────────────────────────────────────────────

    async def create_order_row(
        self, client_order_id: str, leg_id: str, intent_id: str, purpose: str, request_json: str
    ) -> bool:
        """Reserve an order identity before sending. Return False for an existing ID."""
        now = time.time()
        cursor = await self._db.execute(
            "INSERT OR IGNORE INTO orders "
            "(client_order_id, leg_id, intent_id, purpose, request_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_order_id, leg_id, intent_id, purpose, request_json, now, now),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def get_order_row(self, client_order_id: str) -> OrderRow | None:
        cursor = await self._db.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,))
        row = await cursor.fetchone()
        return OrderRow(**dict(row)) if row else None

    async def get_orders_for_leg(self, leg_id: str, purpose: str | None = None) -> list[OrderRow]:
        cursor = await self._db.execute(
            "SELECT * FROM orders WHERE leg_id = ? AND (? IS NULL OR purpose = ?) ORDER BY created_at, client_order_id",
            (leg_id, purpose, purpose),
        )
        return [OrderRow(**dict(row)) for row in await cursor.fetchall()]

    async def update_order_row(
        self, client_order_id: str, status: str, snapshot_json: str | None = None, error_msg: str | None = None
    ) -> None:
        await self._db.execute(
            "UPDATE orders SET status = ?, snapshot_json = COALESCE(?, snapshot_json), error_msg = ?, updated_at = ? "
            "WHERE client_order_id = ?",
            (status, snapshot_json, error_msg, time.time(), client_order_id),
        )
        await self._db.commit()

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

        jsonl_line = json.dumps(
            {
                "ts": ts,
                "intent_id": intent_id,
                "event_type": event_type,
                "payload": payload,
            }
        )
        with open(jsonl_path, "a") as f:
            f.write(jsonl_line + "\n")

    # ── Blocking check ───────────────────────────────────────

    async def count_intents_with_status(self, status: str) -> int:
        """Count intents currently in ``status``.

        Deliberately generic: which status blocks the system is a Coordinator
        policy, not a persistence one. See
        docs/developer-guide/standards/directory-structure.md §5.3.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        cursor = await self._db.execute("SELECT COUNT(*) as cnt FROM intents WHERE status = ?", (status,))
        row = await cursor.fetchone()
        return row["cnt"]

    # ── Risk queries ──────────────────────────────────────────

    async def get_daily_pnl(self) -> float | None:
        """Return cumulative realized PnL (USD) for today's filled legs.

        Open trade notional is not PnL: a filled buy is an asset position, not
        a realized loss. This method only counts realized components available
        in the store: execution fees and closed-out compensated legs.

        Returns None if no realized PnL components exist today.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self._db.execute(
            """SELECT l.status, l.venue, l.filled_amount, l.avg_price, l.fee_usd,
                      l.compensation_filled_amount, l.compensation_avg_price,
                      l.compensation_fee_usd, l.filled_at, l.compensated_at,
                      i.raw_intent_json
               FROM legs l
               JOIN intents i ON l.intent_id = i.intent_id
               WHERE l.status IN ('FILLED', 'COMPENSATED')
                 AND l.filled_amount > 0
                 AND (l.filled_at >= ? OR l.compensated_at >= ?)""",
            (today, today),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None

        pnl = 0.0
        has_realized_component = False
        for row in rows:
            # Only count fill fee if the fill event happened today
            filled_at = row["filled_at"]
            if filled_at and filled_at >= today:
                fee_usd = row["fee_usd"] or 0.0
                if fee_usd:
                    pnl -= fee_usd
                    has_realized_component = True

            # Only count compensation components if compensation happened today
            compensated_at = row["compensated_at"]
            if compensated_at and compensated_at >= today:
                compensation_fee_usd = row["compensation_fee_usd"] or 0.0
                if compensation_fee_usd:
                    pnl -= compensation_fee_usd
                    has_realized_component = True

                filled_amount = row["filled_amount"]
                avg_price = row["avg_price"]
                compensation_filled_amount = row["compensation_filled_amount"]
                compensation_avg_price = row["compensation_avg_price"]
                if filled_amount and avg_price and compensation_filled_amount and compensation_avg_price:
                    qty = min(filled_amount, compensation_filled_amount)
                    side = self._side_from_intent_json(row["raw_intent_json"], row["venue"])
                    if side == "sell":
                        pnl += (avg_price - compensation_avg_price) * qty
                    else:
                        pnl += (compensation_avg_price - avg_price) * qty
                    has_realized_component = True

        return pnl if has_realized_component else None

    async def get_venue_exposure(self, venue: str) -> float | None:
        """Return total notional (USD) of FILLED legs that have NOT been compensated,
        for a specific venue. Returns None if no exposure exists.
        """
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """SELECT SUM(planned_notional_usd) as total
               FROM legs
               WHERE venue = ?
                 AND status = 'FILLED'""",
            (venue,),
        )
        row = await cursor.fetchone()
        total = row["total"]
        return total if total is not None else None

    # ── Instruments Cache ────────────────────────────────────

    async def save_instrument_rows(self, rows: list[InstrumentRow]) -> int:
        """Upsert instrument rows into the cache. Returns count saved."""
        if self._db is None:
            raise RuntimeError("store not initialized")
        now = datetime.now(timezone.utc).isoformat()
        values = [
            (
                r.venue,
                r.network,
                r.market_type,
                r.base,
                r.quote,
                r.venue_symbol,
                r.min_qty,
                r.qty_step,
                r.price_step,
                r.min_notional,
                r.taker_fee_rate,
                r.maker_fee_rate,
                r.contract_size,
                int(r.is_inverse),
                r.listing_status,
                now,
            )
            for r in rows
        ]
        await self._db.executemany(
            """INSERT OR REPLACE INTO instruments
               (venue, network, market_type, base, quote, venue_symbol,
                min_qty, qty_step, price_step, min_notional,
                taker_fee_rate, maker_fee_rate, contract_size,
                is_inverse, listing_status, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        await self._db.commit()
        return len(values)

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

    # ── Funding rate snapshots ───────────────────────────────

    async def insert_funding_snapshot(
        self,
        venue: str,
        symbol: str,
        funding_rate: float | None,
        next_funding_time: float | None = None,
        mark_price: float | None = None,
    ) -> None:
        """Record a funding rate observation for a perp instrument."""
        if self._db is None:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO funding_rate_snapshots "
            "(venue, symbol, funding_rate, mark_price, next_funding_time, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (venue, symbol, funding_rate, mark_price, next_funding_time),
        )
        await self._db.commit()

    async def get_latest_funding_rates(
        self,
    ) -> list[dict]:
        """Return the most-recent funding rate snapshot per (venue, symbol)."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT venue, symbol, funding_rate, mark_price, next_funding_time, "
            "MAX(fetched_at) as fetched_at "
            "FROM funding_rate_snapshots "
            "GROUP BY venue, symbol "
            "ORDER BY venue, symbol"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_funding_history(
        self,
        venue: str,
        symbol: str,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent funding rate history for one instrument."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT * FROM funding_rate_snapshots WHERE venue = ? AND symbol = ? ORDER BY fetched_at DESC LIMIT ?",
            (venue, symbol, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Watch candles (price-watch sliding window) ──────────

    async def upsert_watch_candle(
        self,
        asset: str,
        venue: str,
        ts: str,
        open_px: float,
        high_px: float,
        low_px: float,
        close_px: float,
        interval: str = "5m",
    ) -> None:
        """Upsert one candlestick observation into the watch window."""
        if self._db is None:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO watch_candles (asset, venue, interval, ts, open, high, low, close) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (asset, venue, interval, ts, open_px, high_px, low_px, close_px),
        )
        await self._db.commit()

    async def upsert_watch_candles(self, rows: list[tuple]) -> int:
        """Bulk upsert many candlestick rows in one transaction.

        ``rows`` is a sequence of ``(asset, venue, ts, open, high, low, close, interval)``.
        Returns the number of rows written. A full window is thousands of candles,
        so one transaction per symbol avoids thousands of separate commits.
        """
        if self._db is None or not rows:
            return 0
        await self._db.executemany(
            "INSERT OR REPLACE INTO watch_candles (asset, venue, ts, open, high, low, close, interval) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._db.commit()
        return len(rows)

    async def get_watch_candles(self, asset: str, venue: str, since_ts: str, interval: str = "5m") -> list[dict]:
        """Return candles for one (asset, venue, interval) with ts >= since_ts, ascending."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT * FROM watch_candles WHERE asset = ? AND venue = ? AND interval = ? AND ts >= ? ORDER BY ts ASC",
            (asset, venue, interval, since_ts),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_latest_watch_candle(self, asset: str, venue: str, interval: str = "5m") -> dict | None:
        """Return the most recent candlestick for one (asset, venue, interval), or None."""
        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT * FROM watch_candles WHERE asset = ? AND venue = ? AND interval = ? ORDER BY ts DESC LIMIT 1",
            (asset, venue, interval),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_earliest_watch_candle(self, asset: str, venue: str, interval: str = "5m") -> dict | None:
        """Return the oldest candlestick for one (asset, venue, interval), or None."""
        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT * FROM watch_candles WHERE asset = ? AND venue = ? AND interval = ? ORDER BY ts ASC LIMIT 1",
            (asset, venue, interval),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ── Derived candles (coarse aggregated from the base 5m series) ──

    async def upsert_derived_candles(self, rows: list[tuple]) -> int:
        """Bulk upsert aggregated coarse bars in one transaction.

        ``rows`` is a sequence of ``(asset, venue, interval, ts, open, high, low, close, volume)``.
        Returns the number of rows written.
        """
        if self._db is None or not rows:
            return 0
        await self._db.executemany(
            "INSERT OR REPLACE INTO derived_candles "
            "(asset, venue, interval, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._db.commit()
        return len(rows)

    async def get_derived_candles(self, asset: str, venue: str, interval: str, since_ts: str) -> list[dict]:
        """Return derived candles for one (asset, venue, interval) with ts >= since_ts, ascending."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT * FROM derived_candles WHERE asset = ? AND venue = ? AND interval = ? AND ts >= ? ORDER BY ts ASC",
            (asset, venue, interval, since_ts),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_latest_derived_candle(self, asset: str, venue: str, interval: str) -> dict | None:
        """Return the most recent derived candle for one (asset, venue, interval), or None."""
        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT * FROM derived_candles WHERE asset = ? AND venue = ? AND interval = ? ORDER BY ts DESC LIMIT 1",
            (asset, venue, interval),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_earliest_derived_candle(self, asset: str, venue: str, interval: str) -> dict | None:
        """Return the oldest derived candle for one (asset, venue, interval), or None."""
        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT * FROM derived_candles WHERE asset = ? AND venue = ? AND interval = ? ORDER BY ts ASC LIMIT 1",
            (asset, venue, interval),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ── Trade log (manual per-order journal) ─────────────────────

    async def record_trade(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        notional_usd: float,
        ts: str | None = None,
        venue: str | None = None,
        tag: str | None = None,
        fee_usd: float | None = None,
        pnl_usd: float | None = None,
        strategy: str | None = None,
        reason: str | None = None,
        note: str | None = None,
    ) -> None:
        """Record one manual trade. A sell with no explicit pnl auto-matches the
        most recent unmatched buy for that symbol and computes pnl."""
        if self._db is None:
            return
        ts = ts or datetime.now(timezone.utc).isoformat()
        matched_buy_id = None
        if side == "sell" and pnl_usd is None:
            buy = await self._find_unmatched_buy(symbol)
            if buy is not None:
                matched_buy_id = buy["id"]
                pnl_usd = (price - buy["price"]) * qty - (fee_usd or 0.0) - (buy.get("fee_usd") or 0.0)
        await self._db.execute(
            "INSERT OR REPLACE INTO trades "
            "(id, ts, venue, symbol, tag, side, qty, price, notional_usd, fee_usd, pnl_usd, "
            " strategy, reason, note, matched_buy_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade_id,
                ts,
                venue,
                symbol,
                tag,
                side,
                qty,
                price,
                notional_usd,
                fee_usd,
                pnl_usd,
                strategy,
                reason,
                note,
                matched_buy_id,
                ts,
            ),
        )
        await self._db.commit()

    async def _find_unmatched_buy(self, symbol: str) -> dict | None:
        """Return the most recent buy for ``symbol`` not yet matched to a sell."""
        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT * FROM trades WHERE symbol = ? AND side = 'buy' "
            "AND id NOT IN (SELECT matched_buy_id FROM trades WHERE side='sell' "
            "AND matched_buy_id IS NOT NULL) ORDER BY ts DESC LIMIT 1",
            (symbol,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_trades(self, tag: str | None = None, limit: int = 200) -> list[dict]:
        """Return trade-log rows newest-first, optionally filtered by tag."""
        if self._db is None:
            return []
        if tag is None:
            cursor = await self._db.execute("SELECT * FROM trades ORDER BY ts DESC LIMIT ?", (limit,))
        else:
            cursor = await self._db.execute("SELECT * FROM trades WHERE tag = ? ORDER BY ts DESC LIMIT ?", (tag, limit))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_trade(self, trade_id: str) -> dict | None:
        """Return one trade by id, or None."""
        if self._db is None:
            return None
        cursor = await self._db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def delete_trade(self, trade_id: str) -> int:
        """Delete one trade by id. Returns rows deleted."""
        if self._db is None:
            return 0
        cursor = await self._db.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        await self._db.commit()
        return cursor.rowcount

    # ── Telegram subscribers (dynamic watch recipients) ──────────

    async def add_subscriber(self, chat_id: str) -> None:
        """Add a chat_id to the Telegram broadcast list (idempotent)."""
        if self._db is None:
            return
        await self._db.execute(
            "INSERT OR IGNORE INTO telegram_subscribers (chat_id, added_at) VALUES (?, ?)",
            (chat_id, datetime.now(timezone.utc).isoformat()),
        )
        await self._db.commit()

    async def remove_subscriber(self, chat_id: str) -> int:
        """Remove a chat_id. Returns rows deleted."""
        if self._db is None:
            return 0
        cursor = await self._db.execute("DELETE FROM telegram_subscribers WHERE chat_id = ?", (chat_id,))
        await self._db.commit()
        return cursor.rowcount

    async def list_subscribers(self) -> list[str]:
        """Return all dynamically-subscribed chat ids."""
        if self._db is None:
            return []
        cursor = await self._db.execute("SELECT chat_id FROM telegram_subscribers ORDER BY added_at")
        rows = await cursor.fetchall()
        return [r["chat_id"] for r in rows]

    # ── Hedged positions ────────────────────────────────────

    async def create_hedged_position(
        self,
        position_id: str,
        base: str,
        venue_long: str,
        venue_short: str,
        notional_usd: float,
        intent_open: str,
        leg_long_id: str,
        leg_short_id: str,
        rate_a: float | None = None,
        rate_b: float | None = None,
    ) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "INSERT INTO hedged_positions "
            "(position_id, base, venue_long, venue_short, notional_usd, "
            "intent_open, leg_long_id, leg_short_id, rate_at_open_a, rate_at_open_b) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                position_id,
                base,
                venue_long,
                venue_short,
                notional_usd,
                intent_open,
                leg_long_id,
                leg_short_id,
                rate_a,
                rate_b,
            ),
        )
        await self._db.commit()

    async def get_open_hedged_positions(self) -> list[dict]:
        if self._db is None:
            return []
        cursor = await self._db.execute("SELECT * FROM hedged_positions WHERE status = 'OPEN' ORDER BY opened_at DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def close_hedged_position(
        self,
        position_id: str,
        intent_close: str,
    ) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "UPDATE hedged_positions SET status = 'CLOSED', intent_close = ?, "
            "closed_at = datetime('now') WHERE position_id = ?",
            (intent_close, position_id),
        )
        await self._db.commit()

    # ── Cross-venue arbitrage cycles ────────────────────────

    async def create_arbitrage_cycle(self, **fields) -> None:
        """Insert one arbitrage cycle using primitive scalar fields."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        required = (
            "cycle_id",
            "base",
            "market_type",
            "direction",
            "venue_buy",
            "venue_sell",
            "symbol_buy",
            "symbol_sell",
            "target_qty_base",
        )
        missing = [name for name in required if name not in fields]
        if missing:
            raise ValueError(f"Missing arbitrage cycle fields: {', '.join(missing)}")
        now = datetime.now(timezone.utc).isoformat()
        values = {
            "opportunity_id": None,
            "execution_context_json": None,
            "opened_qty_base": 0.0,
            "closed_qty_base": 0.0,
            "status": "DETECTED",
            "expected_net_pnl_usd": None,
            "realized_gross_pnl_usd": None,
            "realized_fee_usd": None,
            "realized_funding_usd": None,
            "realized_slippage_usd": None,
            "realized_net_pnl_usd": None,
            "residual_exposure_usd": 0.0,
            "max_unhedged_ms": 0,
            "failure_reason": None,
            "created_at": now,
            "updated_at": now,
            "opened_at": None,
            "closed_at": None,
        }
        values.update({key: fields[key] for key in required})
        unknown = set(fields) - values.keys()
        if unknown:
            raise ValueError(f"Unsupported arbitrage cycle fields: {', '.join(sorted(unknown))}")
        values.update({key: fields[key] for key in values if key in fields})
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        await self._db.execute(
            f"INSERT INTO arbitrage_cycles ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )
        await self._db.commit()

    async def get_arbitrage_cycle(self, cycle_id: str) -> dict | None:
        """Return one arbitrage cycle row, or ``None``."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        cursor = await self._db.execute("SELECT * FROM arbitrage_cycles WHERE cycle_id = ?", (cycle_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_arbitrage_cycles(self, *, status: str | None = None, limit: int = 100) -> list[dict]:
        """Return arbitrage cycles newest first, optionally filtered by status."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        if status is None:
            cursor = await self._db.execute(
                "SELECT * FROM arbitrage_cycles ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM arbitrage_cycles WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        return [dict(row) for row in await cursor.fetchall()]

    async def update_arbitrage_cycle(self, cycle_id: str, **fields) -> None:
        """Update selected cycle fields and refresh ``updated_at``."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        allowed = {
            "opportunity_id",
            "status",
            "opened_qty_base",
            "closed_qty_base",
            "expected_net_pnl_usd",
            "realized_gross_pnl_usd",
            "realized_fee_usd",
            "realized_funding_usd",
            "realized_slippage_usd",
            "realized_net_pnl_usd",
            "residual_exposure_usd",
            "max_unhedged_ms",
            "failure_reason",
            "opened_at",
            "closed_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported arbitrage cycle fields: {', '.join(sorted(unknown))}")
        if not fields:
            return
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        assignments = ", ".join(f"{column} = ?" for column in fields)
        cursor = await self._db.execute(
            f"UPDATE arbitrage_cycles SET {assignments} WHERE cycle_id = ?",
            (*fields.values(), cycle_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Arbitrage cycle '{cycle_id}' does not exist")
        await self._db.commit()

    async def create_arbitrage_cycle_leg(self, **fields) -> None:
        """Insert one leg for an arbitrage cycle."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        required = (
            "leg_id",
            "cycle_id",
            "role",
            "venue",
            "symbol",
            "side",
            "target_qty_base",
        )
        missing = [name for name in required if name not in fields]
        if missing:
            raise ValueError(f"Missing arbitrage leg fields: {', '.join(missing)}")
        values = {
            "filled_qty_base": 0.0,
            "avg_price": None,
            "fee_usd": 0.0,
            "client_order_id": None,
            "venue_order_id": None,
            "status": "PENDING_SEND",
            "sent_at": None,
            "completed_at": None,
            "error_msg": None,
        }
        values.update({key: fields[key] for key in required})
        unknown = set(fields) - values.keys()
        if unknown:
            raise ValueError(f"Unsupported arbitrage leg fields: {', '.join(sorted(unknown))}")
        values.update({key: fields[key] for key in values if key in fields})
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        await self._db.execute(
            f"INSERT INTO arbitrage_cycle_legs ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )
        await self._db.commit()

    async def get_arbitrage_cycle_legs(self, cycle_id: str) -> list[dict]:
        """Return legs for a cycle in insertion order."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        cursor = await self._db.execute(
            "SELECT * FROM arbitrage_cycle_legs WHERE cycle_id = ? ORDER BY rowid", (cycle_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_arbitrage_cycle_leg(self, leg_id: str) -> dict | None:
        """Return one arbitrage cycle leg, or ``None``."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        cursor = await self._db.execute("SELECT * FROM arbitrage_cycle_legs WHERE leg_id = ?", (leg_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_arbitrage_cycle_leg(self, leg_id: str, **fields) -> None:
        """Update selected fields on an arbitrage cycle leg."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        allowed = {
            "role",
            "venue",
            "symbol",
            "side",
            "target_qty_base",
            "filled_qty_base",
            "avg_price",
            "fee_usd",
            "client_order_id",
            "venue_order_id",
            "status",
            "sent_at",
            "completed_at",
            "error_msg",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported arbitrage leg fields: {', '.join(sorted(unknown))}")
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        cursor = await self._db.execute(
            f"UPDATE arbitrage_cycle_legs SET {assignments} WHERE leg_id = ?",
            (*fields.values(), leg_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Arbitrage leg '{leg_id}' does not exist")
        await self._db.commit()

    async def insert_arbitrage_fill(self, **fields) -> bool:
        """Insert a fill idempotently; return ``False`` for a duplicate trade."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        required = (
            "fill_id",
            "cycle_id",
            "leg_id",
            "venue",
            "trade_id",
            "quantity",
            "price",
        )
        missing = [name for name in required if name not in fields]
        if missing:
            raise ValueError(f"Missing arbitrage fill fields: {', '.join(missing)}")
        values = {
            "fee_usd": 0.0,
            "fee_currency": None,
            "exchange_timestamp": None,
            "received_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        values.update({key: fields[key] for key in required})
        unknown = set(fields) - values.keys()
        if unknown:
            raise ValueError(f"Unsupported arbitrage fill fields: {', '.join(sorted(unknown))}")
        values.update({key: fields[key] for key in values if key in fields})
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        cursor = await self._db.execute(
            f"INSERT INTO arbitrage_fills ({', '.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT(venue, trade_id) DO NOTHING",
            tuple(values[column] for column in columns),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def get_arbitrage_fills(self, cycle_id: str, leg_id: str | None = None) -> list[dict]:
        """Return fills for a cycle, optionally restricted to one leg."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        if leg_id is None:
            cursor = await self._db.execute(
                "SELECT * FROM arbitrage_fills WHERE cycle_id = ? ORDER BY received_timestamp", (cycle_id,)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM arbitrage_fills WHERE cycle_id = ? AND leg_id = ? ORDER BY received_timestamp",
                (cycle_id, leg_id),
            )
        return [dict(row) for row in await cursor.fetchall()]

    async def list_unfinished_arbitrage_cycles(self) -> list[dict]:
        """Return cycles that need execution recovery after a restart."""
        if self._db is None:
            raise RuntimeError("Store not initialized. Call initialize() first.")
        cursor = await self._db.execute(
            "SELECT * FROM arbitrage_cycles WHERE status NOT IN ('CLOSED', 'MANUAL_REVIEW') "
            "ORDER BY created_at"
        )
        return [dict(row) for row in await cursor.fetchall()]

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
            compensation_avg_price=row["compensation_avg_price"],
            compensation_fee_usd=row["compensation_fee_usd"],
            instrument_selection_log=row["instrument_selection_log"],
            funding_rate_at_plan=row["funding_rate_at_plan"],
            next_funding_time_at_plan=row["next_funding_time_at_plan"],
            leverage=row["leverage"],
            filled_at=row["filled_at"],
            compensated_at=row["compensated_at"],
            execution_context_json=row["execution_context_json"],
        )

    @staticmethod
    def _side_from_intent_json(raw_intent_json: str, venue: str) -> str:
        try:
            intent_data = json.loads(raw_intent_json)
        except (json.JSONDecodeError, TypeError):
            return "buy"

        side = intent_data.get("side", "buy")
        leg_configs = intent_data.get("leg_configs") or {}
        if isinstance(leg_configs, dict):
            leg_config = leg_configs.get(venue) or {}
            if isinstance(leg_config, dict) and leg_config.get("side") is not None:
                side = leg_config["side"]
        return side if side in ("buy", "sell") else "buy"
