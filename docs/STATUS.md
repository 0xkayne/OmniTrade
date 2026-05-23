# oneFill — Implementation Status

> Snapshot: 2026-05-23 · Branch: main · Commit: 2a8fec2
> Full context: `docs/PRD.md` (product spec) · `docs/REFACTOR_PLAN.md` (phased plan) · `CLAUDE.md` (architecture & invariants)

## Test Suite

```
222 passed · 0 failed · 0 skipped
```

| Package | Tests | Coverage |
|---|---|---|
| `tests/market/` | 72 | Asset, Instrument, Quote.estimate_fill, InstrumentRegistry, QuoteFetcher, MockExchange |
| `tests/persistence/` | 43 | PersistenceStore (SQLite + JSONL), Intent/Leg CRUD, audit log, blocking check, concurrent ops |
| `tests/coordinator/` | 82 | Planner, Validator, Executor, Reconciler, Orchestrator, state machine, full E2E pipeline |
| `tests/cli/` | 17 | `parse_split`, `parse_quote_preference` edge cases |
| `tests/unit/exchanges/` | 4 | Hyperliquid testnet connect + Binance demo connect |
| `tests/unit/test_base_exchange.py` | 4 | Network switching, network info |

## What's Built

### Market Layer (`src/market/`) — COMPLETE for MVP
- `Asset` — user-facing base/quote handle (frozen, hashable)
- `Instrument` — (venue, market_type, base, quote) tuple, venue-native symbol, qty/price rounding, fee rates
- `InstrumentRegistry` — `load_all()` from venues, `find_one(base, venue, market_type, quote_preference)`, `add()` (test convenience), 12-24h TTL
- `Quote` — top-of-book + full depth (`_bids`/`_asks`), `estimate_fill(amount, side)` walks orderbook, returns `EstimatedFill(avg_price, slippage_pct, depth_consumed_levels, filled_fully)`
- `QuoteFetcher` — `fetch(instrument)` / `fetch_many(instruments)` via `asyncio.gather`, individual failures don't break batch
- `MockExchange` — configurable test double (canned orderbooks, balances, markets; order/error injection)

### Persistence Layer (`src/persistence/`) — COMPLETE for MVP
- `PersistenceStore` — async SQLite (WAL mode) + append-only JSONL audit (`logs/audit-YYYY-MM-DD.jsonl`)
- Tables: `intents`, `legs`, `audit_events`
- Intent CRUD + Leg CRUD + `append_event()` dual-write
- `is_blocked_by_needs_manual()` — returns True when any intent is ROLLED_BACK_FAILED
- Duck-typed row access (no imports from coordinator or market)

### Coordinator (`src/coordinator/`) — COMPLETE for MVP (mock-only)
- `Intent` — user order intent (base, quote_preference, product, side, order_type, total_notional_usd, split, thresholds)
- `Plan` / `PlannedLeg` — planner output with instrument selection, estimated fill, fee, funding rate, selection log
- **Planner** — instrument selection via quote_preference matching, Quote-based fill/slippage/fee estimation, threshold checking (`filled_fully`, `max_slippage_pct`, `max_fee_usd`), pure reads (no side effects)
- **Validator** — concurrent per-venue pre-flight checks (listing status, balance, qty rules, exchange config), `ValidationResult`
- **Executor** — persist-before-send invariant, concurrent `asyncio.gather` order dispatch, fill polling loop with deadline, `ExecutionResult` / `LegExecution`
- **Reconciler** — reverse market orders for filled legs, best-effort cancel for pending legs, `NEEDS_MANUAL` escalation, `ReconciliationResult` / `LegReconciliation`
- **Orchestrator** — wires Planner → Validator → Executor → Reconciler, checks `is_blocked_by_needs_manual()` before any new intent, `dry_run` support
- **State machine** — `is_valid_transition()` with full transition table

### CLI (`src/cli/`) — STUBS ONLY (commands raise NotImplementedError)
- `src/cli/bootstrap.py` — `build_orchestrator()` with DI hooks (`_exchanges`, `_store`)
- `src/cli/main.py` — typer app with 6 registered commands (`order`, `query`, `list_intents`, `cancel`, `recover`, `venues`), all stubs

### Exchange Layer (`src/exchanges/`) — PARTIAL
- `BaseExchange` — abstract interface: `connect`, `fetch_balance`, `fetch_orderbook`, `create_order`, `cancel_order`, `fetch_order`, `list_markets`, `connect_websocket`, `subscribe_orderbook`
- `CCXTExchange` — wraps ccxt for Hyperliquid + Binance
  - Hyperliquid: testnet support, HIP3 market filtering, vault address
  - Binance: demo trading via `enable_demo_trading(True)` auto-enabled in `connect()`
- `ExchangeFactory` — creates exchanges from YAML config

### Exchange Connectivity Tests — WORKING
- Hyperliquid testnet: `test_hyperliquid.py` passes (loads markets, queries balance)
- Binance demo: `test_binance.py` passes (3590 spot pairs, USDT balance 5000)

### Legacy Bot — UNCHANGED
- `VolumeEngine`, `ArbitrageEngine`, `HedgeVolumeStrategy`, `SpreadArbitrageStrategy` — all preserved
- `python -m src.main --mode volume|arbitrage|both` still works
- `config/volume_farming.yaml` still valid (Hyperliquid only, Lighter removed)

## What's NOT Yet Built

### Round 2: CLI Implementation (NEXT PRIORITY)
The CLI has 6 registered command stubs but no real implementations. The `build_orchestrator()` bootstrap function exists but the command bodies all raise `NotImplementedError`. This is the next deliverable — wire the Coordinator into the typer CLI with rich output.

Estimated: ~400 LOC, ~20 test functions.

See: `docs/subagent/D_CLI.md`

### Round 3: Integration & E2E (AFTER CLI)
- Migrate coordinator tests from `FakeExchange` to proper `MockExchange`
- Write mock E2E tests covering all 4 terminal states
- Real testnet E2E tests (Binance + Hyperliquid, small $20 orders)
- `list_markets()` real implementations (currently return `[]` stubs)
- Ruff lint clean-up

See: `docs/subagent/E_MERGE.md`

### Stage 4: Perp Support (Post-MVP)
- Leverage setting (`set_leverage`)
- Margin checks (free margin, not just balance)
- Funding rate fetching and display
- `reduce_only` reverse orders in Reconciler

### Stage 5: Production Hardening (Post-MVP)
- Crash recovery validation (chaos tests)
- Structured logging (JSON)
- Metrics hooks (Prometheus-ready interface)
- Agent SDK integration point (`src/cli/agent_api.py`)

### Known Gaps

| Gap | Impact | Fix |
|---|---|---|
| CLI commands are stubs | Can't use oneFill from command line yet | Round 2 |
| `CCXTExchange.list_markets()` returns `[]` | InstrumentRegistry can't load real instruments via CCXTExchange | Round 3 |
| `LighterExchange` removed | No DEX support for MVP | Low priority |
| 23 ruff warnings remain (bare except, old typing imports) | Cosmetic | Round 3 cleanup |
| Legacy `VolumeEngine` still references Lighter in comments | Harmless | Already noted |
| No CI pipeline | Manual test runs only | Not in MVP scope |

## File Inventory

```
src/
  market/         7 files   (asset, instrument, quote, registry, quote_fetcher, mock_backend, __init__)
  coordinator/    9 files   (intent, plan, planner, validator, executor, reconciler, orchestrator, state_machine, __init__)
  persistence/    3 files   (store, schema, __init__)
  cli/            2 files   (main, bootstrap)
  core/           4 files   (base_exchange, exchange_factory, volume_engine, arbitrage_engine)
  exchanges/      1 file    (ccxt_exchange)
  strategies/     2 files   (hedge_volume, spread_arbitrage)
  utils/          4 files   (data_processor, logger, network_manager, log_utils)
  main.py

tests/
  market/         7 files   (72 tests)
  coordinator/   10 files   (82 tests)
  persistence/    7 files   (43 tests)
  cli/            2 files   (17 tests)
  unit/exchanges/ 2 files   (test_binance, test_hyperliquid)
  unit/           1 file    (test_base_exchange)
  conftest.py

docs/
  PRD.md, REFACTOR_PLAN.md, STATUS.md, subagent/
config/
  exchanges.yaml, secrets.example.yaml, volume_farming.yaml
```

## Quick Commands

```bash
uv run pytest -m "not network"        # 218 core tests (fast, no network)
uv run pytest -m network              # 4 exchange tests (requires secrets.yaml)
uv run pytest                         # all 222 tests
uv run python -m src.cli.main --help  # CLI stubs
uv run python -m src.main --help      # legacy bot
```
