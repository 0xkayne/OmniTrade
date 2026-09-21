---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-13
applies_to: onefill CLI
---

# CLI Reference

oneFill exposes a CLI via the `onefill` command (entry point: `src/cli/main.py:app`). 当前有 12 个顶层命令、18 个叶子操作。参数和默认值以 `onefill <command> --help` 与 `src/cli/main.py` 为准。

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
| `--max-slippage-pct` | no | — | Fixed price protection vs planning mid-price; unset execution tolerance is 0.5% |
| `--max-fee-usd` | no | — | Reject if total estimated fee exceeds this |
| `--max-funding-rate-pct` | no | — | Reject if perp funding rate exceeds this |
| `--execute-timeout` | no | `30` | Seconds before executor times out and triggers reconciliation |
| `--time-in-force` | no | `IOC` | `GTC`, `IOC`, or `FOK`; unsupported venue capabilities reject |
| `--max-spread-pct` | no | — | Maximum orderbook spread |
| `--max-quote-age-ms` | no | `1000` | Maximum quote age at send |
| `--max-total-cost-usd` | no | — | Aggregate adverse price deviation plus fees |
| `--max-order-notional-usd` | no | — | Maximum size of each sequential split order |
| `--min-fill-ratio` | no | `1.0` | Below 1 requires a single leg |
| `--compensation-slippage-pct` | no | `0.5` | Compensation price tolerance vs actual original fill |
| `--reconcile-timeout` | no | `10` | Cancellation and compensation deadline seconds |
| `--poll-interval-ms` | no | `500` | Cap for adaptive HTTP polling backoff |
| `--no-websocket` | no | — | Disable WebSocket fill watching; HTTP polling only |
| `--network` | no | `testnet` | `testnet` or `mainnet` |
| `--dry-run` | no | — | Plan + validate + risk-check only; no orders sent |
| `--yes` | no | — | Skip the interactive confirmation prompt |
| `--json` | no | — | Machine-readable JSON output |

### `onefill query <intent-id>`

Show the full state of a single intent: per-leg fills, fees, timestamps, status transitions.
Add `--json` to include the durable requests and cumulative snapshots for every original and compensation order.

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

Cancel a PENDING/VALIDATED intent only when it has no persisted legs and no executor holds the database lock.
Intents that may have live orders must use explicit recovery; cancel never marks such orders as rejected locally.

### `onefill recover`

List intents stuck in `ROLLED_BACK_FAILED` with suggested remediation. This state blocks all subsequent intents until resolved.

`onefill recover --intent-id ID --network testnet` settles and flattens an interrupted, nonterminal intent.
Use the original network. It queries/cancels original orders and confirms protected compensation, without
resubmitting opening orders. Even fully executed original orders are flattened by this interrupted-execution
recovery policy. Existing terminal states, including NEEDS_MANUAL, are not retried; use `ack` after manual review.

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

### `onefill arb testnet-smoke`

Check Arcus, Hyperliquid, and Binance testnet configuration, markets, order
books, and order capabilities without submitting an order. The command can
temporarily construct a configured adapter even when its `enabled` flag is
false; it never writes configuration or submits an order. Credentials remain
in the local, ignored `config/secrets.yaml` file.

| Flag | Default | Description |
|---|---|---|
| `--symbol` | `BTC` | Base asset to check on both venues |
| `--market` | `perp` | `perp` or `spot` |
| `--venues` | `arcus,hyperliquid,binance` | Comma-separated venue selection |
| `--account` | — | Read balances when credentials are configured |
| `--json` | — | Machine-readable JSON output |

### `onefill arb testnet-canary`

Run exactly one small, explicitly confirmed testnet hedge cycle and immediately close it.
The command is bounded to a default 25 USD notional and never targets mainnet. It requires
private testnet credentials for both selected venues: Arcus Ed25519, Hyperliquid EVM wallet,
or Binance Demo HMAC credentials as applicable.

```bash
uv run --locked onefill arb testnet-canary \
  --venue-a arcus --venue-b hyperliquid --base BTC --quantity 0.0001 \
  --max-notional-usd 25 --confirm TESTNET_CANARY --json
```

| Flag | Default | Description |
|---|---|---|
| `--venue-a` | `arcus` | First testnet venue |
| `--venue-b` | `hyperliquid` | Second testnet venue |
| `--base` | `BTC` | Base asset |
| `--quantity` | `0.001` | Base quantity for each leg |
| `--direction` | `buy_a_sell_b` | `buy_a_sell_b` or `buy_b_sell_a` |
| `--max-notional-usd` | `25` | Hard per-cycle notional limit |
| `--confirm` | — | Must be exactly `TESTNET_CANARY` |
| `--json` | — | Machine-readable output |

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

## Price watch commands

### `onefill watch run`

Run the configured watchlist daemon. It fetches candles, evaluates the pair-band strategy, and sends Telegram alerts.

### `onefill watch backfill`

Fetch historical candles for the configured watchlist without starting the alert loop.

## Trade log commands

### `onefill trades record`

Record a manual trade in the persistent trade ledger.

### `onefill trades list`

List recorded trades, optionally filtered by symbol, venue, tag, or date.

### `onefill trades export`

Export recorded trades for external analysis.

## Backtest command

### `onefill backtest run`

Replay the configured candle data through the same pair-band signal engine used by `watch`, then print portfolio metrics. Use `--json` for machine-readable output.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `ALL_FILLED` — every leg filled within tolerances |
| `1` | General error (bad args, unreachable venue, etc.) |
| `2` | `REJECTED` — plan or validation failed; no orders sent |
| `3` | `ROLLED_BACK` — partial fill, compensation succeeded; net exposure flat |
| `4` | `ROLLED_BACK_FAILED` — compensation failed; manual intervention required |

These let you script multi-step workflows with safe failure handling.
