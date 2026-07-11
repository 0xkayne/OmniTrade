# CLI Reference

oneFill exposes a CLI via the `onefill` command (entry point: `src/cli/main.py:app`). Eight core commands plus four funding-rate arbitrage subcommands.

## Core commands

### `onefill order` — submit a coordinated intent

| Flag | Required | Default | Description |
|---|---|---|---|
| `--base` | yes | — | Base asset symbol, e.g. `BTC`, `ETH`, `SOL` |
| `--quote-preference` | no | `USDT,USDC` | Comma-separated list, tried in order when matching instruments |
| `--product` | yes | — | `spot` or `perp`. Default for all legs; individual legs can override via `--split` |
| `--side` | yes | — | `buy` or `sell`. Default for all legs; individual legs can override via `--split` |
| `--type` | yes | — | `market` or `limit` |
| `--total-notional-usd` | yes | — | Total intent size in USD |
| `--split` | yes | — | Venue weights, e.g. `binance=0.5,hyperliquid=0.5` (must sum to 1.0). Extended syntax: `venue=weight:side:product:leverage` |
| `--leverage` | no | `1` | Leverage (perp only). oneFill calls `set_leverage()` on the exchange before placing perp orders |
| `--limit-price` | no | — | Price for limit orders |
| `--max-slippage-pct` | no | — | Reject if estimated slippage exceeds this. On Hyperliquid market orders, passed to ccxt as IOC limit-price tolerance (default 5%) |
| `--max-fee-usd` | no | — | Reject if total estimated fee exceeds this |
| `--max-funding-rate-pct` | no | — | Reject if perp funding rate exceeds this |
| `--execute-timeout` | no | `30` | Seconds before executor times out and triggers reconciliation |
| `--time-in-force` | no | — | `GTC`, `IOC`, or `FOK` |
| `--poll-interval-ms` | no | `500` | Cap for adaptive HTTP polling backoff |
| `--no-websocket` | no | — | Disable WebSocket fill watching; HTTP polling only |
| `--network` | no | `testnet` | `testnet` or `mainnet` |
| `--dry-run` | no | — | Plan + validate + risk-check only; no orders sent |
| `--yes` | no | — | Skip the interactive confirmation prompt |
| `--json` | no | — | Machine-readable JSON output |

### `onefill query <intent-id>`

Show the full state of a single intent: per-leg fills, fees, timestamps, status transitions.

```bash
uv run onefill query 7a3f9b2c-…
```

### `onefill list-intents [--status STATUS]`

List the 50 most recent intents, optionally filtered by status.

Valid statuses: `PENDING`, `VALIDATED`, `EXECUTING`, `ALL_FILLED`, `REJECTED`, `ROLLED_BACK`, `ROLLED_BACK_FAILED`.

```bash
uv run onefill list-intents --status ROLLED_BACK_FAILED
```

### `onefill cancel <intent-id>`

Cancel a non-terminal intent in the store.

!!! warning
    In the current MVP this does not cancel orders on the exchange itself if execution is already in flight — use exchange UIs for that.

### `onefill recover`

List intents stuck in `ROLLED_BACK_FAILED` with suggested remediation. This state blocks all subsequent intents until resolved.

### `onefill ack <intent-id>`

Acknowledge a `ROLLED_BACK_FAILED` intent after manual review. Transitions it to `RESOLVED_MANUAL` and unblocks the system.

### `onefill venues`

Print configured venues from `config/exchanges.yaml`: type, enabled flag, default network, supported symbols.

### `onefill instruments`

Browse the local instrument cache (persisted to SQLite, TTL 24h).

```bash
onefill instruments --base BTC              # all BTC pairs across venues
onefill instruments --venue binance         # all Binance pairs
onefill instruments --market perp           # perp only
onefill instruments --refresh               # force re-fetch from exchanges
onefill instruments --base BTC --json       # machine-readable output
```

## Funding rate arbitrage commands

### `onefill arb scan`

Scan current funding rates across venues for cross-venue spread opportunities.

| Flag | Default | Description |
|---|---|---|
| `--base` | — | Filter by base asset |
| `--min-spread` | — | Minimum annualized spread to report |
| `--json` | — | Machine-readable JSON output |

### `onefill arb run`

Run the continuous arbitrage daemon (scan → decide → execute → repeat).

| Flag | Default | Description |
|---|---|---|
| `--min-spread` | — | Minimum spread to open a position |
| `--exit-spread` | — | Spread threshold to close positions |
| `--notional` | — | Notional size per position |
| `--interval` | — | Scan interval in seconds |
| `--max-positions` | — | Maximum concurrent hedged positions |
| `--base` | — | Filter by base asset |
| `--dry-run` | — | Scan only, no orders |

### `onefill arb positions`

List currently open hedged positions.

### `onefill arb history`

Query historical funding rate snapshots and arb events.

| Flag | Default | Description |
|---|---|---|
| `--base` | — | Filter by base asset |
| `--venue` | — | Filter by venue |
| `--limit` | — | Max rows to return |
| `--json` | — | Machine-readable JSON output |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `ALL_FILLED` — every leg filled within tolerances |
| `1` | General error (bad args, unreachable venue, etc.) |
| `2` | `REJECTED` — plan or validation failed; no orders sent |
| `3` | `ROLLED_BACK` — partial fill, compensation succeeded; net exposure flat |
| `4` | `ROLLED_BACK_FAILED` — compensation failed; manual intervention required |

These let you script multi-step workflows with safe failure handling.
