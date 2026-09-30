---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-30
applies_to: src/persistence/
---

# Persistence Layer

oneFill uses dual persistence: SQLite for the transactional state machine, and JSONL for the append-only audit trail.

## Design principle

<figure markdown="span">
  <img src="../../../assets/base-persistence-layer.svg" alt="base-persistence-layer" width="100%">
</figure>

（图源码 `docs/assets/base-persistence-layer.dot`，重新生成：`scripts/render_diagrams.sh base-persistence-layer`）

**两种落盘的分工不是冗余，是刻意的**：SQLite 支持事务性查询与状态机推进，
JSONL 只追加、不可变，是争议发生时的真值源。`store.py` 的 `*Row` 数据结构是行的形态——
**持久化层只读写列，不构造 `Intent` / `Instrument` / `Quote`**，转换函数归拥有该领域类型的层。

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
| `quote_preference_matched` | TEXT | Quote selected from the requested preference list |
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
| `instrument_selection_log` | TEXT | Serialized instrument-selection decision |
| `funding_rate_at_plan` | REAL | Perp funding rate at plan time |
| `next_funding_time_at_plan` | REAL | Next funding timestamp captured at planning |
| `leverage` | INTEGER | Leverage (1 for spot) |
| `filled_at` | TEXT | Fill timestamp |
| `compensated_at` | TEXT | Compensation timestamp |
| `execution_context_json` | TEXT | PlannedLeg/Instrument snapshot for interrupted execution recovery |
| `planned_qty_native` | TEXT | Planned venue-native quantity |
| `filled_qty_native` | TEXT | Filled venue-native quantity |
| `compensation_filled_qty_native` | TEXT | Compensated venue-native quantity |
| `quantity_unit` | TEXT | `base` or `contracts` |
| `reason` | TEXT | Planning or execution reason |

### `orders` table

Each original split order and compensation order has a separate `OrderRow`. The primary key is
`client_order_id`; `leg_id` and `intent_id` associate it with the execution. `purpose` distinguishes
original and compensation orders. `request_json` stores the normalized request, `snapshot_json` the
cumulative observed execution, and `status`, `error_msg`, `created_at`, `updated_at` record progress.
Existing databases receive the new table and nullable Leg context column without removing existing rows.

The order row and a sending marker are committed before network I/O. Recovery uses client ID when
the exchange ID is missing. JSONL and SQLite are sequential writes, not one atomic transaction;
SQLite committed requests are the recovery source of truth. `execution_lock()` excludes concurrent
submit/recover/cancel operations sharing a database file.

### `order_fills` table

Each private fill is stored as a scalar row keyed by `(network, product_family, venue, symbol, trade_id)`.
The key makes replayed private-stream events idempotent while `client_order_id`, `leg_id`, and
`intent_id` associate the fill with the durable execution request.

| Column | Type | Description |
|---|---|---|
| `network` / `product_family` | TEXT | Exchange network and account family |
| `venue` / `symbol` | TEXT | Venue and native symbol |
| `trade_id` | TEXT | Venue trade identifier |
| `client_order_id` / `leg_id` / `intent_id` | TEXT | Durable order and execution references |
| `qty_native` / `qty_base` | TEXT | Native and normalized quantities |
| `price` / `notional_quote` | TEXT | Fill price and quote notional |
| `exchange_timestamp` | TEXT | Venue event timestamp |
| `side` / `settlement_asset` | TEXT | Fill side and settlement currency |
| `fees_json` / `fee_usd` | TEXT | Raw fee list and normalized USD fee |
| `realized_pnl_settlement` / `realized_pnl_usd` | TEXT | Realized PnL in settlement and USD |
| `valuation_price` / `valuation_timestamp` | TEXT | Price and time used for USD valuation |

Replayed fills can enrich missing fee, PnL, settlement, and valuation data without creating duplicates.
Conflicting values reject; a replay does not overwrite previously established execution facts.

### `instruments` table

Cached instruments from venue market APIs (TTL 24h), keyed by `(venue, network, market_type, venue_symbol)`. Rows preserve `settlement_asset`, `quantity_unit`, `contract_size`, `is_inverse` and `max_leverage`. Loads select the current network. Legacy instrument caches without sufficient contract metadata are rebuilt, rather than guessing COIN-M identity.

Execution context and order snapshots retain native quantities, account family, network, position effect and the pre-send baseline. Domain conversion belongs to `market/registry.py` and `coordinator/leg_context.py`; the persistence layer continues to store rows without importing domain objects. Old in-flight execution context that cannot establish those facts requires manual handling.

### `funding_rate_snapshots` table

Point-in-time funding rate records for historical analysis and arbitrage backtesting.

### `hedged_positions` table

Tracks delta-neutral positions opened by the funding arbitrage strategy. Links the long and short legs with entry/exit intents.

### Cross-venue arbitrage tables

`arbitrage_cycles` stores pair-level lifecycle and realized PnL, `arbitrage_cycle_legs`
stores each execution attempt, and `arbitrage_fills` stores normalized fills. The latter
has a unique `(venue, trade_id)` key so replayed private-stream events are idempotent.
These tables store scalar rows only and do not import the arbitrage domain package.

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

    # Status queries (generic — the blocking *policy* is the Coordinator's)
    async def count_intents_with_status(self, status: str) -> int: ...

    # Risk queries
    async def get_daily_pnl(self) -> float | None: ...
    async def get_venue_exposure(self, venue) -> float | None: ...

    # Instrument cache — rows only; market/registry.py converts to Instrument
    async def save_instrument_rows(self, rows: list[InstrumentRow]) -> int: ...
    async def load_instruments_by_query(self, *, base=None, venue=None, market_type=None) -> list[InstrumentRow]: ...

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

The order ledger also precedes step 3. Recovery can query using the persisted client ID even when
the process exits before an exchange order ID is recorded. See [execution reliability](base-execution-reliability.md).
