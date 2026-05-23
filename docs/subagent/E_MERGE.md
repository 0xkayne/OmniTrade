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

## Phase 2: Post-merge cleanup

### Migrate C's FakeExchange to A's MockExchange

Subagent C built a minimal `FakeExchange` in `tests/coordinator/conftest.py` for its tests. After merging, migrate those tests to use A's `MockExchange` from `src.market.mock_backend` instead. The `MockExchange` is more complete (supports order injection, error injection, canned orderbooks) and will be the canonical test double going forward.

Steps:
1. Replace `from tests.coordinator.conftest import FakeExchange` → `from src.market.mock_backend import MockExchange`
2. Update any fixture that constructed `FakeExchange` to use `MockExchange` with equivalent setup
3. Delete `FakeExchange` from `tests/coordinator/conftest.py`
4. Verify `uv run pytest tests/coordinator -v` still passes

### Create data/ and logs/ directories

```bash
mkdir -p data logs
echo "*.db" >> .gitignore   # SQLite files are local state, not committed
```

The `data/` directory holds `onefill.db` by default. The `logs/` directory holds JSONL audit files. Both are local-only (gitignored).

## Phase 3: Real venue integration

### Binance testnet config

Add this entry to `config/exchanges.yaml` (merge into the existing `exchanges:` dict):

```yaml
  binance:
    type: "ccxt"
    enabled: true
    default_network: "testnet"
    networks:
      mainnet:
        rest_base_url: "https://api.binance.com"
        websocket_url: "wss://stream.binance.com:9443/ws"
      testnet:
        rest_base_url: "https://testnet.binance.vision"
        websocket_url: "wss://testnet.binance.vision/ws"
    rate_limit: 1200
    symbols: []
    fees:
      taker: 0.00075
      maker: 0.00075
```

Add this to `config/secrets.example.yaml`:
```yaml
binance:
  api_key: "your_binance_testnet_api_key"
  secret: "your_binance_testnet_secret"
```

### Binance testnet

1. Verify `config/exchanges.yaml` has the Binance entry with `type: ccxt`, `enabled: true`, testnet URLs
2. Verify `secrets.example.yaml` has Binance credentials template
3. Test: `CCXTExchange("binance", config, secrets).connect()` succeeds on testnet
4. Implement `CCXTExchange.list_markets()` — convert ccxt market data to `Instrument` objects
   - ccxt's `exchange.load_markets()` returns `{symbol: {...}}` with fields like `base`, `quote`, `type`, `spot`, `swap`, `limits`, `precision`
   - Map: `type == "swap"` (or ccxt's `swap == True`) → `market_type = "perp"`; `type == "spot"` → `market_type = "spot"`
   - `venue_symbol` = ccxt symbol (e.g. `"BTC/USDT"` for Binance spot, `"BTC/USDT:USDT"` for Binance perp)
   - `min_qty` / `qty_step` / `price_step` from ccxt `limits` / `precision` fields
   - `taker_fee_rate` / `maker_fee_rate` from config (exchanges.yaml has this)
5. Verify: `InstrumentRegistry.load_all()` populates Binance instruments correctly

### Hyperliquid testnet

Already works for the legacy bot. Verify `CCXTExchange.list_markets()` returns correct Instruments for Hyperliquid. The existing `CCXTExchange._build_ccxt_config` already handles Hyperliquid auth, so no code changes needed — just verify the `list_markets()` implementation works.

### `BaseExchange.list_markets()` abstract method

Already defined in Stage 0 contract (§10). Verify both `CCXTExchange` and `LighterExchange` have concrete implementations. If any subagent didn't implement it, add a stub that raises `NotImplementedError` for now.

## Phase 4: E2E tests (`tests/e2e/`)

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

## Phase 5: Final verification checklist

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

## Troubleshooting common integration failures

### "ImportError: No module named 'src.market.quote_fetcher'"
→ Subagent A didn't create `quote_fetcher.py`, or the file is in the wrong directory. Check `src/market/quote_fetcher.py` exists and `src/market/__init__.py` exports `QuoteFetcher`.

### "ImportError: cannot import name 'PersistenceStore'"
→ Subagent B's `src/persistence/store.py` exists but the class name doesn't match, or `src/persistence/__init__.py` doesn't export it.

### "TypeError: Orchestrator.__init__() got an unexpected keyword argument"
→ The contract type signatures (Stage 0) and the implementation signatures (C) are inconsistent. Compare `src/coordinator/orchestrator.py` against `docs/subagent/C_COORDINATOR.md`. Whichever is wrong, fix the code (not the doc — the doc is the spec).

### `uv run pytest tests/coordinator` fails after merge but passed in C's worktree
→ C's tests probably depended on C's `FakeExchange` which was deleted during Phase 2 migration. Check that the migration was done correctly — every test fixture that used `FakeExchange` should now use `MockExchange`.

### "no such table: intents" during E2E tests
→ `PersistenceStore.initialize()` wasn't called, or the SQLite path is wrong. The `bootstrap.build_orchestrator()` should call `store.initialize()` before returning.

### `onefill venues` crashes with "Registry not loaded"
→ This is expected when secrets aren't configured. The CLI should handle it gracefully (see Phase 2 of D_CLI.md). If it crashes instead, fix the venues command to show config-only info when the registry isn't loaded.

### ccxt.AuthenticationError on Binance testnet
→ Testnet credentials need to be created at https://testnet.binance.vision/ (separate from mainnet). Verify `secrets.yaml` has the testnet API key.

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
