# Legacy Mode

This repository previously shipped an autonomous volume-farming and arbitrage-monitoring bot. That code still runs alongside the oneFill engine during the transition period.

## What the legacy bot does

The legacy bot (`TradeBot` in `src/main.py`) has two modes:

### Volume mode — `python -m src.main --mode volume`

Autonomous volume farming using a hedge-based strategy:

1. **Selects symbols** based on target completion rates and priority
2. **Opens hedged positions** — simultaneously longs on one exchange and shorts on another, with spread-optimized direction selection
3. **Manages positions** — probabilistic close logic, emergency closes on hedge failures
4. **Enforces limits** — daily volume caps, concurrent position limits, fund checks

The volume engine (`src/core/volume_engine.py`, 1474 lines) is the largest single file in the project.

### Arbitrage mode — `python -m src.main --mode arbitrage`

Monitoring cross-exchange spreads by fetching order books and calculating arbitrage opportunities between all exchange pairs. Does not trade autonomously — it is a monitoring tool.

### Both mode — `python -m src.main --mode both`

Runs volume farming and arbitrage monitoring concurrently in separate asyncio tasks. Graceful shutdown (Ctrl+C) closes all open hedge positions.

## How it coexists with oneFill

```
┌──────────────────────────────┐
│         oneFill CLI           │  ← new entry point (src/cli/main.py)
│   uv run onefill order ...    │
└──────────┬───────────────────┘
           │ uses
┌──────────▼───────────────────┐
│  Shared lower layer          │
│  BaseExchange, CCXTExchange  │
│  ExchangeFactory             │
└──────────┬───────────────────┘
           │ also used by
┌──────────▼───────────────────┐
│      Legacy TradeBot          │  ← old entry point (src/main.py)
│   python -m src.main ...      │
└──────────────────────────────┘
```

Both entry points share the same `BaseExchange` and `CCXTExchange` implementations. The separation is at the orchestration layer:

| | oneFill | Legacy TradeBot |
|---|---|---|
| **Entry point** | `onefill` CLI (Typer) | `python -m src.main` |
| **Orchestrator** | `Orchestrator` (coordinator/) | `TradeBot` (main.py) |
| **Execution model** | User submits individual intents | Autonomous continuous loop |
| **State tracking** | SQLite + JSONL (persistence/) | In-memory + log files |
| **Strategies** | Funding rate arbitrage (strategy/) | Volume farming + spread (strategies/) |

## Legacy module map

| Module | Purpose |
|---|---|
| `src/main.py` | `TradeBot` entry point, asyncio event loop, signal handling |
| `src/core/volume_engine.py` | Hedge-based volume farming (1474 lines) |
| `src/core/arbitrage_engine.py` | Cross-exchange spread monitoring |
| `src/strategies/hedge_volume.py` | `HedgeVolumeStrategy` + `VolumeTarget` dataclass |
| `src/strategies/spread_arbitrage.py` | `SpreadArbitrageStrategy` |
| `src/utils/network_manager.py` | Network type switching for legacy bot |
| `config/volume_farming.yaml` | Volume farming parameters |

## Phase-out plan

The legacy bot will be phased out once oneFill reaches feature parity for overlapping use cases. Currently:

- **Order execution** — oneFill is superior (coordinated multi-venue, compensation logic)
- **Volume farming** — legacy only (no oneFill equivalent yet)
- **Arbitrage monitoring** — legacy, partially superseded by `onefill arb scan`
- **Funding rate arbitrage** — oneFill only (new Stage 6 feature)

## Running legacy commands

```bash
uv run python -m src.main --mode volume --network testnet
uv run python -m src.main --mode arbitrage --network testnet
uv run python -m src.main --mode both --network testnet
# Ctrl+C → graceful shutdown (closes all open hedge positions)
```

!!! warning
    The legacy bot has its own configuration and risk parameters in `config/volume_farming.yaml`. These are independent of oneFill's `config/risk.yaml`.
