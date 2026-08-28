# Persistence Layer

oneFill uses dual persistence: SQLite for the transactional state machine, and JSONL for the append-only audit trail.

## Design principle

```
SQLite (data/onefill.db)     ←→     JSONL (logs/audit-YYYY-MM-DD.jsonl)
  ↑ transactional queries            ↑ append-only, immutable
  ↑ query/list/recover               ↑ full audit trail
  ↑ can be rebuilt from JSONL        ↑ source of truth for disputes
```

## SQLite schema

### `intents` table

| Column | Type | Description |
|---|---|---|
| `intent_id` | TEXT PRIMARY KEY | UUID |
| `status` | TEXT NOT NULL | Current state machine state |
| `raw_intent_json` | TEXT NOT NULL | Full Intent serialized as JSON |
| `created_at` | TEXT NOT NULL | ISO 8601 timestamp |
| `updated_at` | TEXT NOT NULL | Last state transition |

### `legs` table

| Column | Type | Description |
|---|---|---|
| `leg_id` | TEXT PRIMARY KEY | UUID |
| `intent_id` | TEXT NOT NULL | FK → intents |
| `venue` | TEXT NOT NULL | Exchange name |
| `instrument_venue_symbol` | TEXT | Venue-native symbol |
| `instrument_base` | TEXT | Base asset |
| `instrument_quote` | TEXT | Quote asset |
| `instrument_market_type` | TEXT | "spot" or "perp" |
| `planned_notional_usd` | REAL | Planned size |
| `planned_qty_base` | REAL | Size in base units |
| `status` | TEXT | Leg state machine |
| `sent_at` | TEXT | Order submission timestamp |
| `order_id` | TEXT | Exchange order ID |
| `filled_amount` | REAL | Filled quantity |
| `avg_price` | REAL | Volume-weighted average fill price |
| `fee_usd` | REAL | Actual fee paid |
| `error_msg` | TEXT | Error from exchange |
| `compensation_order_id` | TEXT | Reverse order exchange ID |
| `compensation_filled_amount` | REAL | Compensation fill quantity |
| `compensation_avg_price` | REAL | Compensation fill price |
| `compensation_fee_usd` | REAL | Compensation fee |
| `funding_rate_at_plan` | REAL | Perp funding rate at plan time |
| `leverage` | INTEGER | Leverage (1 for spot) |
| `filled_at` | TEXT | Fill timestamp |
| `compensated_at` | TEXT | Compensation timestamp |

### `instruments` table

Cached instruments from venue market APIs (TTL 24h). Enables fast startup without re-fetching market data on every launch.

### `funding_rate_snapshots` table

Point-in-time funding rate records for historical analysis and arbitrage backtesting.

### `hedged_positions` table

Tracks delta-neutral positions opened by the funding arbitrage strategy. Links the long and short legs with entry/exit intents.

### `watch_candles` table

Shared OHLCV store for the price-watch daemon and the backtester — both replay the *same*
persisted candle series through `CandleService` (see [Shared candle store](#shared-candle-store)). Each
row is tagged with its bar `interval` (`5m`/`1h`/`4h`/`1d`), so every resolution is stored as a
separate, never-mixed series.

| Column | Type | Description |
|---|---|---|
| `asset` | TEXT | Base asset (e.g. "BTC") |
| `venue` | TEXT | Exchange name (`hyperliquid` / `binance`) |
| `interval` | TEXT | Bar resolution (`5m`, `1h`, `4h`, `1d`) |
| `ts` | TEXT | ISO 8601 bar open time |
| `open` / `high` / `low` / `close` | REAL | OHLC |
| `volume` | REAL | Base volume |

`UNIQUE(asset, venue, interval, ts)` — upserts are idempotent (`INSERT OR REPLACE`), indexed by `(asset, venue, interval)`.

## JSONL audit trail

Every state-changing event is appended to `logs/audit-YYYY-MM-DD.jsonl`:

```json
{"intent_id": "abc-123", "event_type": "INTENT_CREATED", "ts": "2026-07-11T12:00:00Z", ...}
{"intent_id": "abc-123", "event_type": "LEG_CREATED", "leg_id": "leg-1", ...}
{"intent_id": "abc-123", "event_type": "ORDER_SENT", "leg_id": "leg-1", "order_id": "...", ...}
{"intent_id": "abc-123", "event_type": "ORDER_FILLED", "leg_id": "leg-1", "filled": 0.1, ...}
```

Properties:
- **Append-only** — once written, never modified
- **One file per UTC day** — rotates at midnight
- **Immutable** — full audit trail for dispute resolution
- **Rebuildable** — SQLite can be regenerated from JSONL if corrupted

## PersistenceStore API

```python
class PersistenceStore:
    # Lifecycle
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...

    # Intent CRUD
    async def create_intent(self, intent, status="PENDING") -> None: ...
    async def get_intent(self, intent_id) -> IntentRow | None: ...
    async def update_intent_status(self, intent_id, status) -> None: ...
    async def list_intents(self, *, status=None, limit=50) -> list[IntentRow]: ...

    # Leg CRUD
    async def create_leg(self, **fields) -> str: ...           # returns leg_id
    async def get_leg(self, leg_id) -> LegRow | None: ...
    async def get_legs_for_intent(self, intent_id) -> list[LegRow]: ...
    async def update_leg(self, leg_id, **fields) -> None: ...

    # Audit
    async def append_event(self, intent_id, event_type, payload) -> None: ...

    # Blocking check
    async def is_blocked_by_needs_manual(self) -> bool: ...

    # Risk queries
    async def get_daily_pnl(self) -> float | None: ...
    async def get_venue_exposure(self, venue) -> float | None: ...

    # Instrument cache
    async def save_instruments(self, instruments) -> int: ...
    async def load_instruments(self) -> list[Instrument]: ...

    # Funding rate snapshots
    async def insert_funding_snapshot(self, ...) -> None: ...
    async def get_latest_funding_rates(self) -> list[dict]: ...

    # Hedged positions
    async def create_hedged_position(self, ...) -> None: ...
    async def close_hedged_position(self, position_id, intent_close) -> None: ...
```

## Shared candle store

The price-watch daemon and the backtester both read `watch_candles` through `CandleService`
(`src/strategy/candles.py`), so a watch run and a backtest never fetch the same history twice.

- **Incremental fill** — on each cycle the service reads the store and fetches only the missing
  tail; it never re-downloads a window it already holds.
- **Startup seed** — on watch start, an asset whose store is empty is seeded back as far as the
  exchange serves, across the configured intervals (default `5m/1h/4h/1d`; each interval to its own
  depth — 5m clamps to ~17 days on Hyperliquid, coarser bars go deeper). A restart that already has
  data instead closes the gap back to the last stored candle, so downtime longer than the lookback
  window leaves no hole.
- **No prune** — candles accumulate indefinitely, so deep history compounds over time (this is the
  only honest way to build deep 5m history for Hyperliquid).

### Exchange data limits

Real exchange availability caps how deep a fetch can go. This is a **candle-count** limit, not a
data-retention limit — both venues keep full history at coarser intervals:

| Venue | Per-request limitation | Effect at 5m |
|---|---|---|
| Hyperliquid | ~5000 candles per retrievable window; cannot be paginated further | ~17 days |
| Binance | 1000 candles/request; we page manually (ccxt `paginate` itself caps at ~10000) | up to `--history-days` |

So a 5m strategy on Hyperliquid is bound to ~17 days of history. Deeper 5m must be accumulated over
time; for long-horizon analysis use the coarser intervals (1h/4h/1d) as separate, never-mixed series.

## Hard rule: persist before send

The Executor **must** write a leg row to SQLite before calling `create_order()`. The sequence is:

```
1. INSERT INTO legs (...) VALUES (...)
2. append JSONL audit event "LEG_CREATED"
3. exchange.create_order(...)               # ← only after steps 1–2 complete
```

This ensures that if the process crashes after sending the order (step 3), the leg record (steps 1–2) already exists. Recovery can then query the exchange for the order's fill status using the stored `order_id`.
