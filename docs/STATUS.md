# oneFill — Implementation Status

> Snapshot: 2026-05-24 · Branch: main
> Full context: `docs/PRD.md` (product spec) · `docs/REFACTOR_PLAN.md` (phased plan) · `CLAUDE.md` (architecture & invariants)

## Test Suite

```
263 passed · 0 failed · 0 skipped (non-network)
  9 network tests (Binance demo + Hyperliquid testnet, requires credentials)
```

| Package | Tests | Coverage |
|---|---|---|
| `tests/market/` | 72 | Asset, Instrument, Quote.estimate_fill, InstrumentRegistry, QuoteFetcher, MockExchange |
| `tests/persistence/` | 43 | PersistenceStore (SQLite + JSONL), Intent/Leg CRUD, audit log, blocking check, concurrent ops |
| `tests/coordinator/` | 82 | Planner, Validator, Executor, Reconciler, Orchestrator, state machine, full E2E pipeline — all using MockExchange + real PersistenceStore + real QuoteFetcher |
| `tests/cli/` | 41 | 17 parse tests + 24 command tests (order/query/list/cancel/recover/venues + help) |
| `tests/e2e/` | 9 mock + 9 network | Mock E2E (ALL_FILLED, REJECTED, ROLLED_BACK, NEEDS_MANUAL, DRY_RUN, crash recovery) + real testnet (Binance demo + Hyperliquid) |
| `tests/unit/exchanges/` | 14 | 10 list_markets tests + Binance demo connect + Hyperliquid testnet connect + BaseExchange network switching |
| `tests/unit/test_base_exchange.py` | 4 | Network switching, network info |

## What's Built

### Market Layer (`src/market/`) — COMPLETE
- `Asset` — user-facing base/quote handle (frozen, hashable)
- `Instrument` — (venue, market_type, base, quote) tuple, venue-native symbol, qty/price rounding, fee rates
- `InstrumentRegistry` — `load_all()` from real venues (1415 HL + 1995 Binance instruments), `find_one(base, venue, market_type, quote_preference)`, `add()` (test convenience), 12-24h TTL
- `Quote` — top-of-book + full depth (`_bids`/`_asks`), `estimate_fill(amount, side)` walks orderbook, returns `EstimatedFill(avg_price, slippage_pct, depth_consumed_levels, filled_fully)`
- `QuoteFetcher` — `fetch(instrument)` / `fetch_many(instruments)` via `asyncio.gather`, individual failures don't break batch
- `MockExchange` — canonical test double implementing `BaseExchange` with order lifecycle simulation, fail switches, balance/orderbook injection

### Persistence Layer (`src/persistence/`) — COMPLETE
- `PersistenceStore` — async SQLite (WAL mode) + append-only JSONL audit (`logs/audit-YYYY-MM-DD.jsonl`)
- Tables: `intents`, `legs`, `audit_events`
- Intent CRUD + Leg CRUD + `append_event()` dual-write
- `is_blocked_by_needs_manual()` — returns True when any intent is ROLLED_BACK_FAILED
- `create_leg()` accepts keyword args from Executor (avoids coupling to PlannedLeg)

### Coordinator (`src/coordinator/`) — COMPLETE
- `Intent` — user order intent (base, quote_preference, product, side, order_type, total_notional_usd, split, thresholds)
- `Plan` / `PlannedLeg` — planner output with instrument selection, estimated fill, fee, funding rate, selection log
- **Planner** — instrument selection via quote_preference matching, Quote-based fill/slippage/fee estimation, threshold checking, no side effects
- **Validator** — concurrent per-venue pre-flight checks (listing status, balance, qty rules, exchange config), `ValidationResult`
- **Executor** — persist-before-send, concurrent `asyncio.gather` order dispatch, fill polling loop with deadline, `ExecutionResult` / `LegExecution`
- **Reconciler** — reverse market orders for filled legs, best-effort cancel for pending legs, `NEEDS_MANUAL` escalation
- **Orchestrator** — wires Planner → Validator → Executor → Reconciler, blocks on NEEDS_MANUAL, `dry_run` support
- **State machine** — `is_valid_transition()` with full transition table

### CLI (`src/cli/`) — COMPLETE
- `src/cli/main.py` — typer app with 6 fully implemented commands: `order`, `query`, `list-intents`, `cancel`, `recover`, `venues`
- `src/cli/bootstrap.py` — `build_orchestrator()` with DI hooks (`_exchanges`, `_store`), `build_store()`
- JSON output (`--json`) + rich terminal rendering
- Exit codes: 0=ALL_FILLED, 2=REJECTED, 3=ROLLED_BACK, 4=NEEDS_MANUAL

### Exchange Layer (`src/exchanges/`) — COMPLETE
- `BaseExchange` — abstract interface: `connect`, `fetch_balance`, `fetch_orderbook`, `create_order`, `cancel_order`, `fetch_order`, `list_markets`, `connect_websocket`, `subscribe_orderbook`
- `CCXTExchange` — wraps ccxt for Hyperliquid + Binance
  - Hyperliquid: testnet support, HIP3 market filtering, vault address
  - Binance: demo trading via `enable_demo_trading(True)`
  - `list_markets()` converts ccxt market dicts to `Instrument` objects (spot/perp type mapping, precision extraction, inactive filtering, per-market error resilience)
- `ExchangeFactory` — creates exchanges from YAML config

### Real Testnet Connectivity — WORKING
- Binance demo: 1995 instruments, spot orders executed successfully ($10 BTC buy)
- Hyperliquid testnet: 1415 instruments, perp orders executed (ROLLED_BACK on partial fill)
- `onefill order --dry-run` produces real Plan output against both venues
- `onefill venues` shows connected exchanges

### Legacy Bot — UNCHANGED
- `VolumeEngine`, `ArbitrageEngine`, `HedgeVolumeStrategy`, `SpreadArbitrageStrategy` — preserved
- `python -m src.main --mode volume|arbitrage|both` still works

## Known Gaps

| Gap | Impact | Priority |
|---|---|---|
| 23 ruff warnings remain (bare except, old typing imports) | — | Already fixed (0 warnings) |
| No CI pipeline | Manual test runs only | Not in MVP scope |
| `venues` command doesn't show live connection status | Minor UX | Low |
| `cancel` command can't cancel exchange orders (store-only) | Requires exchange adapters be initialised | Low |
| Event loop closing warning in test teardown (aiosqlite thread) | Harmless | Low |
| Perp-specific features (set_leverage, margin checks, reduce_only) | Not needed for spot MVP | Post-MVP Stage 4 |

## Quick Commands

```bash
uv run pytest -m "not network"             # 263 core tests (fast, no network)
uv run pytest -m network                   # 9 exchange/E2E tests (requires secrets.yaml)
uv run pytest                              # all 272 tests
uv run onefill --help                      # CLI with 6 commands
uv run onefill order --dry-run --json \    # real Plan against testnet
  --base BTC --product spot --side buy --type market \
  --total-notional-usd 100 --split binance=0.5,hyperliquid=0.5
uv run python -m src.main --help           # legacy bot
uv run ruff check .                        # lint (0 errors)
```
