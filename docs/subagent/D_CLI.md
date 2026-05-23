# Subagent D — CLI Layer Implementation

> **Depends on:** All of A (Market), B (Persistence), C (Coordinator) merged
> **Runs in:** a git worktree branched from the post-merge commit of A+B+C
> **Parallel with:** nothing (serial — depends on all prior subagents)
> **Estimated LOC:** ~400 new

## Purpose

Wire the full Orchestrator into a `typer` CLI. The CLI stub and entry point (`src/cli/main.py`) already exist from Stage 0 — the command stubs raise `NotImplementedError`. **You replace those stubs with real implementations.**

## Files to modify

### NEW: `src/cli/bootstrap.py`

```python
"""Build the Orchestrator from config files or injected mocks."""

async def build_orchestrator(
    exchanges_config_path: Path = Path("config/exchanges.yaml"),
    secrets_config_path: Path = Path("config/secrets.yaml"),
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
    _exchanges: dict | None = None,   # for test injection
    _store: "PersistenceStore | None" = None,  # for test injection
) -> "Orchestrator":
    """
    1. Load exchanges.yaml + secrets.yaml (skip if _exchanges provided)
    2. Create ExchangeFactory, initialise exchanges (skip if _exchanges provided)
    3. Build InstrumentRegistry, load all instruments
    4. Build QuoteFetcher
    5. Build PersistenceStore (skip if _store provided), initialise
    6. Build Orchestrator(registry, quote_fetcher, exchanges, store)
    7. Return
    """
```

The `_exchanges` and `_store` parameters are **dependency injection hooks for testing** — never exposed in CLI flags. In production, they are always `None` and the function reads from config files. In tests, you inject mock exchanges and an in-memory store.

### MODIFY: `src/cli/main.py` — replace every stub

#### `order` command

```
onefill order [OPTIONS]
```

Steps:
1. Parse `--split` from `"binance=0.5,hyperliquid=0.5"` into `dict[str, float]`
2. Parse `--quote-preference` from `"USDT,USDC"` into `list[str]`
3. Generate an `intent_id` (uuid7 or ulid)
4. Build `Intent` dataclass from parsed args
5. Build `Orchestrator` from bootstrap (see below)
6. Call `orch.submit(intent, dry_run=dry_run)`
7. Format output:
   - `--json` → print `json.dumps(result, indent=2)`, exit with code matching status
   - No `--json` → render with `rich`:

```
╭─────────────────────────────────────────────────╮
│              oneFill — Order Result             │
╰─────────────────────────────────────────────────╯
 Status:  ALL_FILLED
 Intent:  buy $1000 BTC (spot) across 2 venues

 binance (spot):
   Instrument:   BTC/USDT
   Notional:     $500.00 → 0.00744 BTC
   Fill price:   $67,234.50
   Slippage:     0.08%
   Fee:          $1.34

 hyperliquid (spot):
   Instrument:   BTC/USDC:USDC
   ...

 Aggregate:
   Weighted avg: $67,238.50
   Total fee:    $2.69
   Duration:     873ms
```

8. Exit with code 0 (ALL_FILLED), 2 (REJECTED), 3 (ROLLED_BACK), or 4 (NEEDS_MANUAL)

Implementation notes:
- `--dry-run` calls `orch.submit(intent, dry_run=True)` — skips Execute/Reconcile
- `--yes` skips a confirmation prompt that otherwise shows the Plan and asks "Proceed? [y/N]"
- `--json` suppresses rich output and prints raw JSON

JSON output shape (single object, always valid JSON):
```json
{
  "intent_id": "intent_01J...",
  "status": "ALL_FILLED",
  "legs": [
    {
      "venue": "binance",
      "instrument": "BTC/USDT",
      "market_type": "spot",
      "notional_usd": 500.0,
      "qty_base": 0.00744,
      "order_id": "abc123",
      "filled_amount": 0.00744,
      "avg_price": 67234.5,
      "fee_usd": 1.34,
      "slippage_pct": 0.08
    }
  ],
  "aggregate": {
    "total_notional": 1000.0,
    "weighted_avg_price": 67238.5,
    "total_fee_usd": 2.69,
    "duration_ms": 873
  },
  "error": null
}
```
Exit code matches PRD §6.3: 0=ALL_FILLED, 2=REJECTED, 3=ROLLED_BACK, 4=NEEDS_MANUAL.

#### `query` command

```
onefill query <intent_id>
```

Load intent + legs from PersistenceStore, render with rich (or JSON).

#### `list_intents` command

```
onefill list [--status STATUS]
```

Load last 50 intents, render a table:

```
Intent ID          Status        Created             Base   Notional  Venues
intent_abc123...   ALL_FILLED    2026-05-23 14:32    BTC    $1000     binance,hyperliquid
intent_def456...   ROLLED_BACK   2026-05-23 14:28    ETH    $500      binance,hyperliquid
```

#### `cancel` command

```
onefill cancel <intent_id>
```

Logic:
- Load intent. If status is terminal → "Cannot cancel: intent is already in terminal state {status}"
- If status is PENDING or VALIDATED → set to REJECTED
- If status is EXECUTING → for each leg that is SENT but not filled, call `exchange.cancel_order()`. If the exchange does not support cancel for this order type (some venues don't), log a warning and suggest waiting for Execute timeout to trigger Reconcile automatically.
- Print result.

#### `recover` command

```
onefill recover
```

Logic:
1. Load all intents with status `ROLLED_BACK_FAILED` (= NEEDS_MANUAL)
2. If none: "No intents need manual recovery."
3. If any: for each, print:
   - Intent ID + summary
   - Each leg's status
   - Suggested action: "Review positions manually on each venue. Run `onefill cancel <id>` to mark as resolved."
4. Exit 0

#### `venues` command

```
onefill venues
```

Logic:
1. List venues from config (read `config/exchanges.yaml`)
2. Show: name, type (ccxt/native), enabled, default_network
3. If registry is loaded, also show instrument count per venue
4. If registry is NOT loaded (e.g. secrets not configured yet), show config-only
   info without crashing — print a note "Registry not loaded (run onefill order --dry-run first?)"

This is called once at startup by every CLI command that needs the orchestrator. (MVP: each command re-builds it — no daemon.)

## Tests to write (`tests/cli/`)

| Test file | What it covers |
|---|---|
| `test_main_help.py` | `onefill --help` and `onefill order --help` output correct text |
| `test_order_parse.py` | CLI arg parsing → Intent fields correct; `--split` parsing edge cases; `--json` flag |
| `test_order_mock.py` | Full `order` command with mock orchestrator → verify exit code mapping, JSON output shape |
| `test_query_mock.py` | `query` command output format |
| `test_list_mock.py` | `list_intents` table output |
| `test_bootstrap.py` | `build_orchestrator` constructs without crashing (with mock config) |

All tests should use a mock `Orchestrator.submit()` — you don't need real exchanges for CLI tests.

## Verification

```bash
uv run onefill --help                                    # all commands listed
uv run onefill order --help                              # all options listed
uv run onefill venues                                    # shows configured venues (may need real config)
uv run pytest tests/cli -v                               # all green
uv run python -c "from src.cli.bootstrap import build_orchestrator; print('bootstrap ok')"
```

## Commit message

```
stage 3: oneFill CLI implementation

Replace CLI stubs with real implementations for order, query,
list_intents, cancel, recover, and venues commands. Add bootstrap
module that wires Orchestrator from config files. Rich-formatted
output for humans; --json for machines. Full CLI test suite.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Out of scope

- ❌ Real venue testnet integration — CLI commands work against the Orchestrator, which currently uses mock exchanges. Real exchanges come in the merge/E2E stage.
- ❌ Persistent daemon — each `onefill order` invocation creates a fresh Orchestrator
- ❌ Agent SDK wrapper — `src/cli/agent_api.py` comes in Stage 5
- ❌ Config file editing commands
