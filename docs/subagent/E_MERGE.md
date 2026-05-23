# Subagent E — Merge, Integration, and E2E

> **Depends on:** Subagents A, B, C, D all completed in their worktrees
> **Runs in:** main branch (merges all worktrees)
> **Parallel with:** nothing (final step)
> **Estimated LOC:** ~300 new (mostly tests + config)

## Purpose

1. Merge the 4 worktree branches (A, B, C, D) into main
2. Resolve all merge conflicts (import path collisions, `__init__.py` conflicts, overlapping pyproject.toml changes)
3. Wire real venue adapters (Binance + Hyperliquid testnet)
4. Run the full E2E test suite (mock + real testnet)
5. Verify the legacy bot still works

## Phase 1: Merge worktrees

### Merge order

```
main (post-Stage-0) + A (Market)     → merge-1
merge-1 + B (Persistence)            → merge-2
merge-2 + C (Coordinator)            → merge-3
merge-3 + D (CLI)                    → merge-4 (candidate for E2E)
```

Merge one at a time, in this order. A, B, C were built in parallel so their `__init__.py` changes will conflict — prefer "union of all exports" resolution. Their test directories are disjoint so no conflicts there.

### Expected conflict hotspots

| File | Conflict | Resolution |
|---|---|---|
| `src/market/__init__.py` | A added exports B/C didn't know about | Union: keep all exports |
| `src/coordinator/__init__.py` | C added many exports | Keep C's version |
| `src/persistence/__init__.py` | B added `PersistenceStore` | Keep B's version |
| `pyproject.toml` | A/B/C all might have added test deps | Manual merge; keep all non-duplicate deps |
| `src/market/quote.py` | A added `estimate_fill` implementation + `filled_fully` field | Keep A's version |
| `src/market/registry.py` | A implemented method bodies | Keep A's version |

### After each merge step, verify

```bash
uv sync --extra dev
uv run pytest                     # no regressions
uv run pytest tests/market tests/persistence tests/coordinator tests/cli -v   # all new tests pass
```

## Phase 2: Real venue integration

### Binance testnet

1. Verify `config/exchanges.yaml` has a Binance entry with `type: ccxt`, `enabled: true`, testnet URLs
2. Verify `secrets.example.yaml` has Binance credentials template
3. Test: `CCXTExchange("binance", config, secrets).connect()` succeeds on testnet
4. Implement `CCXTExchange.list_markets()` — convert ccxt market data to `Instrument` objects
   - ccxt's `exchange.load_markets()` returns `{symbol: {...}}` with fields like `base`, `quote`, `type`, `spot`, `swap`, `limits`, `precision`
   - Map: `type == "swap" or (type == "spot" and swap == True)` → `market_type = "perp"`; `type == "spot"` → `market_type = "spot"`
   - `venue_symbol` = ccxt symbol (e.g. `"BTC/USDT"` for Binance)
   - `min_qty` / `qty_step` / `price_step` from ccxt `limits` / `precision` fields
   - `taker_fee_rate` / `maker_fee_rate` from config (exchanges.yaml has this)
5. Verify: `InstrumentRegistry.load_all()` populates Binance instruments correctly

### Hyperliquid testnet

Already works for the legacy bot. Verify `CCXTExchange.list_markets()` returns correct Instruments for Hyperliquid.

### `BaseExchange.list_markets()` abstract method

Add to `BaseExchange` if not already there from Stage 0/1 work:
```python
@abstractmethod
async def list_markets(self) -> list["Instrument"]:
    """Return all tradable instruments on this exchange."""
    ...
```

Both `CCXTExchange` and `LighterExchange` get concrete implementations.

## Phase 3: E2E tests (`tests/e2e/`)

### Mock E2E (no network — always runs)

`test_orchestrator_e2e_mock.py`:
- Build fully wired Orchestrator with `MockExchange` for 2 venues
- Submit 5 intents covering all 4 terminal states
- Verify each returns correct status + exit code
- Verify `is_blocked_by_needs_manual` blocks after NEEDS_MANUAL

### Testnet E2E (marked `@pytest.mark.network`)

`test_testnet_spot_market.py`:
- Requires `config/secrets.yaml` configured
- Submit a small testnet market order ($20 notional) across Binance + Hyperliquid
- Verify ALL_FILLED status
- Verify fill amounts are close to planned amounts

`test_testnet_dry_run.py`:
- Submit a `--dry-run` intent against real testnets
- Verify Plan output is reasonable (prices, fees, slippage estimates)
- Verify NO orders were actually sent

`test_testnet_partial_failure.py`:
- Intentionally use a wrong symbol on one venue
- Verify the system rejects at Validate stage (not Execute)
- Or: use an impossibly tight max_slippage → reject at Plan stage

## Phase 4: Final verification checklist

```bash
# 1. All tests
uv run pytest -m "not network"           # all green, no regressions
uv run pytest tests/e2e -v              # all green (mock tests)

# 2. Lint
uv run ruff check .
uv run ruff format --check .

# 3. Legacy bot
uv run python -m src.main --help         # still prints help

# 4. oneFill CLI
uv run onefill --help                    # all commands listed
uv run onefill venues                    # shows binance + hyperliquid
uv run onefill order --help              # all options listed

# 5. Import chain
uv run python -c "
from src.market import Asset, Instrument, Quote, InstrumentRegistry, QuoteFetcher
from src.coordinator import Intent, Plan, PlannedLeg
from src.coordinator.planner import Planner
from src.coordinator.validator import Validator
from src.coordinator.executor import Executor
from src.coordinator.reconciler import Reconciler
from src.coordinator.orchestrator import Orchestrator
from src.persistence.store import PersistenceStore
from src.cli.bootstrap import build_orchestrator
print('all imports ok')
"
```

## Commit message

```
stage 3-4: merge worktrees + real venue integration + E2E

Merge Market (A), Persistence (B), Coordinator (C), and CLI (D)
worktree branches. Implement CCXTExchange.list_markets() for Binance
and Hyperliquid testnet. Add mock E2E test suite covering all 4
terminal states. Add testnet E2E tests (network-gated).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Out of scope

- ❌ Perp-specific features (leverage setting, margin checks, funding rate fetching) — those are Stage 4, a separate workstream after this merge
- ❌ Agent SDK integration — Stage 5
- ❌ Production deployment config
- ❌ Lighter exchange `list_markets()` — the adapter exists but we don't need it for MVP (Binance + Hyperliquid only)
