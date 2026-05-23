# Subagent A — Market Layer Implementation

> **Depends on:** Subagent 0 (contract types must be committed on main)
> **Runs in:** a git worktree branched from the post-Stage-0 commit
> **Parallel with:** Subagent B (Persistence) and Subagent C (Coordinator)
> **Estimated LOC:** ~700 new

## Purpose

Implement the Market layer behavior:
- `InstrumentRegistry` — load/store/filter instruments from venue market APIs
- `QuoteFetcher` — concurrently fetch orderbook snapshots for N instruments
- `Quote.estimate_fill()` — depth-walking fill estimator
- `MockExchange` — configurable test double implementing `BaseExchange`, used by all subagents' tests

The `Asset`, `Instrument`, `Quote`, and `InstrumentRegistry` dataclasses are already defined in `src/market/` by Stage 0. **You fill in the methods.**

## Files to create / modify

### NEW: `src/market/quote_fetcher.py`

```python
class QuoteFetcher:
    """
    Fetches real-time Quote snapshots for one or more Instruments.

    Uses the exchange adapter's fetch_orderbook() + any available fee/funding data.
    """
    def __init__(self, exchanges: dict[str, "BaseExchange"]):
        ...

    async def fetch(self, instrument: "Instrument", depth: int = 20) -> "Quote":
        """
        Fetch a single Quote for one instrument.
        1. Call exchange.fetch_orderbook(instrument.venue_symbol, depth)
        2. Parse bids/asks into Quote._bids / Quote._asks
        3. Extract top-of-book (bid_price, bid_size, ask_price, ask_size)
        4. Compute mid_price
        5. Return Quote with fetched_at = time.time()
        """
        ...

    async def fetch_many(self, instruments: list["Instrument"], depth: int = 20) -> list["Quote | None"]:
        """
        Fetch Quotes for multiple instruments concurrently via asyncio.gather.
        A single instrument failure must not fail the whole batch —
        return None in that slot (not skip — list length must match input).
        Log a warning per failed instrument.
        Returns list in same order as input.
        """
        ...
```

**Fee source:** `QuoteFetcher` reads fee rates from `instrument.taker_fee_rate` / `instrument.maker_fee_rate` (already populated in the Instrument). No API call needed for fees. The Quote's `taker_fee_rate` and `maker_fee_rate` fields are copied from the Instrument.

**Orderbook format handling:** Different exchanges return different dict shapes from `fetch_orderbook()`. ccxt returns `{"bids": [[price, qty], ...], "asks": [[price, qty], ...]}`. The QuoteFetcher normalises this into `_bids` and `_asks` as `list[tuple[float, float]]`. If the exchange returns an unexpected shape, log a warning and return `None`.

### NEW: `src/market/mock_backend.py`

A test double implementing `BaseExchange`'s full interface. Used by Subagents A, B, C, D for their tests.

```python
class MockExchange(BaseExchange):
    """
    Configurable mock exchange for testing.

    Usage:
        mock = MockExchange("mock", canned_orderbook=..., canned_markets=...)
        mock.set_orderbook("BTCUSDT", bids=[...], asks=[...])
        mock.set_balance("USDT", 50000.0)
        mock.set_markets([Instrument(...), Instrument(...)])
        mock.inject_order_error(Instrument(...), RuntimeError("rate limit"))
    """
    def __init__(self, name: str = "mock", ...): ...
    async def connect(self) -> None: ...
    async def fetch_orderbook(self, symbol: str, limit: int = 20) -> dict: ...
    async def fetch_balance(self, params=None) -> dict: ...
    async def create_order(self, symbol, order_type, side, amount, price=None, params=None) -> dict: ...
    async def cancel_order(self, order_id, symbol, params=None) -> bool: ...
    async def fetch_order(self, order_id, symbol, params=None) -> dict: ...
    async def list_markets(self) -> list["Instrument"]: ...
    async def close(self) -> None: ...
```

**`create_order` must support injection** — a way for test code to say "the next call to create_order for this symbol should return this canned result". This lets coordinator tests simulate fills, partial fills, rejections.

Key design decisions for MockExchange:
- Canned orderbooks are stored as dict[symbol] → dict with "bids" and "asks" keys (matching `fetch_orderbook` return type)
- `create_order` returns a dict with shape: `{"id": "mock-xxx", "symbol": ..., "side": ..., "amount": ..., "status": "closed", "filled": amount, "average": price, ...}`
- Support `inject_order_error(symbol, exception)` for simulating venue failures
- `list_markets()` returns whatever was set via `set_markets()`

### MODIFY: `src/market/quote.py` — implement `estimate_fill`

```python
def estimate_fill(self, amount_base: float, side: Literal["buy", "sell"]) -> "EstimatedFill":
    """
    Walk the orderbook on the relevant side, accumulating fills level by level
    until `amount_base` is fully consumed.

    For BUY: walk _asks (ascending price — lowest ask first)
    For SELL: walk _bids (descending price — highest bid first)

    Returns:
        EstimatedFill(avg_price=..., slippage_pct=..., depth_consumed_levels=...)

    If the book cannot fully fill the amount:
        Return EstimatedFill with avg_price based on whatever filled, and
        mark the result somehow (add a `filled_fully: bool` field to EstimatedFill).

    Edge cases to handle:
    - _bids or _asks is empty
    - amount is zero
    - amount exceeds total available depth
    """
    ...
```

**Also**: add `filled_fully: bool = True` to `EstimatedFill` dataclass.

### MODIFY: `src/market/registry.py` — implement methods

```python
async def load_all(self, exchanges: dict) -> None:
    """
    Call list_markets() on every exchange concurrently (asyncio.gather).
    Populate self._instruments dict keyed by instrument_key.
    Set self._loaded_at = time.time().
    Individual venue failures are logged but don't fail the whole load.
    """

async def reload(self, venue: str, exchanges: dict) -> None:
    """
    Reload a single venue's instruments. Remove old entries for this venue,
    load new ones, merge back.
    """

def list_instruments(self, *, base=None, market_type=None, venue=None) -> list["Instrument"]:
    """Filter by any combination of base symbol, market_type, venue. All filters optional."""

def find_one(self, *, base, venue, market_type, quote_preference) -> "Instrument | None":
    """
    List instruments matching (base, venue, market_type).
    Walk quote_preference in order. Return the first instrument whose
    quote symbol is in the preference list. Return None if none match.

    Example:
        find_one(base="BTC", venue="binance", market_type="spot",
                 quote_preference=["USDT", "USDC"])
        → Instrument(base=BTC, quote=USDT, ...) if BTC/USDT spot exists
        → Instrument(base=BTC, quote=USDC, ...) if no USDT but USDC exists
        → None if neither exists
    """

def is_stale(self) -> bool:
    """Return True if more than self._ttl_hours have passed since load_all()."""
```

### MODIFY: `src/market/__init__.py`

Add exports for `QuoteFetcher` and `MockExchange` after they're created.

## Tests to write (`tests/market/`)

Use TDD: write each test, watch it fail, then implement.

| Test file | What it covers |
|---|---|
| `test_asset.py` | `Asset` equality, hashing, frozen |
| `test_instrument.py` | `round_qty` (edge: zero step, fractional), `round_price`, `instrument_key` uniqueness |
| `test_quote.py` | `estimate_fill` happy path (buy walk asks, sell walk bids), partial fill, empty book, zero amount |
| `test_registry.py` | `list_instruments` filter combinations, `find_one` preference matching + fallback, stale detection, empty registry |
| `test_quote_fetcher.py` | `fetch` returns valid Quote, `fetch_many` concurrency (use MockExchange), one venue fails doesn't break batch |
| `test_mock_backend.py` | `MockExchange.create_order` returns canned result, orderbook injection, balance injection, error injection, `list_markets` |

## Verification

```bash
uv run pytest tests/market -v          # all green, >85% line coverage
uv run python -c "
from src.market.quote_fetcher import QuoteFetcher
from src.market.registry import InstrumentRegistry
from src.market.mock_backend import MockExchange
print('market layer ok')
"
```

## Commit message

```
stage 1: market layer implementation

Implement InstrumentRegistry (load/filter/find_one with quote preference),
QuoteFetcher (concurrent orderbook fetching), Quote.estimate_fill 
(depth-walking fill estimator), and MockExchange (configurable test double
for all BaseExchange operations). Full unit test suite.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Out of scope

- ❌ Real Binance or Hyperliquid implementations of `list_markets()` — Stage 3/E
- ❌ Funding rate fetching logic — the Quote fetcher populates funding fields if the exchange provides them, but perp-specific fetch logic is Stage 4
- ❌ Modifying `BaseExchange` — `list_markets()` abstract method is already in the contract (Stage 0). MockExchange implements it.
- ❌ CLI or entry points
