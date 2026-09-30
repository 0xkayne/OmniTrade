---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-30
applies_to: src/market/
---

# Market Layer

The Market layer (`src/market/`) abstracts away venue-specific, quote-specific, and product-specific differences. It is the **only layer** that knows about venue-native symbols and normalized order book structures; raw protocol details remain inside each CCXT or native adapter.

## Layer map

<figure markdown="span">
  <img src="../../../assets/base-market-layer.svg" alt="base-market-layer" width="100%">
</figure>

（图源码 `docs/assets/base-market-layer.dot`，重新生成：`scripts/render_diagrams.sh base-market-layer`）

**方向是这张图的要点。** `exchange → market` 是合法的（适配器负责把 venue 原始数据构造成领域对象），
`market → exchange` 一律禁止——`QuoteFetcher` 只对交易所对象做鸭子类型调用，需要类型标注时
在本层声明 `Protocol`。这条规则由 `tests/test_architecture.py::test_only_allowed_dependency_edges_exist`
断言。`NetworkType` 定义在 `instrument.py` 而非 `exchange/`，正是解开曾经那个环的关键。

## Core concepts

### Asset

```python
@dataclass(frozen=True)
class Asset:
    symbol: str   # "BTC", "USDT", "ETH"
    kind: str = "crypto"
```

An `Asset` is a user-facing handle — the thing you want to trade. It is **not** bound to any venue or quote currency. Frozen (hashable) so it can be used as a dict key.

### Instrument

```python
@dataclass(frozen=True)
class Instrument:
    venue: str                      # "binance", "hyperliquid", "arcus"
    network: NetworkType            # TESTNET or MAINNET
    market_type: Literal["spot", "perp"]
    base: Asset                     # Asset("BTC")
    quote: Asset                    # Asset("USDT")
    venue_symbol: str               # native symbol; format is venue-specific
    min_qty: float = 0.0
    qty_step: float = 0.0
    price_step: float = 0.0
    min_notional: float = 0.0
    taker_fee_rate: float = 0.0
    maker_fee_rate: float = 0.0
    contract_size: float = 1.0      # native contract multiplier; semantics depend on is_inverse
    is_inverse: bool = False
    listing_status: str = "trading"
    max_leverage: float | None = None
    settlement_asset: Asset | None = None
    quantity_unit: Literal["base", "contracts"] = "base"
```

An `Instrument` is the system's **minimum tradable unit**, uniquely identified by the tuple
`(venue, network, market_type, venue_symbol)`. Base, quote, settlement asset, and quantity unit
are selection attributes carried by that venue-native identity.

Key methods:
- `round_qty(amount)` — round to `qty_step` precision
- `round_price(price)` — round to `price_step` precision
- `required_margin(notional_usd, leverage)` — compute margin for perp positions

`settlement_asset` stores the settlement currency; `quantity_unit` is `base` or `contracts`.
Native quantities determine venue rounding and fill completion. Inverse contract notional is
native contracts × contract size; its base equivalent depends on the execution price.
Market identity is `(venue, network, market_type, venue_symbol)`. Registry selection defaults
perpetuals to linear unless inverse is explicit, and can filter settlement assets. Cache loading
selects the adapter network and rebuilds insufficient legacy metadata.

### InstrumentRegistry

```python
class InstrumentRegistry:
    def __init__(self, ttl_hours: int = 24): ...
    async def load_all(self, exchanges, store=None) -> None: ...
    async def refresh(self, exchanges) -> None: ...
    def find_one(self, *, base, venue, market_type, quote_preference) -> Instrument | None: ...
    def list_instruments(self, *, base=None, market_type=None, venue=None) -> list[Instrument]: ...
    def is_stale(self) -> bool: ...
```

The registry is loaded at startup from each venue adapter's `list_markets()` API, whether that adapter uses CCXT or a native protocol. Results are cached in SQLite with a 24-hour TTL. On subsequent starts, instruments load from the local cache (fast) instead of hitting exchange APIs.

**`find_one()`** is the critical method — given a base asset, venue, market type, and ordered quote preferences, it returns the best-matching instrument. For example:

```python
# For "BTC spot on Binance, prefer USDT then USDC"
registry.find_one(
    base="BTC", venue="binance", market_type="spot",
    quote_preference=["USDT", "USDC"]
)
# → Instrument(venue="binance", base=Asset("BTC"), quote=Asset("USDT"), ...)
```

### Quote

```python
@dataclass
class Quote:
    instrument: Instrument
    fetched_at: float
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    mid_price: float
    taker_fee_rate: float
    maker_fee_rate: float
    funding_rate: float | None = None       # perp only
    next_funding_time: float | None = None  # perp only
    open_interest: float | None = None

    def estimate_fill(self, amount_base, side) -> EstimatedFill: ...
```

A `Quote` is a point-in-time snapshot of one instrument's top of book. The `estimate_fill()` method walks the order book depth to compute:

```python
@dataclass
class EstimatedFill:
    avg_price: float              # volume-weighted average price
    slippage_pct: float           # from mid-price
    depth_consumed_levels: int    # how deep into the book
    filled_fully: bool            # does size fit within available depth?
```

### QuoteFetcher

```python
class QuoteFetcher:
    def __init__(self, exchanges, cache=None): ...
    async def fetch(self, instrument, depth=20, *, enrich_funding=True) -> Quote: ...
    async def fetch_many(self, instruments, depth=20) -> list[Quote | None]: ...
```

Fetches real-time order book snapshots. `fetch_many()` uses `asyncio.gather` for concurrent fetching — a single failure returns `None` in that slot (list length preserved) rather than failing the entire batch.

When `enrich_funding=True` (default), perp quotes include the current funding rate and next funding timestamp from the exchange.

### FundingRateCache

```python
class FundingRateCache:
    def get(self, venue, symbol) -> dict | None: ...
    def all_rates(self) -> list[dict]: ...
    async def refresh(self, instruments) -> None: ...
```

Polled funding rate cache (TTL 60s). Used by the funding arbitrage scanner.
