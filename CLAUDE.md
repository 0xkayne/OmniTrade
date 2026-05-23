# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OmniTrade is a Python-based multi-exchange trading bot for Perpetual DEXes, focused on two goals:
1. **Volume farming** — hedge positions across exchanges to earn airdrop points with anti-sybil mechanisms
2. **Spread arbitrage** — detect and (conceptually) exploit price differences across Perp DEXes

## Commands

### Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.example.yaml config/secrets.yaml
# Edit config/secrets.yaml with your API keys/private keys
```

### Run
```bash
# Volume farming on testnet (most common)
python -m src.main --mode volume --network testnet

# Arbitrage monitoring
python -m src.main --mode arbitrage --network testnet

# Both modes simultaneously
python -m src.main --mode both --network testnet

# Switch to mainnet
python -m src.main --mode volume --network mainnet

# Stop: Ctrl+C (triggers graceful shutdown and closes all positions)
```

### Tests
```bash
# All tests
pytest

# Unit tests only
pytest tests/unit -vv

# Single exchange test (runs as script with real network calls)
python -m tests.unit.exchanges.test_hyperliquid
python -m tests.unit.exchanges.test_lighter

# Skip slow tests
pytest -m "not slow"
pytest -m unit
```

## Architecture

### Layer Structure

```
Config (YAML) → ExchangeFactory → BaseExchange subclasses
                                        ↓
                              CCXTExchange | LighterExchange
                                        ↓
                         ArbitrageEngine | VolumeEngine
                                        ↓
                    SpreadArbitrageStrategy | HedgeVolumeStrategy
                                        ↓
                                  TradeBot (main.py)
```

### Exchange Layer (`src/core/` + `src/exchanges/`)
- **`BaseExchange`** (`src/core/base_exchange.py`): Abstract base with unified interface — `connect()`, `fetch_balance()`, `fetch_orderbook()`, `create_order()`, `connect_websocket()`, `subscribe_orderbook()`. Also handles mainnet/testnet switching via `NetworkType` enum and a shared `aiohttp.ClientSession`.
- **`ExchangeFactory`** (`src/core/exchange_factory.py`): Reads `config/exchanges.yaml`, selects `CCXTExchange` (type: `ccxt`) or `LighterExchange` (type: `native`) based on `type` field, skips `enabled: false` entries, applies `target_network` override, then calls `connect()` on each.
- **`CCXTExchange`** (`src/exchanges/ccxt_exchange.py`): Wraps `ccxt.async_support` for Hyperliquid, Binance, etc. Has a special-cased config branch for `hyperliquid` (wallet address, private key, testnet flag, HIP3 market filtering). All exchanges default to `swap` (perpetual) market type.
- **`LighterExchange`** (`src/exchanges/lighter_exchange.py`): Uses the `lighter-sdk` installed from GitHub (`elliottech/lighter-python`). Supports full trading: orderbook, balance, positions, limit/market orders, leverage, close-all, fund transfers.

### Engine Layer (`src/core/`)
- **`VolumeEngine`** (`src/core/volume_engine.py`): Core of the volume farming workflow. Manages `HedgePosition` dataclass lifecycle (open → monitor → close). Implements: lognormal position sizing, random timing (anti-sybil), spread profitability check, concurrent position limits, daily volume cap, and auto-close smallest positions when margin is low.
- **`ArbitrageEngine`** (`src/core/arbitrage_engine.py`): Async concurrent orderbook fetching across exchanges, calculates bi-directional spread for all exchange pairs, returns opportunities above a threshold.

### Strategy Layer (`src/strategies/`)
- **`HedgeVolumeStrategy`** (`src/strategies/hedge_volume.py`): Higher-level decisions on top of `VolumeEngine` — target selection by priority/completion, optimal position sizing, intelligent close decisions, progress tracking per symbol.
- **`SpreadArbitrageStrategy`** (`src/strategies/spread_arbitrage.py`): Spread calculation, volume sizing, risk budget, and balance validation for arbitrage.

### Entry Point (`src/main.py`)
`TradeBot` orchestrates everything: loads YAML configs, calls `ExchangeFactory`, initializes engines/strategies, runs async tasks via `asyncio.gather()`. Three modes via `--mode`:
- `arbitrage` — runs `ArbitrageEngine` polling loop (100ms interval)
- `volume` — runs `VolumeEngine` + `HedgeVolumeStrategy` + stats reporter (every 5 min)
- `both` — runs all tasks concurrently

Uses a file-based process lock (`/tmp/arbitrage_bot_{mode}.lock`) to prevent duplicate instances. Handles `SIGINT`/`SIGTERM` for graceful shutdown (closes all positions, then disconnects exchanges).

## Configuration

- **`config/exchanges.yaml`**: Exchange enable/disable, type (`ccxt`/`native`), mainnet/testnet URLs, supported symbols, fee rates. Fee rates are critical for the `min_profit_threshold` calculation — set them to match your actual VIP tier.
- **`config/volume_farming.yaml`**: All volume farming parameters — timing randomization, position size range (USD), leverage, risk limits (`min_profit_threshold`, `min_fund_balance`, `daily_max_volume`), and per-symbol targets.
- **`config/secrets.yaml`**: API credentials (gitignored, never commit). Copy from `secrets.example.yaml`.

**Secrets schema is not uniform:** Lighter splits credentials by network (`lighter.testnet.*` and `lighter.mainnet.*` with `wallet_address`, `api_private_key`, `api_key_index`, `account_index`), while Hyperliquid uses a single flat block (`walletAddress` / `privateKey`). Code that loads secrets must branch on exchange name — don't assume one shape.

## Margin Safety Guard

Before every open, `VolumeEngine` checks free margin on both legs:
- On startup, each exchange balance must be ≥ `min_fund_balance`, or the bot refuses to launch
- If margin is insufficient mid-run, it waits 5 minutes and retries (up to 3 times), then **auto-closes the lowest-cost active position** to free margin

When modifying the open-position path, don't bypass this guard — it's the primary defense against liquidation cascades.

## Volume Accounting Units

`daily_max_volume`, `daily_target_volume`, and the cumulative volume in stats reports are all denominated in **USD notional value** (`entry_price × size`), not coin count. This was a corrected behavior — keep it consistent when touching `VolumeEngine.stats` or `HedgeVolumeStrategy.targets`.

## Key Design Patterns

- **All exchange I/O is async** (`asyncio` + `aiohttp`). Use `await` throughout; never call blocking I/O in the event loop.
- **Adding a new exchange**: Subclass `BaseExchange`, implement the abstract methods, add it to `ExchangeFactory.create_exchange()` with `type: native`, and add its config to `exchanges.yaml`.
- **Adding a CCXT exchange**: Just add an entry in `exchanges.yaml` with `type: ccxt`. If it needs special auth config, add a branch in `CCXTExchange._build_ccxt_config()`.
- **Testnet first**: All exchanges default to `testnet` in config. Use `--network mainnet` only after thorough testnet validation.

See `EXCHANGE_INTEGRATION_GUIDE.md` for detailed integration instructions. See `VOLUME_FARMING_GUIDE.md` for end-to-end volume farming setup including margin/fee tuning.
