# Market Layer

The Market layer (`src/market/`) abstracts away venue-specific, quote-specific, and product-specific differences. It is the **only layer** that knows about venue-native symbols and order book structures.

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
    venue: str                      # "binance", "hyperliquid"
    network: NetworkType            # TESTNET or MAINNET
    market_type: Literal["spot", "perp"]
    base: Asset                     # Asset("BTC")
    quote: Asset                    # Asset("USDT")
    venue_symbol: str               # "BTC/USDT" on Binance, "BTC/USDC:USDC" on Hyperliquid
    min_qty: float = 0.0
    qty_step: float = 0.0
    price_step: float = 0.0
    min_notional: float = 0.0
    taker_fee_rate: float = 0.0
    maker_fee_rate: float = 0.0
    contract_size: float = 1.0      # >1 for inverse contracts
    is_inverse: bool = False
    listing_status: str = "trading"
    max_leverage: float | None = None
```

An `Instrument` is the system's **minimum tradable unit**, uniquely identified by the tuple `(venue, network, market_type, base.symbol, quote.symbol)`.

Key methods:
- `round_qty(amount)` — round to `qty_step` precision
- `round_price(price)` — round to `price_step` precision
- `required_margin(notional_usd, leverage)` — compute margin for perp positions

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

The registry is loaded at startup from each venue's `list_markets()` API. Results are cached in SQLite with a 24-hour TTL. On subsequent starts, instruments load from the local cache (fast) instead of hitting exchange APIs.

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

### OrderbookCache (WebSocket)

```python
class OrderbookCache:
    async def start(self, instruments_by_venue) -> None: ...  # subscribe WS streams
    def get_quote(self, instrument) -> Quote | None: ...       # latest cached quote
    async def close(self) -> None: ...                         # unsubscribe + disconnect
```

WebSocket-backed order book cache using `ccxt.pro.watch_order_book()`. Maintains the most recent bid/ask for each subscribed instrument. The cache is used by the Executor for fill confirmation; the Planner uses REST fetches for initial quotes since it needs depth for `estimate_fill()`.

### FundingRateCache

```python
class FundingRateCache:
    def get(self, venue, symbol) -> dict | None: ...
    def all_rates(self) -> list[dict]: ...
    async def refresh(self, instruments) -> None: ...
```

Polled funding rate cache (TTL 60s). Used by the funding arbitrage scanner.

### MockExchange

Located in `src/market/mock_backend.py` (despite being in the market package, it implements `BaseExchange`). This is the **canonical test double** for all tests. It provides:

- Configurable order books, balances, markets, listing statuses
- Fault injection: `set_fail_create()`, `inject_order_error()`, `set_fail_fetch()`
- Funding rate and max leverage configuration
- `get_order()` for fill simulation

Every unit test in `tests/coordinator/`, `tests/market/`, and `tests/persistence/` uses `MockExchange`.
