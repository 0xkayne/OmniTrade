---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-11
applies_to: src/exchange/base.py and src/exchange/
---

# Exchange Layer

The Exchange layer provides a uniform interface to all trading venues. It is the only layer that talks directly to exchange APIs.

## Layer map

<figure markdown="span">
  <img src="../../../assets/base-exchange-layer.svg" alt="base-exchange-layer" width="100%">
</figure>

（图源码 `docs/assets/base-exchange-layer.dot`，重新生成：`scripts/render_diagrams.sh base-exchange-layer`）

**`exchange → market` 是本层唯一允许的「向上」依赖，方向固定。** 适配器的职责正是把 venue 的
原始市场数据**构造成领域对象**（`CCXTExchange.list_markets()` 产出 `Instrument`），这是端口-适配器方向；
反向的 `market → exchange` 一律禁止。`MockExchange` 留在 `src/` 内而非 `tests/`，是因为它实现
`BaseExchange` 的完整接口，必须与该接口同处一地才能在接口变化时立刻失效。

## BaseExchange

**File:** `src/exchange/base.py` (996 lines)

The abstract base class that all exchange adapters must implement. It defines:

- **`NetworkType` enum** — `MAINNET` and `TESTNET`
- **Shared `aiohttp` session** — one session per exchange, managed by `BaseExchange.__init__`
- **Balance caching** — TTL-based in-memory cache to avoid excessive `fetch_balance` calls
- **Fee rate lookup** — reads `config/exchanges.yaml` fees section
- **~240 ccxt method stubs** — all defaulting to `NotImplementedError`, so `CCXTExchange` can delegate any ccxt method through `getattr`

### Abstract methods

Every exchange adapter must implement:

```python
async def connect(self) -> None: ...                    # load markets, authenticate
async def close(self) -> None: ...                      # close session, cleanup
async def list_markets(self) -> list[Instrument]: ...   # discover tradable instruments
async def fetch_balance(self) -> dict: ...              # account balances
async def fetch_orderbook(self, symbol, depth=20) -> dict: ...
async def create_order(self, symbol, type, side, amount, price=None, params=None) -> dict: ...
async def cancel_order(self, order_id, symbol, params=None) -> dict: ...
async def fetch_order(self, order_id, symbol) -> dict: ...
```

### Network switching

`NetworkType` controls mainnet vs testnet URLs. Exchange configs in `exchanges.yaml` define both endpoints:

```yaml
binance:
  networks:
    mainnet:
      rest_base_url: "https://api.binance.com"
      websocket_url: "wss://stream.binance.com:9443"
      testnet:
        # Binance demo mode uses the standard API host.
        rest_base_url: "https://api.binance.com"
        websocket_url: "wss://stream.binance.com:9443/ws"
```

The `target_network` parameter (from `--network` CLI flag or `default_network` config) selects which endpoint to use.

## CCXTExchange

**File:** `src/exchange/ccxt.py` (1238 lines)

Wraps the `ccxt.async_support` library. Currently the primary adapter for both Binance and Hyperliquid.

### Key behaviors

- **Dynamic method delegation:** `__getattr__` routes any non-implemented method to the underlying ccxt exchange instance, so all ~240 ccxt methods are available without explicit stubs.
- **Config assembly:** `_build_ccxt_config()` merges YAML config, network settings, and secrets into the ccxt exchange constructor options.
- **Market loading:** `connect()` calls `exchange.load_markets()` with venue-specific options (e.g., `fetchMarkets: ['spot']` for Binance).
- **`list_markets()`:** converts ccxt market dicts to `Instrument` dataclass objects.

### Binance specifics

- **Demo trading:** When `network_type == TESTNET`, calls `exchange.enable_demo_trading(True)` after construction, before `load_markets()`. This swaps `urls.api` → demo-api.binance.com.
- **Auth:** HMAC (`apiKey` + `secret`). Ed25519 keys are not supported by ccxt.
- **Market types:** `spot` and `perp`.

### Hyperliquid specifics

- **Testnet:** Sets `options['testnet'] = True` in config.
- **Auth:** `walletAddress` + `privateKey` (Ethereum-style hex). Optional `vaultAddress`.
- **HIP3 filtering:** Disabled by default (`filterHip3Markets: false` in config).
- **Market order prices:** Hyperliquid requires a limit price for market orders; ccxt derives a price from the order book with a slippage tolerance (default 5%, overridable via `--max-slippage-pct`).

## ExchangeFactory

**File:** `src/exchange/factory.py`

```python
class ExchangeFactory:
    @staticmethod
    def create_exchange(name, config, secrets) -> BaseExchange:
        """Map config['type'] to adapter class."""
        ...

    @staticmethod
    async def initialize_exchanges(config_path, secrets_path, target_network=None):
        """Read exchanges.yaml, skip disabled, connect all enabled exchanges."""
        ...
```

Currently maps `type: "ccxt"` → `CCXTExchange`. No native SDK adapter is part of the current implementation.

## Adding a new venue

See the [Exchange Integration Guide](base-exchange-integration.md) for a detailed walkthrough. The high-level steps are:

1. Add the venue to `config/exchanges.yaml` with network endpoints and fees
2. Add credentials to `config/secrets.yaml`
3. If using ccxt: update `_build_ccxt_config()` with any venue-specific options
4. Add the new adapter class to `ExchangeFactory.create_exchange()` when CCXT cannot provide the required surface
5. Implement tests using a `MockExchange`-based approach

## WebSocket support

`CCXTExchange` supports `ccxt.pro` WebSocket streams for:
- Order book updates (`watch_order_book`)
- Order status tracking (`watch_orders`)

### OrderbookCache

```python
class OrderbookCache:
    async def start(self, instruments_by_venue) -> None: ...  # subscribe WS streams
    def get_quote(self, instrument) -> Quote | None: ...       # latest cached quote
    async def close(self) -> None: ...                         # unsubscribe + disconnect
```

Creates its own `ccxt.pro` instances (one per venue per market type), separate from the
REST instances used for order placement, and maintains the latest bid/ask per subscribed
instrument. The Executor uses it for fill confirmation; the Planner fetches REST quotes
instead because it needs depth for `estimate_fill()`.

It lives here, not in `market/`, because it is venue I/O — it builds exchange clients.

### MockExchange

`mock.py` is the **canonical test double** for every test:

- Configurable order books, balances, markets, listing statuses
- Fault injection: `set_fail_create()`, `inject_order_error()`, `set_fail_fetch()`
- Funding rate and max leverage configuration
- `get_order()` for fill simulation

**Production code must not import it.** It sits next to `BaseExchange` rather than under
`tests/` so that a change to the interface breaks it immediately.
