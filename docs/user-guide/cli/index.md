---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-30
applies_to: onefill CLI
---

# CLI Reference

Omnitrade exposes a CLI via the `onefill` command (entry point: `src/cli/main.py:app`). 参数和默认值以 `onefill <command> --help` 与 `src/cli/main.py` 为准。

## Core commands

### `onefill order` — submit a coordinated intent

| Flag | Required | Default | Description |
|---|---|---|---|
| `--base` | yes | — | Base asset symbol, e.g. `BTC`, `ETH`, `SOL` |
| `--quote-preference` | no | `USDT,USDC` | Comma-separated list, tried in order when matching instruments |
| `--product` | yes | — | `spot` or `perp`. Default for all legs; individual legs can override via `--split` |
| `--side` | yes | — | `buy` or `sell`. Default for all legs; individual legs can override via `--split` |
| `--type` | yes | — | `market` or `limit` |
| `--total-notional-usd` | except close-all | — | USD sizing budget; a maximum when native quantity is given |
| `--contract-type` | no | linear for perp | `linear` (USDⓈ-M) or `inverse` (COIN-M); spot must leave unset |
| `--settlement-asset` | no | — | Required settlement asset filter, e.g. `USDT` or `BTC` |
| `--leg-contract-type` | no | — | Repeat `venue=linear` / `venue=inverse` to override each leg |
| `--leg-settlement-asset` | no | — | Repeat `venue=ASSET` to override each leg |
| `--position-effect` | no | `open` | `open` or `close`; close uses reduce-only and requires an existing opposite position |
| `--close-all` | no | false | Single-leg perp close of the observed entire position; budget may be omitted |
| `--quantity-native` | no | — | Exact single-leg quantity: base units for spot, contracts for perp; requires a USD maximum budget and excludes `--close-all` |
| `--split` | yes | — | Venue weights, e.g. `binance=0.5,hyperliquid=0.5` (must sum to 1.0). Extended syntax: `venue=weight:side:product:leverage` |
| `--leverage` | no | `1` | Leverage (perp only). Omnitrade calls `set_leverage()` on the exchange before placing perp orders |
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

`--network` selects both the exchange endpoints and `config/secrets.<network>.yaml`.
If an entry point supplies no network override, each venue uses its `default_network`
(or `testnet` if absent). No credentials are taken from the other network or the shared
`config/secrets.yaml`. See [Configuration](../configuration/index.md) for file setup and migration.

### `onefill query <intent-id>`

Show the full state of a single intent: per-leg fills, fees, timestamps, status transitions.
Add `--json` to include the durable requests and cumulative snapshots for every original and compensation order.

```bash
uv run onefill query 7a3f9b2c-…
```

### `onefill status <intent-id>`

Without `--refresh`, show the saved intent. `--refresh --network testnet` reads original and compensation
orders plus the current position on the original network. It reports updated facts and never submits,
cancels, compensates, or automatically clears a blocking state. Add `--json` for machine-readable output.

### `onefill binance-smoke`

Check a single Binance product family, defaulting to public read-only Demo access:

```bash
uv run --locked onefill binance-smoke --network testnet --family spot --json
uv run --locked onefill binance-smoke --network testnet --family usdm --account --json
uv run --locked onefill binance-smoke --network testnet --family coinm --json
```

`--family` selects `spot`, `usdm`, or `coinm`; `--base` defaults to BTC. `--account` also reads the selected
family's authenticated account and perp position. Public market success does not verify order permission;
`family_errors` reports initialization failures for that family. The command enables only its selected
family in memory, including opt-in COIN-M, without changing configuration.

Order submission requires both `--allow-orders` and a positive `--notional-cap`, and is restricted to
`--network testnet`. The coordinator persists one bounded open/compensation cycle, requires no existing
orders on the selected instrument and a flat initial perp position, then closes the actual fill through
protected compensation. A cap below venue minimum produces `SKIPPED` with `orders_sent: false`;
it is not successful trading evidence. Only `CLOSED` confirms the requested cycle completed. This
implementation has local simulated test coverage; no live Demo trading verification is claimed.

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

`onefill recover --intent-id ID --network testnet` settles an interrupted, nonterminal intent.
Use the original network. Opening intents restore their recorded pre-send position baseline with protected
compensation. Closing intents never reopen closed exposure: unresolved close outcomes block for manual
review. Existing terminal states, including NEEDS_MANUAL, are not retried; use guarded `ack` after manual review.

### `onefill ack <intent-id>`

Acknowledge a `ROLLED_BACK_FAILED` intent after manual correction with `--network` matching the original
intent (default `testnet`). The coordinator first confirms that orders are terminal and positions match
recorded fills. Unresolved orders or position drift reject acknowledgement. A successful acknowledgement
transitions to `RESOLVED_MANUAL` and unblocks the system.

### `onefill venues`

Print configured venues from `config/exchanges.yaml`: type, enabled flag, default network, supported symbols.

### `onefill instruments`

Browse the local instrument cache (persisted to SQLite, TTL 24h). Output distinguishes network,
linear/inverse contracts, settlement asset, native quantity units, and contract size.

```bash
onefill instruments --base BTC              # all BTC pairs across venues
onefill instruments --venue binance         # all Binance pairs
onefill instruments --market perp           # perp only
onefill instruments --refresh               # force re-fetch from exchanges
onefill instruments --base BTC --json       # machine-readable output
```

Perpetual order selection defaults to linear contracts. COIN-M requires an explicit `inverse` selector
and enabled family; it is excluded from funding and cross-venue arbitrage. See the
[Binance integration design](../../developer-guide/design/base-binance-integration.md).

## Funding rate arbitrage commands

### `onefill arb testnet-smoke`

Check Arcus, Hyperliquid, and Binance testnet configuration, markets, order
books, and order capabilities without submitting an order. The command can
temporarily construct a configured adapter even when its `enabled` flag is
false; it never writes configuration or submits an order. Credentials remain
in the local, ignored `config/secrets.testnet.yaml` file with `network: "testnet"`.
The command always selects testnet, even when the venue’s configured default is mainnet.

| Flag | Default | Description |
|---|---|---|
| `--symbol` | `BTC` | Base asset to check on both venues |
| `--market` | `perp` | `perp` or `spot` |
| `--venues` | `arcus,hyperliquid,binance` | Comma-separated venue selection |
| `--account` | — | Read balances when credentials are configured |
| `--json` | — | Machine-readable JSON output |

### `onefill arb testnet-canary`

Run exactly one small, explicitly confirmed testnet hedge cycle and immediately close it.
The command defaults to a 25 USD limit per order, with a hard ceiling of 100 USD, and never targets mainnet. It requires
private testnet credentials for both selected venues: Arcus `master_wallet_address` / `api_key` / `api_signing_key`, Hyperliquid
`master_wallet_address` plus approved `api_wallet_address` / `api_wallet_private_key`,
or Binance Demo HMAC credentials as applicable, loaded only from
`config/secrets.testnet.yaml`. It never reads the mainnet credentials file.

Both selected symbols must have verified zero positions and no open orders. Unfinished or
`MANUAL_REVIEW` arbitrage cycles block a new run. Opening and closing use fresh order books,
fixed midpoint ±0.5% limits, effective price ticks and quantity rules; insufficient protected
depth or a close that exceeds the budget leaves `RECOVERY` with its order identities.
Canary limit prices start at midpoint ±0.4%, leaving 0.1 percentage points for quote changes
before the validation suite rechecks the hard 0.5% boundary at submission.
`CLOSED` requires terminal order evidence and a final zero-position/no-open-orders check.
Use the [DEX validation suite](../examples/dex-testnet-validation.md) for a complete Arcus /
Hyperliquid run with an independent database and a cumulative turnover budget.

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
| `--symbol-a` | — | Override the first venue's native symbol |
| `--symbol-b` | — | Override the second venue's native symbol |
| `--quantity` | `0.001` | Base quantity for each leg |
| `--direction` | `buy_a_sell_b` | `buy_a_sell_b` or `buy_b_sell_a` |
| `--max-notional-usd` | `25` | Per-order notional cap; cannot exceed the hard 100 USD ceiling |
| `--confirm` | — | Must be exactly `TESTNET_CANARY` |
| `--json` | — | Machine-readable output |

### `onefill arb scan`

Scan current funding rates across venues for cross-venue spread opportunities.

| Flag | Default | Description |
|---|---|---|
| `--base` | — | Filter by base asset |
| `--min-spread` | `0.0` | Minimum absolute funding-rate spread to report |
| `--json` | — | Machine-readable JSON output |

### `onefill arb run`

Run the continuous arbitrage daemon (scan → decide → execute → repeat).

| Flag | Default | Description |
|---|---|---|
| `--min-spread` | `0.01` | Minimum spread to open a position |
| `--exit-spread` | `0.001` | Spread threshold to close positions |
| `--notional` | `1000` | USD notional per leg |
| `--interval` | `60` | Scan interval in seconds |
| `--max-positions` | `5` | Maximum concurrent hedged positions |
| `--base` | — | Filter by base asset |
| `--dry-run` | false | Scan only, no orders |

### `onefill arb positions`

List currently open hedged positions.

### `onefill arb history BASE`

Query stored funding rate snapshots for a base asset on one venue. `BASE` is a required positional argument.

| Flag | Default | Description |
|---|---|---|
| `BASE` | required | Base asset, e.g. `BTC` |
| `--venue` | `hyperliquid` | Venue to query |
| `--limit` | `100` | Max rows to return |
| `--json` | — | Machine-readable JSON output |

```bash
uv run onefill arb history BTC --venue binance --limit 100 --json
```

Without stored history, text output exits with code 1 and suggests running `arb scan` first;
JSON output returns an empty list with code 0.

## Price watch commands

### `onefill watch run`

Run the configured watchlist daemon. It fetches candles, evaluates the pair-band strategy, and sends Telegram alerts.

| Flag | Default | Description |
|---|---|---|
| `--interval` | `600` | Refresh interval in seconds |
| `--timeframe` | `5m` | Primary candle interval |
| `--strategy` | `pair_band` | Strategy registered in the strategy registry |
| `--window-days` | `5` | Lookback window for each asset |
| `--history-days` | `365` | Startup seed horizon in days when no candles are stored |
| `--seed-intervals` | `5m,1h,4h,1d` | Comma-separated intervals to seed |
| `--mtf-interval` | `1d` | Higher-timeframe buy gate; empty string disables it |
| `--mtf-sma` | `10` | Higher-timeframe SMA length |
| `--buy-drop-pct` | `0.10` | Drop from the window high that emits BUY |
| `--sell-rise-pct` | `0.15` | Rise above the assumed buy price that emits SELL |
| `--signal-cooldown-hours` | `6.0` | Minimum hours between same-direction signals per asset |
| `--telegram-cmd-interval` | `120` | Telegram command polling interval in seconds |
| `--heartbeat` | `14400` | Heartbeat interval in seconds |
| `--network` | configuration | Exchange network override |
| `--dry-run` | false | Evaluate and log signals without Telegram sends |

```bash
uv run onefill watch run --network mainnet --interval 600
uv run onefill watch run --dry-run
```

The watchlist is loaded from `config/watchlist.yaml`. Without `--dry-run`, configure Telegram
`bot_token` and `chat_id` in `config/secrets.yaml`; missing Telegram credentials fail startup.
`--dry-run` still fetches market data and updates the candle store. Candle/backfill and per-asset
scan failures are logged; the daemon continues. Stop it with Ctrl+C. Neither watch command accepts
`--json`. Network defaults come from each venue's `default_network` in `config/exchanges.yaml`.
The buy/sell thresholds are fractions: `0.10` means 10%.

### `onefill watch backfill`

Fetch historical candles for the configured watchlist without starting the alert loop.

| Flag | Default | Description |
|---|---|---|
| `--timeframe` | `5m` | Candle interval to backfill |
| `--window-days` | `5` | Lookback window in days |
| `--history-days` | `365` | Seed horizon in days when no candles are stored |
| `--seed-intervals` | `5m,1h,4h,1d` | Comma-separated intervals to backfill |
| `--network` | configuration | Exchange network override |

```bash
uv run onefill watch backfill --network mainnet --history-days 30
```

Backfill updates the candle store without Telegram credentials. Per-asset/interval failures are
logged and skipped, so `Backfill complete` does not guarantee that every requested series was loaded.

## Trade log commands

### `onefill trades record`

Record a manual trade in the persistent trade ledger.

Typer prompts for omitted required options. The command writes a journal entry and prints its ID.

| Flag | Default | Description |
|---|---|---|
| `--symbol` | required / prompt | Asset symbol; normalized to uppercase |
| `--side` | required / prompt | `buy` or `sell` |
| `--qty` | required / prompt | Filled quantity |
| `--price` | required / prompt | Fill price |
| `--venue` | — | Venue label |
| `--tag` | — | Category label |
| `--fee` | — | Fee in USD |
| `--pnl` | — | Known PnL in USD if closed |
| `--strategy` | — | Strategy or signal label |
| `--reason` | — | Decision rationale |
| `--note` | — | Free-form note |
| `--ts` | now | ISO timestamp |

```bash
uv run onefill trades record --symbol BTC --side buy --qty 0.01 --price 60000 \
  --venue binance --fee 0.30 --strategy manual --note "test fill"
```

A side other than `buy` or `sell` exits with code 2 before writing the entry.

### `onefill trades list`

List recorded trades, newest first, optionally filtered by tag.

| Flag | Default | Description |
|---|---|---|
| `--tag` | `None` | Filter by tag |
| `--limit` | `200` | Maximum rows |
| `--json` | false | Emit JSON |

```bash
uv run onefill trades list --tag manual --limit 50 --json
```

No matches produces an empty table or JSON list and exits successfully.

### `onefill trades export`

Export recorded trades for external analysis.

| Flag | Default | Description |
|---|---|---|
| `--format` | `csv` | `csv` or `json`; other values currently fall back to CSV |
| `--out` | stdout | Output file; stdout when omitted |
| `--tag` | `None` | Filter by tag |

```bash
uv run onefill trades export --format csv
```

Use `--out trades.csv` to write to an existing output directory. File-write failures
exit non-zero; the command does not create parent directories and overwrites an existing file.

## Backtest command

### `onefill backtest run`

Replay the configured candle data through the same pair-band signal engine used by `watch`, then print portfolio metrics. Use `--json` for machine-readable output.

| Flag | Default | Description |
|---|---|---|
| `--days` | `30` | Backtest duration in days |
| `--timeframe` | `5m` | Candle interval |
| `--capital` | `10000` | Starting capital in USD |
| `--per-trade-usd` | `1000` | Position size per trade |
| `--fee-rate` | `0.0005` | Fee fraction per side (0.05%) |
| `--slippage-pct` | `0.001` | Slippage fraction of next-bar open (0.1%) |
| `--mtf-intervals` | `1d` | Comma-separated higher-timeframe intervals; empty string disables them |
| `--mtf-sma` | `10` | Higher-timeframe SMA length |
| `--buy-drop` | `0.10` | BUY drawdown fraction (10%) |
| `--sell-rise` | `0.15` | SELL rise fraction (15%) |
| `--window-days` | `5` | Strategy lookback window |
| `--cooldown` | `6.0` | Adjacent-signal cooldown in hours |
| `--strategy` | `pair_band` | Strategy registered in the strategy registry |
| `--symbols` | all watchlist | Comma-separated filter of configured watchlist symbols |
| `--network` | configuration | Exchange network override |
| `--json` | false | Emit machine-readable JSON |

```bash
uv run onefill backtest run --days 30 --symbols BTC,ETH --json
```

Configure the watchlist and exchange market access first. Missing history is filled through the shared
candle store, so this command may fetch market data. Text output prints portfolio metrics and up to
50 trades; JSON contains `metrics` and `trades`. Unresolvable symbols are skipped; an empty selection
can complete with no trades. Configuration or uncaught data-loading errors exit non-zero.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | `ALL_FILLED` — every leg filled within tolerances |
| `1` | General error (bad args, unreachable venue, etc.) |
| `2` | `REJECTED` — plan or validation failed; no orders sent |
| `3` | `ROLLED_BACK` — partial fill, compensation succeeded; net exposure flat |
| `4` | `ROLLED_BACK_FAILED` — compensation failed; manual intervention required |

These let you script multi-step workflows with safe failure handling.
