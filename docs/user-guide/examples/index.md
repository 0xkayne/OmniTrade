---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
applies_to: onefill CLI and src/cli/agent_api.py
---

# Examples

Runnable examples for every user-facing capability, grouped by feature. All commands assume you
have completed [Quick Start](../getting_started/quickstart.md); credentials and limits live in the
files described under [Configuration](../configuration/index.md).

Add `--dry-run` to preview a flow without sending orders, and `--json` for machine-readable
output. Where a command trades real funds, the examples below use `--network testnet`.

## Coordinated execution

### Preview a split order

```bash
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 100 \
  --split binance=0.5,hyperliquid=0.5 \
  --network testnet
```

### Execute it with a slippage cap

```bash
uv run onefill order \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage-pct 0.3 \
  --network testnet --yes
```

`--yes` skips the interactive confirmation prompt.

### Mix products across legs

Each leg can override `side`, `product` and `leverage` through the extended `--split` syntax. This
buys spot on Binance and shorts perp on Hyperliquid at 3× in one intent:

```bash
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 500 \
  --split "binance=0.5:buy:spot,hyperliquid=0.5:sell:perp:3"
```

### Inspect the result

```bash
uv run onefill query 7a3f9b2c-…                          # per-leg fills, fees, transitions
uv run onefill list-intents --status ROLLED_BACK_FAILED  # 50 most recent, filtered
```

### Recover a blocked system

A `ROLLED_BACK_FAILED` intent (also called `NEEDS_MANUAL`) blocks every later intent until it is
acknowledged:

```bash
uv run onefill recover           # blocked intents + suggested remediation
uv run onefill ack <intent-id>   # after manual review, unblocks the system
```

## Venues and instruments

```bash
uv run onefill venues                       # configured venues, type, network, symbols
uv run onefill instruments --base BTC       # BTC pairs across venues
uv run onefill instruments --venue binance  # everything on one venue
uv run onefill instruments --market perp    # perp only
uv run onefill instruments --refresh        # re-fetch instead of using the 24h cache
```

## Funding rate arbitrage

```bash
uv run onefill arb scan --base BTC                        # one-shot spread scan
uv run onefill arb run --base BTC --interval 60 --dry-run # daemon, scan only
uv run onefill arb positions                              # open hedged positions
uv run onefill arb history --base BTC --venue binance     # historical snapshots
```

Drop `--dry-run` from `arb run` to let the daemon open and close hedged positions itself. The
model and its rationale are in
[Funding Arbitrage](../../developer-guide/design/funding-arbitrage.md).

## Price watch and alerts

```bash
uv run onefill watch backfill --network mainnet   # seed candles once, then exit
uv run onefill watch run --network mainnet        # daemon: refresh, evaluate, alert
```

The watchlist lives in `config/watchlist.yaml`; the Telegram `bot_token` and `chat_id` go in
`config/secrets.yaml`. `watch run` also seeds at startup, so running `watch backfill` first is
optional — it only makes that startup seed an incremental top-up.

## Backtesting

```bash
uv run onefill backtest run --network mainnet \
  --symbols BTC,ETH,SOL --days 30 --timeframe 5m \
  --capital 10000 --per-trade-usd 2000 --fee-rate 0.0005 \
  --buy-drop 0.10 --sell-rise 0.15 --window-days 5 --cooldown 6
```

`backtest run` replays the same signal engine `watch run` uses, so backtest signals match live
alerts. Without `--symbols` it backtests the whole watchlist, which is slower.

## Trade log

A hand-recorded per-order journal, separate from the orders oneFill executes:

```bash
uv run onefill trades record --symbol BTC --side buy --qty 0.01 --price 60000 \
  --tag 龙头 --strategy "10%破位"

uv run onefill trades list --tag 龙头 --json
uv run onefill trades export --format csv --out trades.csv
```

## Calling the same flows from Python

Every capability above has a Python entry point via
[`submit_intent_from_dict`](../api/index.md) for the execution path; the strategy, watch and
backtest flows are driven by `src/cli/bootstrap.py` helpers documented in the
[API Reference](../../developer-guide/reference/api/cli.md).
