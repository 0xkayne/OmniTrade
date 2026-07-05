# oneFill Refactor Plan

> How we get from the current legacy codebase to oneFill MVP.
> Status: Draft v0.1 · Author: kayne · Date: 2026-05-23
>
> Read alongside `docs/PRD.md` (product spec) and `CLAUDE.md` (architecture & invariants).

---

## Guiding principles

1. **Don't break the legacy bot.** It still runs (and may be the only thing actually trading) while we build oneFill. Touch `src/core/volume_engine.py` / `src/core/arbitrage_engine.py` / `src/strategies/*` only via additive changes (config gates), never by editing semantics.
2. **Every stage ends in a working state.** No 3-week branches that nobody can merge. After each stage's commits, `uv run pytest` passes and the legacy bot still launches.
3. **Mockable from day one.** Coordinator stages take `BaseExchange` instances as constructor args, not import them. Tests inject a `MockExchange` that implements the same protocol. No "let me just `monkeypatch` ccxt" tests.
4. **Real venue connection comes late.** Stages 1–2 use only the mock exchange. Stage 3 is the first time we touch real Binance / Hyperliquid testnet. By then the Coordinator is well-tested in isolation.
5. **Persistence before parallelism, parallelism before resilience.** Don't try to write the Reconciler before the Executor reliably persists fills. Don't optimize for concurrency before the linear path works.
6. **One PR per stage.** Each stage is mergeable on its own. Stages don't span PRs.

---

## Stage overview

| Stage | Deliverable | LOC est. | Touches legacy? |
|---|---|---|---|
| **0. Skeleton** | New package layout, deps, CLI entry that prints help. Nothing functional yet. | ~200 | No |
| **1. Market layer** | `Asset` / `Instrument` / `InstrumentRegistry` / `Quote` with mock backends + tests | ~700 | No |
| **2. Coordinator (mock-only)** | Planner / Validator / Executor / Reconciler with `MockExchange`; state machine in SQLite + JSONL | ~1500 | No |
| **3. CLI + real venues (testnet)** | `onefill order/query/list/recover/venues` wired to Binance + Hyperliquid testnet | ~600 | Adds Binance config |
| **4. Perp support** | Leverage, margin, funding-rate fetching, perp-specific Planner extensions | ~500 | No |
| **5. Production hardening** | Crash-recovery validation, structured logging, metrics hooks, Agent SDK integration point | ~400 | No | ✅ Complete (Jul 2026) |
| **6. Funding rate arbitrage** | Funding rate scanner, cross-venue pair matching, spread detection, hedged position lifecycle, CLI `onefill arb` | ~1776 | No | 📋 Planned |

Approx total: ~4000 new LOC, ~6–8 commits, ~3–5 weeks if working solo half-time.

After Stage 3, oneFill is usable hand-driven on testnet. Stage 4 adds perp. Stage 5 makes it production-grade and Agent-ready.

---

## Stage 0 — Skeleton

**Goal:** Create the new package layout so everyone can `import src.coordinator` etc. without errors. CLI is registered as a script and runs (just prints help). Zero behavior.

### Files to create

```
src/
  coordinator/__init__.py
  market/__init__.py
  cli/__init__.py
  cli/main.py                 # Click/Typer-based CLI root
  persistence/__init__.py

tests/
  coordinator/__init__.py
  market/__init__.py
  cli/__init__.py
  persistence/__init__.py

docs/
  PRD.md                      # already exists
  REFACTOR_PLAN.md            # this file
```

### Deps to add

```bash
uv add typer rich             # CLI + nice output
uv add aiosqlite              # async SQLite for persistence
uv add --dev pytest-aiohttp   # for async test helpers
```

### pyproject.toml change

Add a CLI entry point:
```toml
[project.scripts]
onefill = "src.cli.main:app"
```

### Acceptance

- `uv sync --extra dev` succeeds
- `uv run onefill --help` shows top-level command list with `order`, `query`, `list`, `cancel`, `recover`, `venues` as registered commands (each is a stub that prints "not implemented" and exits 1)
- `uv run python -m src.main --help` (legacy) still works
- `uv run pytest` still passes (we add no tests in this stage; just verify nothing broke)
- Commit message: `stage 0: oneFill package skeleton + CLI stubs`

### Out of scope

No business logic. Don't create `Coordinator`, `Planner`, etc. classes yet. Only `__init__.py` files and the CLI stub.

---

## Stage 1 — Market layer

**Goal:** Implement Asset / Instrument / InstrumentRegistry / Quote as data structures + a `MockMarketBackend` for testing. Real venue integration deferred to Stage 3.

### What gets built

#### `src/market/asset.py`
```python
@dataclass(frozen=True)
class Asset:
    symbol: str           # "BTC", "USDT"
    kind: str = "crypto"
```

Frozen so it's hashable for set/dict use.

#### `src/market/instrument.py`
```python
@dataclass(frozen=True)
class Instrument:
    venue: str
    market_type: Literal["spot", "perp", "futures"]
    base: Asset
    quote: Asset
    venue_symbol: str             # "BTCUSDT" on Binance
    contract_size: float = 1.0
    min_qty: float
    qty_step: float
    price_step: float
    taker_fee_rate: float
    maker_fee_rate: float
    is_inverse: bool = False
    listing_status: str = "trading"

    def key(self) -> tuple:
        return (self.venue, self.market_type, self.base.symbol, self.quote.symbol)
```

Methods: `round_qty(amount)` (snap to `qty_step`), `round_price(price)`.

#### `src/market/registry.py`
```python
class InstrumentRegistry:
    def __init__(self, exchanges: dict[str, BaseExchange], ttl_hours: int = 24): ...

    async def load_all(self) -> None: ...     # called once at startup
    async def reload(self, venue: str) -> None: ...

    def list_instruments(
        self,
        base: str | None = None,
        market_type: str | None = None,
        venue: str | None = None,
    ) -> list[Instrument]: ...

    def find_one(
        self,
        base: str,
        venue: str,
        market_type: str,
        quote_preference: list[str],
    ) -> Instrument | None: ...

    def is_stale(self) -> bool: ...
```

Loads from each `BaseExchange` via a new abstract method `BaseExchange.list_markets()` (added in this stage — see below).

#### `src/market/quote.py`
```python
@dataclass
class EstimatedFill:
    avg_price: float
    slippage_pct: float
    depth_consumed_levels: int

@dataclass
class Quote:
    instrument: Instrument
    fetched_at: datetime
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    mid_price: float
    taker_fee_rate: float
    maker_fee_rate: float
    funding_rate: float | None = None
    next_funding_time: datetime | None = None
    open_interest: float | None = None

    _bids: list[tuple[float, float]] = field(default_factory=list)   # full depth
    _asks: list[tuple[float, float]] = field(default_factory=list)

    def estimate_fill(self, amount_base: float, side: Literal["buy", "sell"]) -> EstimatedFill: ...
```

`estimate_fill` walks the relevant side of the book, accumulates fills, returns weighted avg price + slippage vs mid.

#### `src/market/quote_fetcher.py`
```python
class QuoteFetcher:
    def __init__(self, exchanges: dict[str, BaseExchange]): ...

    async def fetch(self, instrument: Instrument, depth: int = 20) -> Quote: ...
    async def fetch_many(self, instruments: list[Instrument]) -> list[Quote]: ...
```

`fetch_many` uses `asyncio.gather` so Planner can get N venue quotes concurrently.

#### `src/market/mock_backend.py` (test-only helper)

A `MockExchange` implementing `BaseExchange` that returns canned orderbooks and markets. Lives in `src/market/` for now (not under `tests/`) because the Coordinator stage will also import it.

### Changes to existing `BaseExchange`

Add one abstract method:
```python
async def list_markets(self) -> list[Instrument]: ...
```

Both `CCXTExchange` and `LighterExchange` get implementations in this stage. The implementations don't need to be fully fee-aware yet — pull `taker_fee_rate`/`maker_fee_rate` from `config/exchanges.yaml` since that's where they live today.

### Tests (`tests/market/`)

- `test_asset.py` — hashability, equality
- `test_instrument.py` — `round_qty`, `round_price`, key uniqueness
- `test_registry.py` — `list_instruments` filters; `find_one` preference matching; cache TTL; stale detection
- `test_quote.py` — `estimate_fill` walks depth correctly, slippage calculation
- `test_quote_fetcher.py` — concurrent fetch_many returns in same order as input, handles per-venue failures

All tests use `MockExchange`. No network.

### Acceptance

- `uv run pytest tests/market -v` → all green, >85% line coverage in `src/market/`
- `uv run python -c "from src.market import InstrumentRegistry; print('ok')"` works
- Manual smoke: write a 10-line script that builds `InstrumentRegistry({"mock": MockExchange()})` and prints `find_one("BTC", "mock", "spot", ["USDT"])`. Add this script to `examples/` or just paste in commit message.
- Legacy bot still launches
- Commit message: `stage 1: market layer (Asset/Instrument/Registry/Quote) + mocks + tests`

### Out of scope

- Real Binance / Hyperliquid implementations of `list_markets` beyond returning what `ccxt.load_markets()` already gives. Refining is Stage 3.
- Funding-rate fetching for perp — Stage 4.
- `Quote.estimate_fill` for limit orders — only market-order estimation in Stage 1.

---

## Stage 2 — Coordinator (mock-only)

**Goal:** Build the full Plan → Validate → Execute → Reconcile pipeline against `MockExchange`. Persist state to SQLite/JSONL. No CLI, no real venues yet. Driven from tests.

### What gets built

#### `src/coordinator/intent.py`
```python
@dataclass
class Intent:
    intent_id: str               # uuid7 or ulid
    base: str
    quote_preference: list[str]
    product: Literal["spot", "perp"]
    side: Literal["buy", "sell"]
    type: Literal["market", "limit"]
    total_notional_usd: float
    split: dict[str, float]      # {"binance": 0.5, "hyperliquid": 0.5}
    leverage: int = 1
    limit_price: float | None = None
    max_slippage_pct: float | None = None
    max_fee_usd: float | None = None
    max_funding_rate_pct: float | None = None
    execute_timeout_seconds: int = 30
    created_at: datetime
```

Validation in `__post_init__`: split sums to 1.0 (within 1e-6 tolerance); product is single-valued; if `type==limit` then `limit_price` required; if `product==spot` then `leverage==1`.

#### `src/coordinator/plan.py`
```python
@dataclass
class PlannedLeg:
    venue: str
    instrument: Instrument
    quote_matched: str
    planned_notional_usd: float
    planned_qty_base: float
    estimated_fill: EstimatedFill
    estimated_fee_usd: float
    funding_rate: float | None
    next_funding_time: datetime | None
    selection_log: list[dict]    # candidates considered + skip reasons

@dataclass
class Plan:
    intent: Intent
    legs: list[PlannedLeg]
    rejected_venues: list[tuple[str, str]]  # (venue, reason)
    aggregate_estimated_avg_price: float
    aggregate_estimated_fee_usd: float
    is_acceptable: bool          # threshold checks
    rejection_reasons: list[str]
```

#### `src/coordinator/planner.py`
```python
class Planner:
    def __init__(self, registry: InstrumentRegistry, quote_fetcher: QuoteFetcher): ...

    async def plan(self, intent: Intent) -> Plan: ...
```

Implements the algorithm from PRD §4.5.4: for each venue → list candidates → match preference → fetch Quote → compute estimates → aggregate.

#### `src/coordinator/validator.py`
```python
class Validator:
    def __init__(self, exchanges: dict[str, BaseExchange]): ...

    async def validate(self, plan: Plan) -> ValidationResult: ...
```

Per-venue parallel: balance check, qty rules, leverage feasibility. Returns list of failures with venue + reason.

#### `src/coordinator/executor.py`
```python
class Executor:
    def __init__(
        self,
        exchanges: dict[str, BaseExchange],
        store: PersistenceStore,
        poll_interval_ms: int = 500,
    ): ...

    async def execute(self, plan: Plan, timeout_seconds: int) -> ExecutionResult: ...
```

Pseudocode:
```
1. store.update_intent_status(intent_id, EXECUTING)
2. for leg in plan.legs:
     store.create_leg_row(leg, status=PENDING_SEND)
3. tasks = [exchange.create_order(...) for leg]
4. results = await asyncio.gather(*tasks, return_exceptions=True)
5. for (leg, result) in zip:
     store.update_leg(leg_id, status=SENT or REJECTED, order_id=..., error=...)
6. poll fills until all leg terminal OR timeout
7. compute ExecutionResult
```

#### `src/coordinator/reconciler.py`
```python
class Reconciler:
    def __init__(self, exchanges: dict[str, BaseExchange], store: PersistenceStore): ...

    async def reconcile(self, execution_result: ExecutionResult) -> ReconciliationResult: ...
```

For each filled (or partially filled) leg → send reverse market order sized to the filled amount → poll. If any reverse order fails → return `NEEDS_MANUAL`, store sets intent to `NEEDS_MANUAL`, raises `BlockingError` to any subsequent `execute()` call until cleared.

#### `src/coordinator/state_machine.py`

The transitions defined in PRD §7.1, enforced as code (rejects invalid transitions).

#### `src/persistence/store.py`
```python
class PersistenceStore:
    def __init__(self, sqlite_path: Path, jsonl_dir: Path): ...

    async def initialize(self) -> None: ...   # create tables if needed

    async def create_intent(self, intent: Intent) -> None: ...
    async def update_intent_status(self, intent_id: str, status: str) -> None: ...
    async def create_leg(self, leg: PlannedLeg, intent_id: str) -> str: ...   # returns leg_id
    async def update_leg(self, leg_id: str, **fields) -> None: ...

    async def append_event(self, intent_id: str, event_type: str, payload: dict) -> None: ...
        # writes to BOTH SQLite audit_events AND today's JSONL

    async def list_intents(self, status: str | None = None) -> list[IntentRow]: ...
    async def get_intent(self, intent_id: str) -> IntentRow | None: ...
    async def is_blocked_by_needs_manual(self) -> bool: ...
```

SQLite schema per PRD §7.2 + the additions from §4.5.8. Migration is one `CREATE TABLE IF NOT EXISTS` block for MVP — no Alembic.

#### `src/coordinator/orchestrator.py`

The thing that ties Planner → Validator → Executor → Reconciler into a single `submit(intent)` call. Returns final status.

```python
class Orchestrator:
    async def submit(self, intent: Intent) -> IntentTerminalState: ...
```

This is what the CLI (Stage 3) and the Agent SDK (Phase 2) will call.

### Tests (`tests/coordinator/`)

This is the heaviest test stage. Use `MockExchange` for all venue behavior — configure it to simulate fills, partial fills, timeouts, errors.

- `test_planner.py` — quote_preference matching; rejection on slippage threshold; aggregate calculation
- `test_validator.py` — balance failure rejects; qty step rounding; multi-failure listing
- `test_executor.py` — concurrent send; persists before sending; happy path → ALL_FILLED
- `test_executor_partial.py` — inject failure on leg 2 → state PARTIAL_FILLED, triggers reconciler
- `test_reconciler.py` — reverse order success → ROLLED_BACK; reverse order fail → NEEDS_MANUAL
- `test_state_machine.py` — invalid transitions rejected
- `test_persistence_store.py` — round-trip, JSONL append, NEEDS_MANUAL blocking
- `test_orchestrator_e2e.py` — full pipeline with mock for all 4 terminal states

**Critical test:** `test_executor_crash_recovery.py` — start an execution, kill the process mid-execute (via `asyncio.CancelledError`), restart, verify SQLite has the partially executed intent and `recover` can see it.

### Acceptance

- `uv run pytest tests/coordinator -v` → all green, >80% coverage in `src/coordinator/`, >90% in `src/persistence/`
- Hand-written script in `examples/coordinator_mock_demo.py` that submits 5 intents (one of each terminal state) and prints results — included in commit
- Legacy bot still launches
- Commit message: `stage 2: Coordinator pipeline (plan/validate/execute/reconcile) with mocks`

### Out of scope

- Real venue connections
- CLI wiring
- Limit-order Executor logic (only market orders in Stage 2; limit comes in Stage 3 alongside CLI)

---

## Stage 3 — CLI + real venues (testnet)

**Goal:** Wire the Coordinator to real Binance + Hyperliquid testnet. Implement the full CLI.

### What gets built

#### `src/cli/main.py` + `src/cli/commands/`

Each command file implements one subcommand (`order.py`, `query.py`, `list.py`, `cancel.py`, `recover.py`, `venues.py`).

- `order.py` — parses flags into `Intent`, builds `Orchestrator`, calls `submit`, formats output (rich tables for human, JSON for `--json`). Implements `--dry-run` (Plan + Validate only) and `--yes` (skip confirmation prompt).
- `query.py` — lookup by intent_id, render
- `list.py` — table of recent intents with status filter
- `cancel.py` — cancel a non-terminal intent (only if no orders sent yet, or only PENDING legs)
- `recover.py` — interactive walkthrough of NEEDS_MANUAL intents
- `venues.py` — list configured venues + connection status

#### `src/cli/bootstrap.py`

Centralizes the Orchestrator construction (load configs, create ExchangeFactory, build InstrumentRegistry, etc.). One function returns a ready-to-use Orchestrator. Used by every command.

#### Binance integration

**Decision: use ccxt, not binance-connector-python.**

Reasons:
- `CCXTExchange` already abstracts venue differences (auth, symbol normalization, endpoint routing). Binance becomes just another `type: ccxt` entry in `exchanges.yaml` — near-zero code change.
- ccxt handles the spot-vs-futures endpoint split (`defaultType=spot/swap`), signature, rate limiting, and error-code normalization. We'd have to rebuild all of that with the raw SDK.
- `ccxt.load_markets()` is exactly what `InstrumentRegistry` needs to populate venue-native symbols, precision rules, and fee schedules.

**API key:** create at `demo.binance.com` (the new unified demo environment, launched 2025). One key with both "Enable Spot & Margin" and "Enable Futures" checked covers all products. This replaces the old split testnet (separate `testnet.binance.vision` for spot, `testnet.binancefuture.com` for futures).

**References:**
- ccxt: https://github.com/ccxt/ccxt (unified exchange API, 100+ venues; we use it for Binance + Hyperliquid)
- Binance Spot Demo API: https://developers.binance.com/docs/binance-spot-api-docs/demo-mode/general-info
- Binance Derivatives API (testnet endpoints): https://developers.binance.com/docs/derivatives/
- Demo API key management: https://demo.binance.com/en/my/settings/api-management

**Config:**
- Add Binance entry to `config/exchanges.yaml` with `type: ccxt`, `enabled: true`, testnet URLs, fee rates
- Secrets: `apiKey` + `secret` (HMAC) or `apiKey` + `privateKey` (Ed25519, recommended). ccxt accepts both.
- Verify `CCXTExchange._build_ccxt_config` works for Binance auth (the existing `else` branch for `apiKey` + `secret` already handles it; Ed25519 may need a small branch to pass `privateKey` instead of `secret`)

**Open question — ccxt sandbox mode vs new demo URL:**
ccxt's `exchange.set_sandbox_mode(True)` currently points to the old testnet domains (`testnet.binance.vision` / `testnet.binancefuture.com`). It's unclear whether API keys created at `demo.binance.com` work against those old endpoints. On first `connect()`, verify this. If they don't, hardcode the new demo REST/WS URLs in `exchanges.yaml` via `urls.api` / `urls.ws` overrides — ccxt supports this on the options dict.

- Test connect on Binance demo (spot + futures) and verify `load_markets()` returns symbols for both product types

#### Hyperliquid integration

Already works for legacy bot. Verify it works with the new `BaseExchange.list_markets()` method added in Stage 1.

#### Symbol normalization

This is where the real-world mess hits. Different venues format symbols differently:
- Binance spot: `BTCUSDT`
- Binance perp: `BTCUSDT` (futures) — same! but resolved via `defaultType=swap`
- Hyperliquid perp: `BTC/USDC:USDC` in ccxt's canonical form

`Instrument.venue_symbol` already holds the per-venue native form. The work in Stage 3 is making sure `list_markets()` for each venue returns Instruments with correct `venue_symbol` so Executor's `create_order(venue_symbol, ...)` call works.

### Tests (`tests/cli/`, `tests/integration/`)

- `tests/cli/test_order_command.py` — argument parsing, validation, JSON output schema
- `tests/cli/test_recover_command.py`
- `tests/integration/test_binance_testnet.py` — REAL Binance testnet, marked `@pytest.mark.network`, skipped by default
- `tests/integration/test_hyperliquid_testnet.py` — REAL Hyperliquid testnet, same
- `tests/integration/test_coordinated_dry_run.py` — submit intent with `--dry-run` against real testnets, verify Plan looks sane (no orders actually sent)

### Acceptance

- `uv run onefill --help` shows all 6 commands
- `uv run onefill venues` shows binance + hyperliquid as connected (after secrets configured)
- `uv run onefill order --dry-run --base BTC --quote-preference USDT,USDC --product spot --side buy --type market --total-notional-usd 100 --split binance=0.5,hyperliquid=0.5` produces a clean Plan output
- **Manual milestone** (not auto-tested): submit a real $20 testnet order across Binance + Hyperliquid → returns ALL_FILLED → SQLite has the record → exit code 0
- **Manual milestone**: inject a Hyperliquid testnet outage (set wrong API URL) → submit order → returns ROLLED_BACK with Binance leg compensated → exit code 3
- Legacy bot still launches and runs a volume cycle
- Commit message: `stage 3: oneFill CLI wired to Binance + Hyperliquid testnet`

### Out of scope

- Perp support beyond what existing CCXT/Hyperliquid SDK gives — Stage 4 polishes this
- Lighter (DEX) integration — defer to post-MVP
- Web UI — out of MVP entirely

---

## Stage 4 — Perp support

**Goal:** Make perp the equal of spot. Specifically: leverage setting, margin checks, funding rate estimation, perp-specific Plan output.

### What changes

#### Planner

- For perp Intents, fetch funding rate + next funding time and include in PlannedLeg
- Apply `--max-funding-rate` threshold
- Margin estimation: required margin = notional / leverage (per venue's actual formula)

#### Validator

- Free margin (not just balance) ≥ required margin
- Leverage feasibility (`leverage <= venue.max_leverage_for_instrument`)
- For Hyperliquid: account state checks

#### Executor

- Set leverage before order (per-instrument, idempotent — Hyperliquid requires this; Binance allows it via separate API)
- Track filled position size as well as cost

#### Reconciler

- For perp, reverse order is `reduce_only=True` to ensure it closes existing position rather than opening opposite-side new one

#### New `BaseExchange` methods

```python
async def set_leverage(self, instrument: Instrument, leverage: int) -> None: ...
async def fetch_funding_rate(self, instrument: Instrument) -> FundingInfo: ...
async def fetch_free_margin(self, quote_asset: Asset) -> float: ...
```

### Tests

- `tests/coordinator/test_planner_perp.py` — funding threshold rejection
- `tests/coordinator/test_validator_perp.py` — margin check
- `tests/coordinator/test_reconciler_perp.py` — reverse with reduce_only
- `tests/integration/test_hyperliquid_perp_testnet.py` — REAL testnet perp open + close
- `tests/integration/test_binance_perp_testnet.py` — same

### Acceptance

- `uv run onefill order --product perp --leverage 3 ...` works on testnet for Binance + Hyperliquid
- Funding rate appears in Plan output for perp Intents
- Reverse orders in Reconciler use `reduce_only`
- Commit message: `stage 4: perp support (leverage, margin, funding rate)`

---

## Stage 5 — Production hardening

**Goal:** Make oneFill ready for hand-driven production use, and prepare the integration surface for Phase 2 (Agent SDK).

### What gets built

#### Crash recovery validation

Build a `tools/chaos_test.py` script:
- Submit 10 intents in sequence
- Randomly `kill -9` the process during one of them
- Restart, run `onefill recover`
- Verify state machine + SQLite + JSONL are consistent
- Loop 100 times in CI (marked `slow`)

#### Structured logging

Replace existing `print(...)` and ad-hoc logging in new code with `structlog` (or stdlib `logging.LoggerAdapter` with JSON formatter). Each log line includes `intent_id`, `leg_id`, `phase`. Output: stdout JSON in production, pretty for dev.

#### Metrics hooks

Add a thin metrics emitter interface:
```python
class MetricsEmitter(Protocol):
    def increment(self, name: str, tags: dict = None): ...
    def histogram(self, name: str, value: float, tags: dict = None): ...
```

Default impl is a no-op. Real impl (Prometheus exporter) is a separate package, post-MVP.

Embed emitter calls at:
- Intent submitted / state transitions
- Per-venue API latency
- Per-venue error rates
- Plan-to-Fill latency

#### Agent SDK integration point

Add `src/cli/agent_api.py` — a thin Python-callable wrapper:
```python
async def submit_intent_from_dict(intent_dict: dict) -> dict:
    """Programmatic equivalent of `onefill order`. Returns JSON-friendly result dict."""
```

This is what Phase 2 will register as a Claude Agent SDK tool. Stage 5 only ships the function + a few tests; the actual Agent comes later.

#### Documentation

- README.md gets a "Quick start" updated to oneFill
- VOLUME_FARMING_GUIDE.md gets renamed/relocated to `docs/legacy/` since it documents the legacy bot
- `docs/AGENT_INTEGRATION.md` — half-page note on how to import `submit_intent_from_dict` into an Agent SDK project. No full Agent design yet.

### Acceptance

- Chaos test runs 100 iterations green (in `pytest -m slow`)
- All new-code log output is JSON-structured
- `submit_intent_from_dict` has unit tests
- README + CLAUDE.md reflect Stage 5 state
- Commit message: `stage 5: production hardening + Agent SDK integration point`

---

## What happens after Stage 5

oneFill is feature-complete for MVP. From here the natural directions are:

1. **Phase 2: Claude Agent SDK product** — separate repo or `agent/` subdir, depends on oneFill as a library. PRD for this is written after Stage 5 lands.
2. **More venues** — Lighter (already has adapter), OKX, dYdX. Each is a few hundred LOC of adapter + Instrument list + tests.
3. **Smart split** — let the system pick the split ratio rather than the user. This is a real research problem (depth/slippage optimization) and deserves its own design doc.
4. **Limit-order improvements** — order-book-aware limit placement, post-only, IOC/FOK.
5. **Deprecate legacy bot** — once oneFill can cover the user's hand-driven workflow, retire `VolumeEngine` / `ArbitrageEngine` (move to `legacy/` dir or separate branch).

---

## Sequencing notes

**Why Market layer (Stage 1) before Coordinator (Stage 2)?**
Because Coordinator's Planner needs to *query* Instruments by base+preference. If we wrote Planner first, we'd have to mock the whole Instrument-resolution path inside the Planner code, which would tightly couple them. Splitting cleanly here means we can test Planner with a tiny in-memory `InstrumentRegistry` fixture in Stage 2.

**Why mock-only through Stage 2?**
Real venue calls are slow, flaky, and rate-limited. Iterating on Coordinator design while waiting 3 seconds per testnet round-trip would burn weeks. By Stage 2's end, we know the pipeline logic is correct; Stage 3 only adds "does the wire-format actually match what the venue expects?"

**Why CLI in Stage 3 instead of Stage 2?**
CLI without real venues = printing fake results, which doesn't actually exercise anything new. Easier to write CLI once we have real fills to display.

**Why perp in Stage 4 instead of integrated throughout?**
Perp adds 5+ extra concerns (leverage, margin, funding, reduce_only, position sizing). Bundling them into Stage 2/3 would balloon those stages. Perp is independent enough to be its own stage, and waiting until 3 means we already have a working spot path to compare against.

---

## Risks specific to the refactor

| Risk | Mitigation |
|---|---|
| Stage 1 underestimates how messy `ccxt.load_markets()` output is across venues (different field names, missing fields, edge cases like inverse contracts) | Stage 1 ships a `MockExchange` that we can iterate on; real-venue surprises hit in Stage 3 and don't block Coordinator development |
| Stage 2 SQLite schema turns out wrong → migration pain in Stage 3 | Schema is small enough (3 tables, ~20 fields total) that a full drop + recreate during MVP is acceptable. Document this clearly. Don't ship to anyone but yourself before Stage 5. |
| Stage 3 hits per-venue auth quirks not visible from docs | Budget 2x the time for first venue (Binance), then half for second (Hyperliquid, since we already have an adapter). |
| Reconciler edge cases not exercised until real fills happen (e.g. partial-then-cancel races) | Stage 5 chaos test catches some of these. Some will only surface in real use — accept this and ensure logging is good enough to diagnose post-mortem. |
| User (you) loses interest at Stage 4 because Stage 3 is "good enough" | That's actually fine — Stage 3 alone is a usable tool. Stages 4–5 add polish, not core functionality. Don't force yourself to finish all stages if Stage 3 covers your real use case. |
