# API Reference

Auto-generated API documentation from Python source docstrings (via [mkdocstrings](https://mkdocstrings.github.io/) with the Griffe handler).

## Packages

| Package | Description |
|---|---|
| [Coordinator](coordinator.md) | Core execution pipeline: Planner, Validator, Executor, Reconciler, Orchestrator |
| [Market](market.md) | Market abstraction layer: Asset, Instrument, InstrumentRegistry, Quote |
| [Persistence](persistence.md) | SQLite + JSONL dual persistence: PersistenceStore |
| [CLI](cli.md) | Typer CLI application and bootstrap wiring |
| [Core](core.md) | Shared exchange abstraction (BaseExchange) + legacy engines |
| [Exchanges](exchanges.md) | CCXT exchange adapter implementation |
| [Funding Arbitrage](funding-arb.md) | Cross-venue funding rate arbitrage strategy |
| [Observability](observability.md) | Metrics, logging, and telemetry |

## Conventions

- **Docstring style:** Google-style (Args, Returns, Raises)
- **Private members** (prefixed with `_`) are filtered out
- **Inherited members** are shown (useful for `CCXTExchange` → `BaseExchange`)
- **Source code** can be toggled inline via the `[source]` link on each symbol
- **Dataclass `__init__`** is merged into the class documentation
