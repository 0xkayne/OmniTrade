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

- **Venues:** Binance (demo / mainnet, spot + perp) · Hyperliquid (testnet / mainnet, perp + spot)
- **Tests:** 263 non-network · 9 network (testnet credentials required)
- **CCXT surface:** full ccxt async API mirrored on `BaseExchange` / `CCXTExchange` (~240 methods) for forward extensibility
- **Detailed snapshot:** [`docs/STATUS.md`](docs/STATUS.md) · **Product spec:** [`docs/PRD.md`](docs/PRD.md) · **Architecture & invariants:** [`CLAUDE.md`](CLAUDE.md)

## Quick start

```bash
# 1. Install uv if you don't have it: https://docs.astral.sh/uv/
uv sync --extra dev

# 2. Configure credentials
cp config/secrets.example.yaml config/secrets.yaml
# Edit config/secrets.yaml with your Binance HMAC keys and/or Hyperliquid wallet
```

```bash
# 3. Preview a coordinated order without sending it
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 100 \
  --split binance=0.5,hyperliquid=0.5
```

```bash
# 4. Execute it for real (add --yes to skip the confirmation prompt)
uv run onefill order \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage-pct 0.3
```

```bash
# 5. Per-leg overrides: buy spot on Binance, short perp on Hyperliquid with 3x leverage
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 500 \
  --split "binance=0.5:buy:spot,hyperliquid=0.5:sell:perp:3"
```

## CLI reference

The CLI is exposed as `onefill` (entry point: `src/cli/main.py:app`). Eight commands:

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
| `--dry-run` | no | — | Plan + validate only; do not send orders |
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

### Exit codes

| Code | Meaning |
|---|---|
| `0` | `ALL_FILLED` — every leg filled within tolerances |
| `1` | General error (bad args, unreachable venue, etc.) |
| `2` | `REJECTED` — plan or validation failed; no orders sent |
| `3` | `ROLLED_BACK` — partial fill, compensation succeeded; net exposure flat |
| `4` | `ROLLED_BACK_FAILED` — compensation failed; manual intervention required |

These let you script multi-step workflows with safe failure handling.

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
- **Coordinator** is four independently-testable phases. Planner and Validator have no side effects; Executor and Reconciler do.
- **Persistence** writes every leg row to SQLite *before* the corresponding `create_order` is sent. JSONL is the append-only audit trail and can rebuild SQLite if needed. Instruments from every venue are cached in a local `instruments` table (TTL 24h) for fast startup and pre-flight validation.
- **Exchange layer** wraps ccxt async (`CCXTExchange` for Binance / Hyperliquid) and provides `MockExchange` as the canonical test double.

See [`CLAUDE.md`](CLAUDE.md) and [`docs/PRD.md`](docs/PRD.md) for the full design, invariants, and state machine.

## Configuration

Two YAML files:

- **`config/exchanges.yaml`** — per-venue enable flag, network URLs, fee schedule, symbols.
- **`config/secrets.yaml`** — credentials (gitignored). Schema differs per venue:
  - **Binance:** `apiKey` + `secret` (HMAC). Ed25519 keys not supported by ccxt.
  - **Hyperliquid:** `walletAddress` + `privateKey` (Ethereum-style hex). Optional `vaultAddress`.

Switch a venue to its testnet by setting `default_network: testnet` in `exchanges.yaml`. For Binance, oneFill auto-enables ccxt's `enable_demo_trading(True)` when the network is testnet.

## Testing

```bash
uv run pytest -m "not network"   # 263 core tests, fully offline (MockExchange + :memory: SQLite)
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
