# Architecture

## High-level layout

oneFill is a three-layer system:

```text
┌─────────────────────────────────────────────────────────────────┐
│ CLI Layer    (src/cli/)                                         │
│   onefill order / query / list / cancel / recover / venues      │
│   Legacy entry: src/main.py (TradeBot)                          │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│ Coordinator (src/coordinator/) — oneFill core                   │
│                                                                  │
│   Planner ──→ Validator ──→ Executor ──→ Reconciler             │
│        │           │            │             │                 │
│        └───────────┴────────────┴─────────────┘                 │
│                    state machine                                 │
│                    RiskValidator (between Validate + Execute)    │
└────┬────────────────────┬─────────────────────┬─────────────────┘
     │                    │                     │
┌────▼──────────┐  ┌──────▼─────────┐  ┌────────▼────────────────┐
│ Market layer  │  │ Exchange layer │  │ Persistence + Observability│
│ (src/market/) │  │ (src/exchanges)│  │ (src/persistence/)        │
│               │  │                │  │                            │
│ Asset         │  │ BaseExchange   │  │ SQLite (state machine)    │
│ Instrument    │  │ CCXTExchange   │  │ JSONL (append-only audit) │
│ InstrumentReg │  │                │  │ structured logs           │
│ Quote         │  │                │  │                            │
└───────────────┘  └────────────────┘  └────────────────────────────┘
```

## The three layers

### 1. Market layer (`src/market/`)

Handles all per-venue / per-quote / per-product differences. Three core concepts:

- **`Asset`** — user-facing handle for "BTC", "ETH", etc. Not bound to any venue or quote.
- **`Instrument`** — the system's minimum tradable unit, uniquely identified by `(venue, market_type, base, quote)`. So `BTC/USDT` spot on Binance and `BTC/USDC:USDC` perp on Hyperliquid are different Instruments. Carries venue-native symbol, min/qty/price step, fee schedule, listing status, etc.
- **`InstrumentRegistry`** — loaded at startup from each venue's markets API, cached 12–24h. Answers queries like "list all BTC perp instruments across venues" or "find one BTC spot instrument on Binance preferring USDT then USDC".
- **`Quote`** — point-in-time snapshot of an Instrument: top of book, depth-aware fill estimator, fees, funding rate (perp), open interest. Planner constructs these to decide what to actually send.

**Why this layer exists:** the same "BTC" can correspond to dozens of different Instruments (spot vs perp; USDT vs USDC vs USDH; Binance vs Hyperliquid). Every higher layer must treat these as different markets with different prices, depths, fees, and (for perp) funding rates. Skipping this abstraction is how you build a system that quietly trades against itself.

### 2. Coordinator (`src/coordinator/`)

Four phases, each independently testable:

| Phase | Side effects | What it does |
|---|---|---|
| **Planner** | None | Given an Intent (base + quote_preference + product + total_notional_usd + split), select one Instrument per venue, fetch Quotes, compute per-leg estimated price/slippage/fee/funding. Reject if any per-leg metric exceeds user thresholds. |
| **Validator** | None | Per venue: symbol active, account has balance, qty/price within venue rules, leverage feasible. One failure → reject the whole Intent. |
| **Executor** | **Yes — real orders** | Persist Plan to SQLite (`EXECUTING`), then `asyncio.gather` all `create_order` calls (target: < 50ms spread between request emissions). Poll fills via WebSocket with HTTP fallback. |
| **Reconciler** | **Yes — reverse orders** | If any leg fails or times out, send reverse market orders to flatten any leg that did fill. If reconciliation itself fails → state `ROLLED_BACK_FAILED`, which **blocks all further Intents** until a human resolves it. |

A `RiskValidator` runs between Validate and Execute, checking configurable guardrails: max notional per intent, daily loss limit, venue exposure cap, and rate limiting.

### 3. Persistence (`src/persistence/`)

- **SQLite** (`intents`, `legs`, `audit_events` tables) — transactional state machine, supports query/list/recover.
- **JSONL** (`logs/audit-YYYY-MM-DD.jsonl`) — append-only event log, full audit trail. SQLite can be rebuilt from JSONL if it ever gets corrupted.

**Hard rule:** every `create_order` call MUST be preceded by a persisted leg row. The Executor enforces this. This is the primary defense against "orders got sent but we have no record".

## Exchange layer (shared)

All exchanges inherit from `BaseExchange` (`src/core/base_exchange.py`):

- Mainnet/testnet switching via `NetworkType` enum
- Shared `aiohttp` session
- Abstract methods for order lifecycle: `connect()`, `fetch_balance()`, `fetch_orderbook()`, `create_order()`, `cancel_order()`, `fetch_order()`

`ExchangeFactory.initialize_exchanges()` reads `config/exchanges.yaml`, skips `enabled: false` entries, applies `target_network`, calls `connect()`.

Two adapter kinds:
- **`type: ccxt`** → `CCXTExchange` wraps `ccxt.async_support` (Binance + Hyperliquid)
- **`type: native`** → `LighterExchange` uses the Lighter native Python SDK

## Repository status

The repository is in transition:

- **Legacy code** (`src/core/volume_engine.py`, `src/core/arbitrage_engine.py`, `src/strategies/*`) is the previous incarnation: an autonomous volume-farming / arbitrage-monitoring bot. It still runs through `python -m src.main --mode volume|arbitrage|both`. It will be kept working in parallel during the refactor, then phased out once oneFill reaches feature parity for overlapping use cases.
- **New code** (`src/coordinator/`, `src/cli/`, `src/persistence/`, `src/market/`) implements oneFill.
- **Shared lower layer** (`src/core/base_exchange.py`, `src/exchanges/*`) is reused by both. Treat these as stable; touch with care.

## Shared vs Legacy/New boundary

| Layer | Module(s) | Shared? | Who Uses It |
|---|---|---|---|
| Exchange abstraction | `src/core/base_exchange.py` | **Shared** | Both legacy and new |
| Exchange factory | `src/core/exchange_factory.py` | **Shared** | Both |
| CCXT adapter | `src/exchanges/ccxt_exchange.py` | **Shared** | Both |
| Volume engine | `src/core/volume_engine.py` | **Legacy only** | TradeBot |
| Arbitrage engine | `src/core/arbitrage_engine.py` | **Legacy only** | TradeBot |
| Spread/hedge strategies | `src/strategies/*.py` | **Legacy only** | TradeBot |
| Funding arb strategy | `src/strategy/funding_arb/*.py` | **New only** | oneFill `arb` commands |
| Market layer | `src/market/*.py` | **New only** | oneFill Orchestrator |
| Coordinator pipeline | `src/coordinator/*.py` | **New only** | oneFill Orchestrator |
| Persistence | `src/persistence/*.py` | **New only** | oneFill |
| CLI | `src/cli/*.py` | **New only** | oneFill CLI |
