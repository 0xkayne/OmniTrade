---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-06
applies_to: tests/ and pytest configuration
---

# Testing

当前测试套件收集 448 项，其中 11 项标记为 `network`；默认离线验证收集 437 项。

## Test structure

```
tests/
├── conftest.py                # shared fixtures
├── unit/                      # unit tests for shared components
│   ├── test_base_exchange.py
│   └── exchanges/
│       ├── test_binance.py
│       ├── test_ccxt_markets.py
│       └── test_hyperliquid.py
├── market/                    # market layer and quote tests
├── persistence/               # store, audit, blocking and schema tests
├── coordinator/               # planner, validator, execution and state tests
├── cli/                       # CLI parsing and command tests
├── e2e/                       # mock and network end-to-end tests
├── integration/               # REST and WebSocket integration tests
├── strategy/                  # funding arb, watch, candles, MTF, backtest and trade log
├── utils/                     # utility tests
└── fixtures/                  # mock servers, sample data
```

## Running tests

```bash
# All tests
uv run --locked pytest

# Non-network only (offline, fast)
uv run --locked pytest -m "not network"

# Network tests (requires testnet credentials)
uv run --locked pytest -m network

# Specific test file
uv run --locked pytest tests/coordinator/test_executor.py -vv

# With coverage
uv run --locked pytest --cov=src --cov-report=html
```

## Pytest configuration

From `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"          # async tests without @pytest.mark.asyncio
testpaths = ["tests"]
markers = [
    "slow",
    "integration",
    "websocket",
    "rest",
    "unit",
    "network",
    "mock",
]
```

Key markers:
- **`network`** — tests that need real exchange connectivity (deselect with `-m "not network"` for fast offline runs)
- **`slow`** — tests that take >1s

## MockExchange

`MockExchange` (`src/market/mock_backend.py`) is the canonical test double. It implements `BaseExchange` with configurable canned data:

```python
mock = MockExchange("mock")
mock.set_orderbook("BTCUSDT", bids=[(50000.0, 1.0)], asks=[(50010.0, 0.5)])
mock.set_balance("USDT", 50000.0)
mock.set_markets([Instrument(...), Instrument(...)])
mock.set_fail_create(True, message="rate limit")        # fault injection
mock.inject_order_error("BTCUSDT", RuntimeError("..."))
mock.set_funding_rate("BTCUSDT", 0.0001, 1700000000)
```

All coordinator, market, and persistence unit tests use `MockExchange` — no real network calls.

## Test patterns

### Testing Planner/Validator (pure phases)

Planner and Validator have no side effects, so they are tested with pure unit tests:

```python
async def test_planner_basic(registry, quote_fetcher, sample_intent):
    planner = Planner(registry, quote_fetcher)
    plan = await planner.plan(sample_intent)
    assert plan.is_acceptable
    assert len(plan.legs) == 2
```

### Testing Executor/Reconciler (side-effect phases)

Executor and Reconciler need `MockExchange` and an in-memory SQLite store:

```python
async def test_executor_persist_before_send(mock_exchange, store, sample_plan):
    executor = Executor({"mock": mock_exchange}, store)
    result = await executor.execute(sample_plan)
    # Verify legs were persisted BEFORE orders were sent
    legs = await store.get_legs_for_intent(sample_plan.intent.intent_id)
    assert len(legs) == len(sample_plan.legs)
```

### E2E pipeline tests

Full pipeline tests use `MockExchange` + in-memory SQLite:

```python
async def test_full_pipeline_success(mock_exchange, registry, store, sample_intent):
    orch = Orchestrator(registry, quote_fetcher, {"mock": mock_exchange}, store)
    result = await orch.submit(sample_intent)
    assert result["status"] == "ALL_FILLED"
```

## Test database

All persistence tests use in-memory SQLite (`:memory:`) with `PersistenceStore` — no filesystem dependency. The JSONL audit component is tested with temporary directories via pytest's `tmp_path` fixture.
