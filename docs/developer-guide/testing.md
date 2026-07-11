# Testing

oneFill has a comprehensive test suite with ~300 non-network tests and ~9 network tests.

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
├── market/                    # market layer tests (7 files)
│   ├── test_asset.py
│   ├── test_instrument.py
│   ├── test_mock_backend.py
│   ├── test_orderbook_cache.py
│   ├── test_quote.py
│   ├── test_quote_fetcher.py
│   └── test_registry.py
├── persistence/               # persistence layer tests (9 files)
│   ├── test_audit.py
│   ├── test_blocking.py
│   ├── test_concurrent.py
│   ├── test_daily_pnl.py
│   ├── test_intent_crud.py
│   ├── test_leg_crud.py
│   ├── test_state_transitions.py
│   └── test_store_init.py
├── coordinator/               # coordinator tests (13 files)
│   ├── test_account_type.py
│   ├── test_executor.py
│   ├── test_executor_partial.py
│   ├── test_intent.py
│   ├── test_orchestrator.py
│   ├── test_plan.py
│   ├── test_planner.py
│   ├── test_planner_perp.py
│   ├── test_reconciler.py
│   ├── test_reconciler_perp.py
│   ├── test_risk.py
│   ├── test_state_machine.py
│   └── test_validator.py
├── cli/                       # CLI tests (4 files)
├── e2e/                       # end-to-end tests (2 files)
├── integration/               # integration tests (2 files)
├── strategy/                  # funding arbitrage tests (2 files)
├── utils/                     # utility tests
└── fixtures/                  # mock servers, sample data
```

## Running tests

```bash
# All tests
uv run pytest

# Non-network only (offline, fast)
uv run pytest -m "not network"

# Network tests (requires testnet credentials)
uv run pytest -m network

# Specific test file
uv run pytest tests/coordinator/test_executor.py -vv

# With coverage
uv run pytest --cov=src --cov-report=html
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
