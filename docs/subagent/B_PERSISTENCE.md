# Subagent B — Persistence Layer Implementation

> **Depends on:** Subagent 0 (contract types + schema strings must be committed on main)
> **Runs in:** a git worktree branched from the post-Stage-0 commit
> **Parallel with:** Subagent A (Market) and Subagent C (Coordinator)
> **Estimated LOC:** ~500 new

## Purpose

Implement the `PersistenceStore` — the only module that reads/writes the SQLite state machine and appends to the JSONL audit log. Every `create_order` call in the system must be preceded by a persisted leg row; every state transition must be recorded. This module is the **single writer** to the persistence layer — higher layers never touch SQLite or JSONL directly.

The SQL schema strings (`INTENTS_TABLE`, `LEGS_TABLE`, `AUDIT_TABLE`) are already defined in `src/persistence/schema.py` by Stage 0. **You implement the store class and its tests.**

## Data contract

The store deals with typed dicts / dataclasses for row shapes. You can define these as lightweight dataclasses or TypedDicts in `src/persistence/store.py` itself (they are internal to this module):

```python
@dataclass
class IntentRow:
    intent_id: str
    status: str
    raw_intent_json: str     # json.dumps(Intent dataclass)
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
    quote_preference_matched: str | None
    planned_notional_usd: float
    planned_qty_base: float
    status: str
    sent_at: str | None
    order_id: str | None
    filled_amount: float | None
    avg_price: float | None
    fee_usd: float | None
    error_msg: str | None
    compensation_order_id: str | None
    compensation_filled_amount: float | None
    instrument_selection_log: str | None
    funding_rate_at_plan: float | None
    next_funding_time_at_plan: float | None

@dataclass
class AuditEvent:
    id: int
    intent_id: str
    timestamp: str
    event_type: str           # e.g. "intent_created", "leg_sent", "leg_filled", "reconcile_started"
    payload_json: str
```

## Files to create / modify

### NEW: `src/persistence/store.py`

```python
import aiosqlite
import json
from pathlib import Path
from datetime import datetime, timezone

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
        """
        Create/verify sqlite_path directory, open connection, execute
        INTENTS_TABLE / LEGS_TABLE / AUDIT_TABLE.
        Create jsonl_dir if needed.
        """

    # ── Intent CRUD ──────────────────────────────────────────

    async def create_intent(self, intent: "Intent") -> None:
        """Insert a new row into intents table. Status = 'PENDING'."""

    async def get_intent(self, intent_id: str) -> IntentRow | None:
        """Return the intent row or None."""

    async def update_intent_status(self, intent_id: str, status: str) -> None:
        """
        Update the intent's status and updated_at timestamp.
        MUST call append_event() internally after updating.
        """

    async def list_intents(self, *, status: str = None, limit: int = 50) -> list[IntentRow]:
        """
        Return recent intents, newest first.
        If status is provided, filter by status.
        """

    # ── Leg CRUD ─────────────────────────────────────────────

    async def create_leg(self, leg: "PlannedLeg", intent_id: str) -> str:
        """
        Insert a leg row. Generate a leg_id (uuid4).
        Returns the new leg_id.
        MUST be called BEFORE any create_order() call in Executor.
        """

    async def get_leg(self, leg_id: str) -> LegRow | None: ...

    async def get_legs_for_intent(self, intent_id: str) -> list[LegRow]: ...

    async def update_leg(self, leg_id: str, **fields) -> None:
        """
        Update any subset of leg fields. Only updates fields that are
        provided as keyword arguments. Updates the intent's updated_at too.
        MUST call append_event() internally after updating.
        """

    # ── Audit ────────────────────────────────────────────────

    async def append_event(self, intent_id: str, event_type: str, payload: dict) -> None:
        """
        Insert into audit_events table AND append a line to today's JSONL:
        logs/audit-YYYY-MM-DD.jsonl
        JSONL line = json.dumps({"ts": iso_now, "intent_id": ..., "event_type": ..., "payload": ...})
        """

    # ── Blocking check ───────────────────────────────────────

    async def is_blocked_by_needs_manual(self) -> bool:
        """
        Return True if any intent is in state ROLLED_BACK_FAILED (= NEEDS_MANUAL).
        Executor MUST check this before executing any new Intent.
        """

    # ── Cleanup ──────────────────────────────────────────────

    async def close(self) -> None:
        """Close the SQLite connection."""
```

### MODIFY: `src/persistence/__init__.py`

Add exports for `PersistenceStore`, `IntentRow`, `LegRow`, `AuditEvent`.

## Key design constraints

1. **Always append_event() when state mutates.** Every `update_intent_status` and `update_leg` must internally call `append_event`. The JSONL audit log must be reconstructable.
2. **Thread safety is not a concern** — everything runs in a single asyncio event loop (one async `PersistenceStore` per process).
3. **SQLite is in WAL mode** — enable on `initialize()`: `PRAGMA journal_mode=WAL;`
4. **JSONL is append-only** — never rewrite, never seek. Just open-with-append + write one line + close (or use `aiofiles`).
5. **Don't import from `src.coordinator` or `src.market`** — use duck-typing. The store takes dataclass-like objects that have the expected attributes. This keeps Persistence decoupled.

## Tests to write (`tests/persistence/`)

| Test file | What it covers |
|---|---|
| `test_store_init.py` | `initialize()` creates tables, WAL mode, jsonl_dir created |
| `test_intent_crud.py` | create → get → list → filter by status |
| `test_leg_crud.py` | create leg → get leg → get legs for intent → update leg fields |
| `test_state_transitions.py` | intent: PENDING → VALIDATED → EXECUTING → ALL_FILLED (all via store) |
| `test_audit.py` | `append_event` writes to both SQLite audit_events and JSONL; JSONL line is valid JSON; round-trip verification |
| `test_blocking.py` | `is_blocked_by_needs_manual` returns True when an intent is ROLLED_BACK_FAILED, False otherwise |
| `test_concurrent.py` | (optional) create 20 intents concurrently via asyncio.gather, verify all persisted |

All tests use an in-memory SQLite database (`:memory:`) and a temporary directory for JSONL (pytest `tmp_path` fixture). No real filesystem state leaks between tests.

## Verification

```bash
uv run pytest tests/persistence -v      # all green, >90% line coverage
uv run python -c "
from src.persistence.store import PersistenceStore
from pathlib import Path
import asyncio, tempfile
async def smoke():
    with tempfile.TemporaryDirectory() as d:
        store = PersistenceStore(Path(d) / 'test.db', Path(d))
        await store.initialize()
        print(f'store ok, wal={store._db}')  # just checking it doesn't crash
asyncio.run(smoke())
"
```

## Commit message

```
stage 1: persistence layer (SQLite + JSONL)

Implement PersistenceStore: async SQLite-backed state machine with
append-only JSONL audit log. Supports intent/leg CRUD, state
transitions, NEEDS_MANUAL blocking check. WAL mode, in-memory-safe
for testing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Out of scope

- ❌ Schema migrations (Alembic) — MVP uses CREATE TABLE IF NOT EXISTS
- ❌ Any Coordinator business logic — this store is a dumb persistence layer
- ❌ CLI query commands (those call `PersistenceStore`, but are implemented by Subagent D)
- ❌ Postgres or any other backend
- ❌ Importing from `src.coordinator` or `src.market`
