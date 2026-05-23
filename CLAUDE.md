# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product

**oneFill** — a multi-venue coordinated order execution engine.

A user submits a single CLI command (e.g. "buy $1000 of BTC across Binance and Hyperliquid, 50/50 split"). The system fans out orders to all venues in parallel within milliseconds, and guarantees a **coordinated final state**: either every leg fills, or partial fills get auto-compensated (reverse orders) to bring net exposure close to zero, or the system enters `NEEDS_MANUAL` and blocks further orders.

The product solves a problem human traders have: **manually placing the same order on 3 venues takes 30+ seconds, during which prices move and partial failures leave you with unwanted directional exposure**. oneFill compresses the time window and handles the failure cases.

**oneFill is an execution tool, not a strategy tool.** It does not decide *whether* to trade or *how much* — the user/Agent does that. It executes the user's already-decided intent.

**Phase 1 (current):** CLI tool, hand-driven.
**Phase 2 (future):** Wrap the CLI / Python API as tools for an **Anthropic Claude Agent SDK** agent, so users can express intent in natural language. (Built with the official SDK — never with leaked Claude Code source.)

See `docs/PRD.md` for full product spec. See `docs/REFACTOR_PLAN.md` for the implementation plan that gets us from the current legacy codebase to oneFill.

## Repository status

The repository is in transition:

- **Legacy code** (`src/core/volume_engine.py`, `src/core/arbitrage_engine.py`, `src/strategies/*`) is the previous incarnation: an autonomous volume-farming / arbitrage-monitoring bot. It still runs, exposed through `python -m src.main --mode volume|arbitrage|both`. It will be kept working in parallel during the refactor, then phased out once oneFill reaches feature parity for the use cases that overlap.
- **New code** (`src/coordinator/`, `src/cli/`, `src/persistence/`, `src/market/`) implements oneFill. See REFACTOR_PLAN.md for what's built when.
- **Shared lower layer** (`src/core/base_exchange.py`, `src/exchanges/*`) is reused by both. Treat these as stable; touch with care.

## Commands

### Setup
```bash
# Install uv first (https://docs.astral.sh/uv/) if not already installed
uv sync --extra dev
cp config/secrets.example.yaml config/secrets.yaml
# Edit config/secrets.yaml with your API keys/private keys
```

### Run oneFill (new)
```bash
# Preview a coordinated order without sending it
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5

# Execute it
uv run onefill order \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage 0.3%

# Query / list / cancel / recover
uv run onefill query <intent-id>
uv run onefill list --status NEEDS_MANUAL
uv run onefill recover
uv run onefill venues
```

### Run legacy bot (volume farming / arbitrage monitoring)
```bash
uv run python -m src.main --mode volume --network testnet
uv run python -m src.main --mode arbitrage --network testnet
uv run python -m src.main --mode both --network testnet
# Ctrl+C → graceful shutdown (closes all open hedge positions)
```

### Tests
```bash
uv run pytest                                  # all
uv run pytest tests/unit -vv                   # unit only
uv run pytest -m "not network and not slow"    # skip live network tests
uv run pytest tests/coordinator                 # oneFill coordinator only
```

### Lint / Format
```bash
uv run ruff check .          # show issues
uv run ruff check --fix .    # safe auto-fixes
uv run ruff format .         # apply formatting
```

A global Stop hook also runs ruff on modified .py files after each Claude turn.

### Dependency management
```bash
uv add <package>             # runtime dep
uv add --dev <package>       # dev dep
uv sync                      # reinstall from lockfile
uv lock --upgrade            # bump deps
```

## Architecture

### High-level layout

```
┌─────────────────────────────────────────────────────────────────┐
│ CLI Layer    (src/cli/)                                         │
│   onefill order / query / list / cancel / recover / venues      │
│                                                                  │
│   Legacy entry: src/main.py (TradeBot)                          │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│ Coordinator (src/coordinator/) ── NEW, oneFill core             │
│                                                                  │
│   Planner ──→ Validator ──→ Executor ──→ Reconciler             │
│        │           │            │             │                 │
│        └───────────┴────────────┴─────────────┘                 │
│                    state machine                                 │
└────┬────────────────────┬─────────────────────┬─────────────────┘
     │                    │                     │
┌────▼──────────┐  ┌──────▼─────────┐  ┌────────▼────────────────┐
│ Market layer  │  │ Exchange layer │  │ Persistence + Observability│
│ (src/market/) │  │ (src/exchanges)│  │ (src/persistence/)        │
│               │  │                │  │                            │
│ Asset         │  │ BaseExchange   │  │ SQLite (state machine)    │
│ Instrument    │  │ CCXTExchange   │  │ JSONL (append-only audit) │
│ InstrumentReg │  │ LighterExchange│  │ structured logs           │
│ Quote         │  │ Binance(new)   │  │                            │
└───────────────┘  └────────────────┘  └────────────────────────────┘
                         ▲
                         │ (reused, unchanged)
┌────────────────────────┴────────────────────────────────────────┐
│ Legacy bot (src/core/, src/strategies/) ── kept running          │
│   VolumeEngine, ArbitrageEngine, HedgeVolumeStrategy, etc.       │
└──────────────────────────────────────────────────────────────────┘
```

### Three layers that matter most

#### 1. Market layer (`src/market/`) — NEW

Handles all per-venue / per-quote / per-product differences. Three core concepts:

- **`Asset`** — user-facing handle for "BTC", "ETH", etc. Not bound to any venue or quote.
- **`Instrument`** — the system's minimum tradable unit, uniquely identified by `(venue, market_type, base, quote)`. So `BTC/USDT` spot on Binance and `BTC/USDC:USDC` perp on Hyperliquid are different Instruments. Carries venue-native symbol, min/qty/price step, fee schedule, listing status, etc.
- **`InstrumentRegistry`** — loaded at startup from each venue's markets API, cached 12–24h. Answers queries like "list all BTC perp instruments across venues" or "find one BTC spot instrument on Binance preferring USDT then USDC".
- **`Quote`** — point-in-time snapshot of an Instrument: top of book, depth-aware fill estimator, fees, funding rate (perp), open interest. Planner constructs these to decide what to actually send.

**Why this layer exists:** the same "BTC" can correspond to dozens of different Instruments (spot vs perp; USDT vs USDC vs USDH; Binance vs Hyperliquid). Every higher layer must treat these as different markets with different prices, depths, fees, and (for perp) funding rates. Skipping this abstraction is how you build a system that quietly trades against itself.

See PRD §4.5 for the full design.

#### 2. Coordinator (`src/coordinator/`) — NEW

Four phases, each independently testable:

| Phase | Side effects | What it does |
|---|---|---|
| **Planner** | None | Given an Intent (base + quote_preference + product + total_notional_usd + split), select one Instrument per venue, fetch Quotes, compute per-leg estimated price/slippage/fee/funding. Reject if any per-leg metric exceeds user thresholds. |
| **Validator** | None | Per venue: symbol active, account has balance, qty/price within venue rules, leverage feasible. One failure → reject the whole Intent. |
| **Executor** | **Yes — real orders** | Persist Plan to SQLite (`EXECUTING`), then `asyncio.gather` all `create_order` calls (target: < 50ms spread between request emissions). Poll fills. |
| **Reconciler** | **Yes — reverse orders** | If any leg fails or times out, send reverse market orders to flatten any leg that did fill. If reconciliation itself fails → state `NEEDS_MANUAL`, which **blocks all further Intents** until a human resolves it. |

#### 3. Persistence (`src/persistence/`) — NEW

- **SQLite** (`intents`, `legs`, `audit_events` tables) — transactional state machine, supports query/list/recover.
- **JSONL** (`logs/audit-YYYY-MM-DD.jsonl`) — append-only event log, full audit trail. SQLite can be rebuilt from JSONL if it ever gets corrupted.

**Hard rule:** every `create_order` call MUST be preceded by a persisted leg row. The Executor enforces this. This is the primary defense against "orders got sent but we have no record".

### State machine

```
PENDING → VALIDATED → EXECUTING ─┬─→ ALL_FILLED              (success)
   │          │                  │
   │          └─→ REJECTED       ├─→ PARTIAL_FILLED ─→ ROLLING_BACK ─┬─→ ROLLED_BACK     (partial; compensated)
   │                             │                                   │
   │                             └─→ EXECUTE_TIMEOUT (same path)     └─→ NEEDS_MANUAL    (compensation failed)
   │
   └─→ REJECTED (plan/validate failed; no orders sent)
```

Terminal states: `REJECTED`, `ALL_FILLED`, `ROLLED_BACK`, `NEEDS_MANUAL`.

CLI exit codes mirror these: 0=ALL_FILLED, 2=REJECTED, 3=ROLLED_BACK, 4=NEEDS_MANUAL.

### Exchange layer (shared, mostly unchanged)

All exchanges inherit from `BaseExchange` (`src/core/base_exchange.py`):
- Mainnet/testnet switching via `NetworkType` enum
- Shared `aiohttp` session
- Abstract: `connect()`, `fetch_balance()`, `fetch_orderbook()`, `create_order()`, `cancel_order()`, `fetch_order()`, `connect_websocket()`, `subscribe_orderbook()`

`ExchangeFactory.initialize_exchanges()` reads `config/exchanges.yaml`, skips `enabled: false` entries, applies `target_network`, calls `connect()`.

Two adapter kinds:
- **`type: ccxt`** → `CCXTExchange` wraps `ccxt.async_support` (Hyperliquid currently; Binance will be added in oneFill phase 3)
- **`type: native`** → `LighterExchange` uses the Lighter native Python SDK

**Adding a new venue** — see `EXCHANGE_INTEGRATION_GUIDE.md`. For oneFill, you also need to make sure the new venue is discoverable by `InstrumentRegistry` (markets API path, fee schedule source).

### Configuration

Three YAML files:
- `config/exchanges.yaml` — per-venue enable/disable, network URLs, fees, symbols. Fee rates feed `min_profit_threshold` (legacy) and Planner's estimated_fee (oneFill).
- `config/secrets.yaml` — credentials, gitignored. **Schema differs per venue**: Lighter splits credentials by network (`lighter.testnet.*` / `lighter.mainnet.*` with `wallet_address` / `api_private_key` / `api_key_index` / `account_index`); Hyperliquid uses a flat block (`walletAddress` / `privateKey`). Code loading secrets must branch on venue.
- `config/volume_farming.yaml` — legacy-only, drives `VolumeEngine`.

oneFill will read the same `exchanges.yaml` and `secrets.yaml`; no separate oneFill config file in MVP.

## Critical invariants (don't break these)

These are load-bearing properties that future Claude sessions should preserve unless explicitly told otherwise:

1. **Every `create_order` is preceded by a persisted leg row.** Executor must write to SQLite/JSONL before issuing the call. Crash-after-send must be recoverable.
2. **`NEEDS_MANUAL` blocks all subsequent Intents.** Don't add "retry" or "auto-recover from NEEDS_MANUAL" paths — escalation to a human is the design.
3. **A single Intent never mixes `spot` and `perp`.** `Intent.product` is single-valued. To do both, user submits two Intents.
4. **The Market layer (`Asset`/`Instrument`/`Quote`) is the only place that knows venue-native symbols.** Higher layers use Instrument objects; CLI uses `--base` and `--quote-preference`. Never let `BTCUSDT` leak into Coordinator code.
5. **Coordinator phases are pure-ish:** Planner and Validator have no side effects. Executor and Reconciler do. Tests rely on this — keep it.
6. **Legacy `VolumeEngine` margin safety guard.** Before every open, free margin is checked; on shortfall it retries 3× with 5-min sleep, then auto-closes the lowest-cost position. Don't bypass when modifying open-position paths.
7. **Legacy volume accounting is in USD notional**, not coin count. `daily_max_volume` / `daily_target_volume` / stats reports — all USD. (oneFill is also USD-notional; same principle, different module.)

## Pre-removal / pre-cleanup checklist

When deleting a feature, dependency, or config:

1. `grep -ri 'X' . --include="*.py" --include="*.yaml" --include="*.md" --include="*.toml"` for ALL references — code, configs, docs, lockfiles, `pyproject.toml` extras
2. Check both `pyproject.toml` and `uv.lock` for stale deps
3. Check `.gitignore` for any rules that referenced the removed path
4. After delete, run `uv run ruff check .` + `uv run pytest` to surface broken imports / tests
5. Keep `.gitignore` changes in their own commit, not bundled with feature commits

(This checklist exists because past removal sessions left orphan references that needed second-round fixes.)

## Key dependencies

- `ccxt` — async exchange connectivity. [Binance docs](https://docs.ccxt.com/#/exchanges/binance) · [Hyperliquid docs](https://docs.ccxt.com/#/exchanges/hyperliquid)
- `aiohttp` — async HTTP sessions

### Exchange-specific ccxt notes

**Binance:**
- Demo trading (testnet): call `exchange.enable_demo_trading(True)` **after** constructing the ccxt instance, **before** `load_markets()`. This swaps `urls.api` → `urls.demo` (demo-api.binance.com). ccxt 4.5.54 supports this natively.
- Demo mode does NOT support sapi/margin endpoints (ccxt internal comment at binance.py:2940). Use `fetchMarkets: ['spot']` option.
- `CCXTExchange.connect()` auto-enables demo mode when `self.name == "binance"` and `self.network_type == TESTNET`.
- Auth: HMAC (`apiKey` + `secret`). Ed25519 keys are not supported by ccxt.

**Hyperliquid:**
- Testnet: set `options['testnet'] = True`. `CCXTExchange._build_ccxt_config` handles this.
- Auth: `walletAddress` + `privateKey` (Ethereum-style hex). Optional `vaultAddress`.
- ccxt defaults to `swap` (perpetual) market type — correct for Hyperliquid.
- `pytest` / `pytest-asyncio` — `asyncio_mode = auto` set in `pyproject.toml`
- `ruff` — lint + format, configured in `pyproject.toml`

For oneFill, additional deps will be introduced in phases (Click/Typer for CLI, aiosqlite for persistence) — see REFACTOR_PLAN.md.
