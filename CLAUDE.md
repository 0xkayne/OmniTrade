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
python -m tests.unit.exchanges.test_paradex
python -m tests.unit.exchanges.test_lighter

# Skip slow tests
pytest -m "not slow"
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
- **`BaseExchange`** (`src/core/base_exchange.py`): Abstract base with unified interface — `connect()`, `fetch_balance()`, `fetch_orderbook()`, `create_order()`. Also handles mainnet/testnet switching via `switch_network()` and a shared `aiohttp.ClientSession`.
- **`ExchangeFactory`** (`src/core/exchange_factory.py`): Reads `config/exchanges.yaml`, selects `CCXTExchange` (type: `ccxt`) or `LighterExchange` (type: `native`) based on `type` field, calls `connect()` on each.
- **`CCXTExchange`** (`src/exchanges/ccxt_exchange.py`): Wraps `ccxt.async_support`. Has special-cased config builders for `hyperliquid` and `paradex` (wallet address, private key, testnet flag). All exchanges default to `swap` (perpetual) market type.
- **`LighterExchange`** (`src/exchanges/lighter_exchange.py`): Uses the `lighter-sdk` installed from GitHub. Supports full trading: orderbook, balance, positions, limit/market orders, leverage, close-all, fund transfers.

### Engine Layer (`src/core/`)
- **`VolumeEngine`** (`src/core/volume_engine.py`): Core of the volume farming workflow. Manages `HedgePosition` dataclass lifecycle (open → monitor → close). Implements: lognormal position sizing, random timing (anti-sybil), spread profitability check, concurrent position limits, daily volume cap, and auto-close smallest positions when margin is low.
- **`ArbitrageEngine`** (`src/core/arbitrage_engine.py`): Async concurrent orderbook fetching across exchanges, calculates bi-directional spread for all exchange pairs, returns opportunities above a threshold.

### Strategy Layer (`src/strategies/`)
- **`HedgeVolumeStrategy`** (`src/strategies/hedge_volume.py`): Higher-level decisions on top of `VolumeEngine` — target selection by priority/completion, optimal position sizing, intelligent close decisions, progress tracking per symbol.
- **`SpreadArbitrageStrategy`** (`src/strategies/spread_arbitrage.py`): Spread calculation, volume sizing, risk budget, and balance validation for arbitrage.

### Entry Point (`src/main.py`)
`TradeBot` orchestrates everything: loads YAML configs, calls `ExchangeFactory`, initializes engines/strategies, runs async tasks via `asyncio.gather()`. Uses a file-based process lock (`/tmp/arbitrage_bot_{mode}.lock`) to prevent duplicate instances. Handles `SIGINT`/`SIGTERM` for graceful shutdown (closes all positions, then disconnects exchanges).

## Configuration

- **`config/exchanges.yaml`**: Exchange enable/disable, type (`ccxt`/`native`), mainnet/testnet URLs, supported symbols, fee rates. Fee rates are critical for profitability calculations in volume farming.
- **`config/volume_farming.yaml`**: All volume farming parameters — timing randomization, position size range (USD), leverage, risk limits (`min_profit_threshold`, `min_fund_balance`, `daily_max_volume`), and per-symbol targets.
- **`config/secrets.yaml`**: API credentials (gitignored). Copy from `secrets.example.yaml`. Lighter requires per-network (`testnet`/`mainnet`) credentials including `wallet_address`, `api_private_key`, `api_key_index`, `account_index`.

## Key Design Patterns

- **All exchange I/O is async** (`asyncio` + `aiohttp`). Use `await` throughout; never call blocking I/O in the event loop.
- **Adding a new exchange**: Subclass `BaseExchange`, implement the abstract methods, add it to `ExchangeFactory.create_exchange()` with `type: native`, and add its config to `exchanges.yaml`.
- **Adding a CCXT exchange**: Just add an entry in `exchanges.yaml` with `type: ccxt`. If it needs special auth config, add a branch in `CCXTExchange._build_ccxt_config()`.
- **Symbol format differences**: Paradex uses `BTC/USD:USDC`, Hyperliquid uses `BTC/USDC:USDC`. Cross-exchange strategies must normalize or map symbols.
- **Testnet first**: All exchanges default to `testnet` in config. Use `--network mainnet` only after thorough testnet validation.
