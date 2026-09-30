---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-30
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
原始市场数据**构造成领域对象**（CCXT 或 native adapter 的 `list_markets()` 产出 `Instrument`），这是端口-适配器方向；
反向的 `market → exchange` 一律禁止。`MockExchange` 留在 `src/` 内而非 `tests/`，是因为它实现
`BaseExchange` 的完整接口，必须与该接口同处一地才能在接口变化时立刻失效。

## BaseExchange

**File:** `src/exchange/base.py`

The abstract base class that all exchange adapters must implement. It defines:

- **`NetworkType` enum** — `MAINNET` and `TESTNET`
- **Shared `aiohttp` session** — one session per exchange, managed by `BaseExchange.__init__`
- **Balance caching** — TTL-based in-memory cache to avoid excessive `fetch_balance` calls
- **Fee rate lookup** — reads `config/exchanges.yaml` fees section
- **Optional protocol stubs** — the inherited methods fail closed with `NotImplementedError`; CCXT adapters may delegate CCXT methods, while native adapters implement only verified capabilities.

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

`NetworkType` binds an adapter to mainnet or testnet. Binance uses Demo Trading for testnet,
with per-product endpoints derived and checked by its adapter:

```yaml
binance:
  market_families: [spot, usdm]  # add coinm explicitly
  networks:
    mainnet: {}
    testnet: {}
```

Binance rejects in-place network switching: recreate the adapter through the network credential
loader. Changing metadata while retaining a live client or its keys is not a network switch.

The CLI boundary resolves network and credentials together in `src/cli/config.py`. Explicit
`target_network` takes precedence over each venue’s `default_network`, with `testnet` as the
final default. The loader reads `secrets.<network>.yaml` beside the exchange configuration,
validates its top-level `network`, and passes only the selected credentials to the factory.
Different venue defaults may select different files; there is no cross-network or legacy-file
fallback. `config/secrets.yaml` contains only shared credentials such as Telegram.

## Native adapters

`ArcusExchange` (`src/exchange/arcus.py`) is the first native adapter. It uses
Arcus REST endpoints, Ed25519 `ordersign` payloads and venue-specific field
mapping while returning `Instrument` and normalized order dictionaries. Public
market/account reads, REST order operations, reconnect, sequence-gap recovery,
deduplicated `userFills` events and client-order-ID reconciliation are
implemented. Live authenticated order behavior still requires Arcus-issued
Ed25519 credentials: webpage API Key maps to `api_key`, API Signing Key maps to `api_signing_key`,
and `master_wallet_address` identifies the authorizing master wallet. Arcus `api_key` is the raw Ed25519
public key, not the secp256k1-derived EVM address used as Hyperliquid `api_wallet_address`. Both DEXes
authorize independent API signers from a master account; their keys are not interchangeable. Old Arcus
`address` / `wallet_address` and key aliases are rejected. Protocol `address` / `ad` fields remain unchanged.
Account/fill reads and subscriptions are public and cannot establish signing permission.

`fetch_order_account`, `fetch_order_position` and `fetch_order_positions` provide typed collateral,
account-mode and signed native-position snapshots. Arcus validates account/market identity,
complete position responses and effective leverage even when the selected market is flat.
Missing or inconsistent account evidence raises an error instead of implying a zero position.

## CCXTExchange

**File:** `src/exchange/ccxt.py`

Wraps the `ccxt.async_support` library. It remains the general adapter for venues such as Hyperliquid. Binance uses a dedicated `BinanceExchange(BaseExchange)` with explicit product routing.

### Key behaviors

- **Explicit wrappers:** supported methods delegate to the underlying CCXT client; unsupported BaseExchange methods fail with `NotImplementedError`. Binance does not inherit these single-client wrappers.
- **Config assembly:** `_build_ccxt_config()` merges YAML config, network settings, and secrets into the ccxt exchange constructor options.
- **Market loading:** `connect()` calls `exchange.load_markets()` with venue-specific options. Binance market loading belongs to its dedicated product clients.
- **`list_markets()`:** converts ccxt market dicts to `Instrument` dataclass objects.

### BinanceExchange

`src/exchange/binance.py` owns fixed `spot/usdm/coinm` clients and routes Instrument-aware
orders, account snapshots and positions. `binance_clients.py` owns construction, endpoint checks
and shared request limits. No raw single-client alias is exposed. Account and position contracts
are `OrderAccountSnapshot` and `OrderPositionSnapshot` in `order.py`.

Client initialization failure never falls back to production or another family. Private WS and
public orderbook clients use the same family/network mapping. Markets exclude dated contracts.
See [Binance integration](base-binance-integration.md) for routing, units, account restrictions
and recovery, and [API reference](../../reference/binance-api-reference.md) for upstream endpoints.

### Hyperliquid specifics

- **Testnet:** Sets `options['testnet'] = True` in config.
- **Auth YAML:** `master_wallet_address` selects the funded query account. `api_wallet_address` and `api_wallet_private_key` must be supplied together, identify an approved API Wallet, match cryptographically, and differ from master. Both API fields may be empty for public reads. Old `walletAddress` / `wallet_address` / `privateKey` / `private_key` fields reject with a migration error; master-wallet signing is not accepted.
- **Internal mapping:** The adapter sets CCXT `walletAddress` from `master_wallet_address` and CCXT `privateKey` from `api_wallet_private_key`. API address is validated locally, while approval must exist on the selected network. Optional `vaultAddress` targets signed actions via CCXT options and alone does not redirect public reads. Public balance success does not verify signer permission. See [credentials](../../user-guide/configuration/credentials.md).
- **Signing network:** Hyperliquid L1 signatures use CCXT `options.sandboxMode`; the adapter pins it and `options.testnet` after merging user options so signing matches the selected network.
- **HIP3 filtering:** Disabled by default (`filterHip3Markets: false` in config).
- **Protected execution:** Coordinator sends explicit limit orders with a fixed protection price and IOC by default.
  `order_capabilities` declares IOC/GTC for Hyperliquid, and IOC/GTC/FOK for Binance. Unsupported capabilities reject.
  `submit_order` / `fetch_order_snapshot` use typed order contracts and preserve actual quantities, fee currencies and fills.
  Hyperliquid order queries omit the account `type` parameter so it cannot overwrite the `/info` request type.
- **Account and position validation:** Typed snapshots inspect account abstraction, complete clearinghouse
  positions and effective leverage. Unknown/stale state rejects. Venue exposure reads include the perpetual
  DEX catalog, with explicit market identity checks. Ordinary Coordinator perpetual execution still requires
  a one-way, single-asset account: unified, portfolio and DEX-abstraction modes are not enabled by these reads.
- **Account WS:** A CCXT Pro client supplies normalized order updates and user fills using the same network
  and account routing. `supports_user_fills` is enabled for Hyperliquid; connection or subscription alone is
  not execution evidence.

Hyperliquid `orderUpdates` and `userFills` subscriptions cover an entire account. The adapter deduplicates
the wire subscription by channel type and user, while preserving CCXT's per-symbol message hashes,
waiting futures and caches. Spot and perpetual consumers therefore reuse the account subscription
without consuming each other's messages. Server error frames are converted to exception objects before
rejecting waiting futures; a raw error string must not terminate the receive loop through a type error.
This compatibility handling is scoped to the locked CCXT Pro implementation and does not merge symbol
filters or replace its event routing with a shared consumer queue.

The [DEX testnet validation suite](../../user-guide/examples/dex-testnet-validation.md) checks real
account/order/WS evidence and database recovery behind explicit budgets. It exercises adapters and
low-level order management; it does not expand the ordinary Coordinator's supported account modes.

## ExchangeFactory

**File:** `src/exchange/factory.py`

```python
class ExchangeFactory:
    @staticmethod
    def create_exchange(name, config, secrets) -> BaseExchange:
        """Map config['type'] to adapter class."""
        ...

    @staticmethod
    async def initialize_exchanges(
        exchange_configs, secrets, target_network=None, *, fail_fast=False
    ):
        """Consume configuration dictionaries, skip disabled venues, and connect."""
        ...
```

The factory performs no configuration file I/O; entry points use
`load_exchange_configuration()` before calling it so the network and credentials agree.

The factory maps `type: "ccxt"` to `BinanceExchange` when `name == "binance"` and to
`CCXTExchange` for other CCXT venues. `type: "native"` plus an explicit `adapter` name maps to a
registered native adapter such as `ArcusExchange`. Unknown adapter names fail during configuration.
All adapters still pass through the same `BaseExchange` lifecycle.

## Adding a new venue

See the [Exchange Integration Guide](base-exchange-integration.md) for a detailed walkthrough. The high-level steps are:

1. Add the venue to `config/exchanges.yaml` with network endpoints and fees
2. Add credential placeholders to both network templates; keep real values in `config/secrets.testnet.yaml` / `config/secrets.mainnet.yaml`, each with its matching `network` marker
3. If using CCXT: update `_build_ccxt_config()` with venue-specific options; otherwise add a native adapter module with protocol-specific auth and conversions
4. Register the adapter in `ExchangeFactory` and add its configuration schema
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

Creates its own `ccxt.pro` instances (one per CCXT venue per market type), separate from the
REST instances used for order placement, and maintains the latest bid/ask per subscribed
instrument. The Executor uses it for fill confirmation; the Planner fetches REST quotes
instead because it needs depth for `estimate_fill()`.

It lives here, not in `market/`, because it is venue I/O. Native adapters may provide an equivalent venue-specific stream and need not use this cache.

### MockExchange

`mock.py` is the **canonical test double** for every test:

- Configurable order books, balances, markets, listing statuses
- Fault injection: `set_fail_create()`, `inject_order_error()`, `set_fail_fetch()`
- Funding rate and max leverage configuration
- `get_order()` for fill simulation

**Production code must not import it.** It sits next to `BaseExchange` rather than under
`tests/` so that a change to the interface breaks it immediately.
