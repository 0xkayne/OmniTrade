# oneFill

> Multi-venue coordinated order execution. Submit one order, fan out across exchanges in parallel, get a guaranteed coordinated final state.

## What it is

Manually placing the same order on multiple exchanges takes 30+ seconds. In that window, prices move and partial failures leave you with unwanted directional exposure. **oneFill** compresses that window to milliseconds and handles the failure cases for you.

You submit a single CLI command — for example *"buy $1000 of BTC across Binance and Hyperliquid, 50/50 split, max slippage 0.3%"*. oneFill:

1. **Plans** — selects one `Instrument` per venue (BTC/USDT spot on Binance, BTC/USDC:USDC perp on Hyperliquid, etc.), fetches live quotes, and estimates per-leg price/slippage/fee.
2. **Validates** — checks listing status, balance, qty rules, leverage feasibility on each venue.
3. **Executes** — persists the plan to SQLite, then fans out all `create_order` calls via `asyncio.gather` (target: <50ms spread between request emissions).
4. **Reconciles** — if any leg fails or times out, sends reverse market orders to flatten any leg that did fill. If reconciliation itself fails, the intent enters `ROLLED_BACK_FAILED` (also called `NEEDS_MANUAL`) and blocks all further intents until a human resolves it.

oneFill is an **execution tool, not a strategy tool**. It does not decide *whether* to trade or *how much* — the user (or, in the future, a Claude Agent SDK agent) does. It executes the user's already-decided intent.

Terminal states: `ALL_FILLED`, `REJECTED`, `ROLLED_BACK`, `ROLLED_BACK_FAILED`.

## Status

**Stage 5 + Stage 6 landed** (Aug 2026). Stage 4 perp support complete — leverage, margin checks, funding rate fetching, reduce_only compensation. Stage 5 production hardening — structured JSON logging, metrics hooks, Agent SDK integration point, chaos-test crash-recovery validation. Stage 6 funding rate arbitrage — premium-index mean-reversion scanner + AutoArb daemon (`onefill arb`): see `docs/FUNDING_ARB_THEORY.md`. Full roadmap: [`docs/REFACTOR_PLAN.md`](docs/REFACTOR_PLAN.md).

- **Venues:** Binance (demo / mainnet, spot + perp) · Hyperliquid (testnet / mainnet, perp + spot)
- **Tests:** 337 non-network · 11 network (testnet credentials required)
- **CCXT surface:** full ccxt async API mirrored on `BaseExchange` / `CCXTExchange` (~240 methods) 
- **Detailed snapshot:** [`docs/STATUS.md`](docs/STATUS.md) · **Product spec:** [`docs/PRD.md`](docs/PRD.md) · **Architecture & invariants:** [`CLAUDE.md`](CLAUDE.md)

## Quick start

```bash
# 1. Install uv if you don't have it: https://docs.astral.sh/uv/
uv sync --extra dev

# 2. Configure credentials
cp config/secrets.example.yaml config/secrets.yaml
# Edit config/secrets.yaml with your Binance HMAC keys and/or Hyperliquid wallet

# 3. (Optional) Review risk guardrails
# Edit config/risk.yaml to adjust max notional, daily loss limit, rate limiting
```

```bash
# 4. Preview a coordinated order without sending it
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 100 \
  --split binance=0.5,hyperliquid=0.5 \
  --network testnet
```

```bash
# 5. Execute it for real (add --yes to skip the confirmation prompt)
uv run onefill order \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage-pct 0.3 \
  --network testnet
```

```bash
# 6. Per-leg overrides: buy spot on Binance, short perp on Hyperliquid with 3x leverage
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 500 \
  --split "binance=0.5:buy:spot,hyperliquid=0.5:sell:perp:3"
```

## CLI reference

The CLI is exposed as `onefill` (entry point: `src/cli/main.py:app`). Commands:

### `onefill order` — submit a coordinated intent

| Flag | Required | Default | Description |
|---|---|---|---|
| `--base` | yes | — | Base asset symbol, e.g. `BTC`, `ETH`, `SOL` |
| `--quote-preference` | no | `USDT,USDC` | Comma-separated list, tried in order when matching instruments |
| `--product` | yes | — | `spot` or `perp`. Default for all legs; individual legs can override via `--split` |
| `--side` | yes | — | `buy` or `sell`. Default for all legs; individual legs can override via `--split` |
| `--type` | yes | — | `market` or `limit` |
| `--total-notional-usd` | yes | — | Total intent size in USD |
| `--split` | yes | — | Venue weights, e.g. `binance=0.5,hyperliquid=0.5` (must sum to 1.0). Each leg can optionally override side, product, and/or leverage: `binance=0.5:buy:spot,hyperliquid=0.5:sell:perp:3` |
| `--leverage` | no | `1` | Leverage (perp only). Default for all legs; individual legs can override via `--split`. oneFill calls `set_leverage()` on the exchange before placing perp orders |
| `--limit-price` | no | — | Price for limit orders |
| `--max-slippage-pct` | no | — | Reject the plan if estimated slippage on any leg exceeds this. On Hyperliquid market orders, also passed to ccxt as the IOC limit-price tolerance; if unset, ccxt defaults to 5%. |
| `--max-fee-usd` | no | — | Reject the plan if total estimated fee exceeds this |
| `--max-funding-rate-pct` | no | — | Reject if perp funding rate exceeds this |
| `--execute-timeout` | no | `30` | Seconds before the executor times out and triggers reconciliation |
| `--time-in-force` | no | — | `GTC`, `IOC`, or `FOK`. Default: exchange default (usually GTC). |
| `--poll-interval-ms` | no | `500` | Cap for adaptive HTTP polling backoff (starts at 50ms, doubles each round up to this cap). |
| `--no-websocket` | no | — | Disable WebSocket fill watching; use HTTP polling only. |
| `--network` | no | `testnet` | `testnet` or `mainnet` |
| `--dry-run` | no | — | Plan + validate + risk-check only; do not send orders |
| `--yes` | no | — | Skip the interactive confirmation prompt |
| `--json` | no | — | Emit machine-readable JSON instead of rich terminal output |

### `onefill query <intent-id>`

Show the full state of a single intent: per-leg fills, fees, timestamps, status transitions.

```bash
uv run onefill query 7a3f9b2c-…
```

### `onefill list-intents [--status STATUS]`

List the 50 most recent intents, optionally filtered by status. Valid statuses include `PENDING`, `VALIDATED`, `EXECUTING`, `ALL_FILLED`, `REJECTED`, `ROLLED_BACK`, `ROLLED_BACK_FAILED`.

```bash
uv run onefill list-intents --status ROLLED_BACK_FAILED
```

### `onefill cancel <intent-id>`

Cancel a non-terminal intent in the store. Note: in the current MVP this does not cancel orders on the exchange itself if execution is already in flight — use exchange UIs for that.

### `onefill recover`

List intents stuck in `ROLLED_BACK_FAILED`, with suggested remediation. This state blocks all subsequent intents until resolved.

### `onefill venues`

Print configured venues from `config/exchanges.yaml`: type (ccxt / native), enabled flag, default network, supported symbols.

### `onefill instruments`

Browse the local instrument cache. oneFill persists every venue's trading pairs to SQLite on first run; subsequent starts load from cache (TTL 24h), avoiding repeated exchange API calls. Before executing an order, the cache is checked — if the requested pair doesn't exist on a venue, the order is rejected early with a clear message.

```bash
onefill instruments --base BTC              # all BTC pairs across venues
onefill instruments --venue binance         # all Binance pairs
onefill instruments --market perp           # perp only
onefill instruments --refresh               # force re-fetch from exchanges
onefill instruments --base BTC --json       # machine-readable output
```

The table shows venue, market type, base, quote, min notional, min qty, and listing status for each pair.

### `onefill ack <intent-id>`

Acknowledge a `ROLLED_BACK_FAILED` intent after manual review. Transitions the intent to `RESOLVED_MANUAL` and unblocks the system so new intents can be submitted.

### `onefill arb`

Funding rate arbitrage scanner / AutoArb daemon.

```bash
# One-shot spread scan across perp pairs
uv run onefill arb scan --base BTC

# Continuous: scan → auto-open hedged positions → auto-close when spreads narrow
uv run onefill arb run --base BTC --interval 60 --dry-run

# List open hedged arbitrage positions
uv run onefill arb positions

# Funding rate history for a base asset on a venue
uv run onefill arb history --base BTC --venue binance
```

Subcommands: `scan` (one-shot), `run` (AutoArb daemon: `--min-spread`, `--exit-spread`,
`--notional`, `--interval`, `--max-positions`, `--dry-run`), `positions`, `history`.
Theory and rationale: [`docs/FUNDING_ARB_THEORY.md`](docs/FUNDING_ARB_THEORY.md).

### `onefill watch`

Price-watch + Telegram-alert daemon for a personal, tagged watchlist. Signals are
**paired-band rotation** (buy-the-dip / sell-the-rip): per asset, once the price
falls `--buy-drop-pct` from the recent window high it emits a BUY signal (and
records that price as the assumed buy price); later, when the price rises
`--sell-rise-pct` above that buy price it emits a SELL signal. Each asset cycles
flat → buy → sell, so signal count ≈ number of bands (no spam).

```bash
# Backfill the candle window for every asset in config/watchlist.yaml
uv run onefill watch backfill --network mainnet

# Run the daemon: every 10 min, fetch Hyperliquid→Binance candles, evaluate the
# paired-band signals, alert via Telegram
uv run onefill watch run --network mainnet
```

Config: `config/watchlist.yaml` — each entry needs `symbol` + `tag` (a category label
shown in alerts); optional `market_type` (default `perp`), `quote_preference`.
Telegram `bot_token` / `chat_id` go in `config/secrets.yaml` → `telegram:`.
Alerts include the asset's tag. Data is read from Hyperliquid first, falling back to
Binance when the asset is absent there. Flags: `--interval` (default `600s`),
`--timeframe` (default `5m`), `--window-days` (default `5`),
`--buy-drop-pct` (default `0.10`), `--sell-rise-pct` (default `0.15`),
`--signal-cooldown-hours` (default `6`; min interval between adjacent buy/sell
signals per asset — also implies a min-hold so sub-cooldown noise round-trips are
suppressed), `--telegram-cmd-interval` (default `120`; how often the bot polls
for `/subscribe` commands), `--dry-run`, `--network`.

**Telegram subscribers**: notify is broadcast to the `chat_id`(s) in `secrets.yaml`
(which double as the whitelisted "master" ids) **plus** any dynamically-subscribed
chats. A master can add a chat by sending `/subscribe` (or `/start`) to the bot —
in a group it subscribes that whole group, in a DM it subscribes the master.
`/unsubscribe` (or `/stop`) removes it, `/status` reports the counts. Non-master
messages are ignored.

**Trade logging**: a whitelisted master can log a trade in a **private chat** by
sending `/log` (a group chat rejects it). Minimal positional form:
`/log BTC buy 0.01 64000 [venue] [tag] [fee] [strategy] [reason]` — only
`symbol side qty price` are required; bare `/log` returns the template. A `sell`
auto-matches the most recent unmatched buy for that symbol and computes pnl
(`(sell-买)×qty − fee`). Persisted to the `trades` table (same as `onefill
trades record`), visible in `list` / `export`.

### `onefill backtest`

Replay the paired-band rotation strategy on historical data with a shared-cash
portfolio — it uses the **same `evaluate_band` signal engine** as `onefill
watch run`, so backtest signals match live alerts.

```bash
uv run onefill backtest run --network mainnet \
  --symbols BTC,ETH,SOL --days 30 --timeframe 5m \
  --capital 10000 --per-trade-usd 2000 --fee-rate 0.0005 \
  --buy-drop 0.10 --sell-rise 0.15 --window-days 5 --cooldown 6
```

Outputs metrics (return %, win rate, profit factor, max drawdown, trade count,
avg win/loss) and per-fill records; `--json` for machine-readable. Without
`--symbols` it uses the whole watchlist (slower — fetching N days each).

### `onefill trades`

Manual trade log (per-order journal) for strategy analysis — hand-recorded, not
derived from oneFill orders.

```bash
# Record a single order (omit --symbol/--side/--qty/--price to be prompted)
uv run onefill trades record \
  --symbol BTC --side buy --qty 0.01 --price 60000 \
  --tag 龙头 --strategy "10%破位" --reason "..."
# venue / tag / fee / pnl / strategy / reason / note / ts are optional

# List / export
uv run onefill trades list [--tag 龙头] [--json]
uv run onefill trades export --format csv|json [--out trades.csv]
```

Storage: `trades` table in SQLite (`data/onefill.db`). `tag` mirrors the watchlist
category label; `strategy` / `reason` are free-form for later analysis.

### Strategy interface

`src/strategy/` defines a `Strategy` ABC (`on_bar(bar) -> Signal`) plus a
`registry` (name → class). `pair_band` is the built-in strategy (the rotation
signal). Both `onefill watch run` and `onefill backtest run` accept
`--strategy <name>` and drive a per-symbol strategy instance — so **backtest
signals equal live alerts**. To add a strategy, implement the ABC, register it in
`src/strategy/strategies/`, and it becomes usable for both warning and backtesting
(and can be compared against others).

### Exit codes

| Code | Meaning |
|---|---|
| `0` | `ALL_FILLED` — every leg filled within tolerances |
| `1` | General error (bad args, unreachable venue, etc.) |
| `2` | `REJECTED` — plan or validation failed; no orders sent |
| `3` | `ROLLED_BACK` — partial fill, compensation succeeded; net exposure flat |
| `4` | `ROLLED_BACK_FAILED` — compensation failed; manual intervention required |

These let you script multi-step workflows with safe failure handling.

## Risk controls

oneFill enforces pre-trade guardrails before any order reaches the exchange. Configure them in `config/risk.yaml`:

| Guard | Default | Description |
|---|---|---|
| `max_notional_per_intent` | `100000` | Reject any single intent above this USD notional |
| `daily_loss_limit_usd` | `10000` | Reject if cumulative filled-leg PnL today exceeds this loss |
| `max_venue_exposure_usd` | `50000` | Reject if any venue has too much filled-but-uncompensated notional |
| `rate_limit.max_orders` | `10` | Max intents per sliding window |
| `rate_limit.window_seconds` | `60` | Sliding window duration for rate limiting |

Set any value to `null` to disable that check. Risk failures appear in `--json` output as `risk_failures` and in the terminal as rejection reasons. The `RiskValidator` runs after Validate (balance / qty / listing checks) but before Executor (order dispatch), so a rejected risk check never sends an order.

Add `"risk_failures"` to your monitoring or scripts to catch risk rejections separately from validation failures.

## Architecture

```text
┌────────────────────────────────────────────────────────────────┐
│ CLI    (src/cli/)                                              │
│   onefill order / query / list-intents / cancel / recover /…   │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│ Coordinator  (src/coordinator/)                                │
│   Planner ──→ Validator ──→ Executor ──→ Reconciler            │
│        │           │            │             │                │
│        └───────────┴────────────┴─────────────┘                │
│                    state machine                               │
└────┬───────────────────┬───────────────────────┬───────────────┘
     │                   │                       │
┌────▼────────┐  ┌───────▼────────┐   ┌──────────▼─────────────┐
│ Market      │  │ Exchange       │   │ Persistence            │
│ (src/market)│  │ (src/exchanges)│   │ (src/persistence)      │
│ Asset       │  │ BaseExchange   │   │ SQLite (state machine) │
│ Instrument  │  │ CCXTExchange   │   │ JSONL (audit log)      │
│ Registry    │  │ MockExchange   │   │                        │
│ Quote       │  │                │   │                        │
└─────────────┘  └────────────────┘   └────────────────────────┘
```

- **Market layer** abstracts venue/quote/product differences. An `Asset` is "BTC"; an `Instrument` is `(venue, market_type, base, quote)` (e.g. BTC/USDT spot on Binance and BTC/USDC:USDC perp on Hyperliquid are different instruments). `Quote` is a point-in-time snapshot with depth-aware fill estimation.
- **Coordinator** is four independently-testable phases, plus a `RiskValidator` that runs between Validate and Execute. Planner and Validator have no side effects; Executor and Reconciler do. Fill confirmation uses WebSocket (`ccxt.watch_orders`) with automatic HTTP polling fallback; early termination exits the poll loop immediately when a leg fills and another definitively fails.
- **Persistence** writes every leg row to SQLite *before* the corresponding `create_order` is sent. JSONL is the append-only audit trail and can rebuild SQLite if needed. Instruments from every venue are cached in a local `instruments` table (TTL 24h) for fast startup and pre-flight validation.
- **Exchange layer** wraps ccxt async (`CCXTExchange` for Binance / Hyperliquid) and provides `MockExchange` as the canonical test double.

See [`CLAUDE.md`](CLAUDE.md) and [`docs/PRD.md`](docs/PRD.md) for the full design, invariants, and state machine.

## Configuration

Three YAML files:

- **`config/exchanges.yaml`** — per-venue enable flag, network URLs, fee schedule, symbols.
- **`config/risk.yaml`** — pre-trade guardrails: max notional, daily loss limit, venue exposure, rate limiting. See [Risk controls](#risk-controls) above.
- **`config/secrets.yaml`** — credentials (gitignored). Schema differs per venue:
  - **Binance:** `apiKey` + `secret` (HMAC). Ed25519 keys not supported by ccxt.
  - **Hyperliquid:** `walletAddress` + `privateKey` (Ethereum-style hex). Optional `vaultAddress`.

Switch a venue to its testnet by setting `default_network: testnet` in `exchanges.yaml`. For Binance, oneFill auto-enables ccxt's `enable_demo_trading(True)` when the network is testnet.

## Testing

```bash
uv run pytest -m "not network"   # 261 core tests, fully offline (MockExchange + :memory: SQLite)
uv run pytest -m network         # 9 network tests (requires real testnet credentials)
uv run pytest                    # everything

uv run ruff check .              # lint
uv run ruff format .             # format
```

## Legacy mode

This repo previously shipped an autonomous volume-farming and arbitrage-monitoring bot. That code still runs:

```bash
uv run python -m src.main --mode volume    --network testnet
uv run python -m src.main --mode arbitrage --network testnet
uv run python -m src.main --mode both      --network testnet
```

`VolumeEngine`, `ArbitrageEngine` and the `src/strategies/` modules are preserved. They will be phased out once oneFill reaches feature parity for the use cases that overlap. For the old README and the volume-farming guide, check the git history (`git log -- README.md`).

## Risk disclaimer

Cryptocurrency trading carries significant market and compliance risk. Validate strategies on testnet before using real funds. This project is for technical research and education; nothing here is investment advice.
