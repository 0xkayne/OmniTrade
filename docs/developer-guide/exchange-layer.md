# Exchange Layer

The Exchange layer provides a uniform interface to all trading venues. It is the only layer that talks directly to exchange APIs.

## BaseExchange

**File:** `src/core/base_exchange.py` (960 lines)

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
      rest_base_url: "https://testnet.binance.vision"
      websocket_url: "wss://testnet.binance.vision"
```

The `target_network` parameter (from `--network` CLI flag or `default_network` config) selects which endpoint to use.

## CCXTExchange

**File:** `src/exchanges/ccxt_exchange.py` (1067 lines)

Wraps the `ccxt.async_support` library. Currently the primary adapter for both Binance and Hyperliquid.

### Key behaviors

- **Dynamic method delegation:** `__getattr__` routes any non-implemented method to the underlying ccxt exchange instance, so all ~240 ccxt methods are available without explicit stubs.
- **Config assembly:** `_build_ccxt_config()` merges YAML config, network settings, and secrets into the ccxt exchange constructor options.
- **Market loading:** `connect()` calls `exchange.load_markets()` with venue-specific options (e.g., `fetchMarkets: ['spot']` for Binance).
- **`list_markets()`:** converts ccxt market dicts to `Instrument` dataclass objects.

### Binance specifics

- **Demo trading:** When `network_type == TESTNET`, calls `exchange.enable_demo_trading(True)` after construction, before `load_markets()`. This swaps `urls.api` → demo-api.binance.com.
- **Auth:** HMAC (`apiKey` + `secret`). Ed25519 keys are not supported by ccxt.
- **Market types:** Spot and USDⓈ-M perpetual futures.

### Hyperliquid specifics

- **Testnet:** Sets `options['testnet'] = True` in config.
- **Auth:** `walletAddress` + `privateKey` (Ethereum-style hex). Optional `vaultAddress`.
- **HIP3 filtering:** Disabled by default (`filterHip3Markets: false` in config).
- **Market order prices:** Hyperliquid requires a limit price for market orders; ccxt derives a price from the order book with a slippage tolerance (default 5%, overridable via `--max-slippage-pct`).

## ExchangeFactory

**File:** `src/core/exchange_factory.py`

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

Currently maps `type: "ccxt"` → `CCXTExchange`. The `type: "native"` path (for Lighter) exists as a stub.

## Adding a new venue

See the [Exchange Integration Guide](../design-docs/exchange-integration-guide.md) (中文) for a detailed walkthrough. The high-level steps are:

1. Add the venue to `config/exchanges.yaml` with network endpoints and fees
2. Add credentials to `config/secrets.yaml`
3. If using ccxt: update `_build_ccxt_config()` with any venue-specific options
4. If using a native SDK: implement a new adapter class inheriting from `BaseExchange`
5. Add the new adapter class to `ExchangeFactory.create_exchange()`
6. Implement tests using a `MockExchange`-based approach

## WebSocket support

`CCXTExchange` supports `ccxt.pro` WebSocket streams for:
- Order book updates (`watch_order_book`)
- Order status tracking (`watch_orders`)

The `OrderbookCache` uses WebSocket as the primary data source, falling back to REST polling when WebSocket disconnects.
