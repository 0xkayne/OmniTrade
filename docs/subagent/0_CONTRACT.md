# Subagent 0 — Contract Types + Project Skeleton

> **Runs on:** main branch (not a worktree)
> **Must complete before:** Subagents A, B, C are launched
> **Estimated LOC:** ~400 new, ~50 modified

## Purpose

Create the package directory layout and all **shared type definitions** (dataclass shells) that subagents A/B/C will code against. This is the "contract" — without it, every worktree would define its own versions of `Asset`, `Instrument`, `Quote`, `Intent`, `Plan`, etc., and merging would be hell.

**No behavior goes here.** Only data structures, method signatures (with `raise NotImplementedError` bodies), and `__init__.py` exports.

## What to build

### 1. Package directories

```
src/
  market/__init__.py
  coordinator/__init__.py
  cli/__init__.py
  persistence/__init__.py

tests/
  market/__init__.py
  coordinator/__init__.py
  cli/__init__.py
  persistence/__init__.py
```

All `__init__.py` files must `from .module import Class` so the public API is importable at the package level.

### 2. `src/market/asset.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Asset:
    symbol: str           # "BTC", "USDT"
    kind: str = "crypto"
```

Frozen + hashable. No methods beyond `__init__`.

### 3. `src/market/instrument.py`

```python
from dataclasses import dataclass
from typing import Literal
from .asset import Asset

@dataclass(frozen=True)
class Instrument:
    venue: str                                  # "binance"
    market_type: Literal["spot", "perp"]        # one Intent never mixes these
    base: Asset
    quote: Asset
    venue_symbol: str                           # native symbol on this venue, e.g. "BTCUSDT"
    min_qty: float = 0.0
    qty_step: float = 0.0
    price_step: float = 0.0
    taker_fee_rate: float = 0.0
    maker_fee_rate: float = 0.0
    contract_size: float = 1.0
    is_inverse: bool = False
    listing_status: str = "trading"   # trading / delisted / preopen

    @staticmethod
    def key(venue: str, market_type: str, base_symbol: str, quote_symbol: str) -> tuple:
        return (venue, market_type, base_symbol, quote_symbol)

    @property
    def instrument_key(self) -> tuple:
        """Unique key for this instrument."""
        return self.key(self.venue, self.market_type, self.base.symbol, self.quote.symbol)

    def round_qty(self, amount: float) -> float:
        """Snap amount to the nearest valid qty_step for this venue."""
        if self.qty_step == 0:
            return amount
        steps = round(amount / self.qty_step)
        return max(self.min_qty, steps * self.qty_step)

    def round_price(self, price: float) -> float:
        """Snap price to the nearest valid price_step for this venue."""
        if self.price_step == 0:
            return price
        return round(price / self.price_step) * self.price_step
```

### 4. `src/market/quote.py`

```python
from dataclasses import dataclass, field
from typing import Literal
from .instrument import Instrument

@dataclass
class EstimatedFill:
    avg_price: float
    slippage_pct: float          # vs mid_price, e.g. 0.08 meaning 0.08%
    depth_consumed_levels: int   # how many orderbook levels were eaten
    filled_fully: bool = True    # False if the book was too shallow to fill the full amount

@dataclass
class Quote:
    instrument: Instrument
    fetched_at: float          # time.time() unix timestamp
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    mid_price: float
    taker_fee_rate: float
    maker_fee_rate: float
    funding_rate: float | None = None
    next_funding_time: float | None = None   # unix timestamp
    open_interest: float | None = None

    _bids: list[tuple[float, float]] = field(default_factory=list, repr=False)
    _asks: list[tuple[float, float]] = field(default_factory=list, repr=False)

    def estimate_fill(self, amount_base: float, side: Literal["buy", "sell"]) -> EstimatedFill:
        """
        Walk the relevant side of the book, accumulate fills until `amount_base`
        is satisfied or the book is exhausted. Return weighted-average fill price
        and slippage vs self.mid_price.
        """
        raise NotImplementedError  # Subagent A implements this
```

### 5. `src/market/registry.py`

```python
class InstrumentRegistry:
    """A list of all known instruments loaded from every venue's markets API."""

    def __init__(self, ttl_hours: int = 24):
        self._ttl_hours = ttl_hours
        self._instruments: dict[tuple, "Instrument"] = {}   # keyed by instrument_key
        self._loaded_at: float | None = None

    # --- to be implemented by Subagent A ---

    async def load_all(self, exchanges: dict) -> None: raise NotImplementedError
    async def reload(self, venue: str, exchanges: dict) -> None: raise NotImplementedError

    def list_instruments(
        self, *, base: str = None, market_type: str = None, venue: str = None
    ) -> list["Instrument"]: raise NotImplementedError

    def find_one(
        self, *, base: str, venue: str, market_type: str, quote_preference: list[str],
    ) -> "Instrument | None": raise NotImplementedError

    def is_stale(self) -> bool: raise NotImplementedError

    @property
    def venue_count(self) -> int: return len({i.venue for i in self._instruments.values()})
    @property
    def instrument_count(self) -> int: return len(self._instruments)
```

### 6. `src/coordinator/intent.py`

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class Intent:
    intent_id: str                                           # uuid7 or ulid
    base: str                                                # "BTC"
    quote_preference: list[str]                              # ["USDT", "USDC"]
    product: Literal["spot", "perp"]
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    total_notional_usd: float                                # e.g. 1000.00
    split: dict[str, float]                                  # {"binance": 0.5, "hyperliquid": 0.5}
    leverage: int = 1
    limit_price: float | None = None
    max_slippage_pct: float | None = None
    max_fee_usd: float | None = None
    max_funding_rate_pct: float | None = None
    execute_timeout_seconds: int = 30
    created_at: str = ""  # ISO 8601, set by Orchestrator on submission

    def __post_init__(self):
        # Validate split sums to 1.0
        total = sum(self.split.values())
        if not (0.999 <= total <= 1.001):
            raise ValueError(f"Split ratios must sum to 1.0, got {total}")
        # Validate product is single-valued
        if self.product not in ("spot", "perp"):
            raise ValueError(f"product must be 'spot' or 'perp', got {self.product}")
        # Limit order must have a price
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        # Spot orders must have leverage=1
        if self.product == "spot" and self.leverage != 1:
            raise ValueError("leverage must be 1 for spot orders")
```

### 7. `src/coordinator/plan.py`

```python
from dataclasses import dataclass, field
from src.market.instrument import Instrument
from src.market.quote import EstimatedFill
from .intent import Intent

@dataclass
class PlannedLeg:
    venue: str
    instrument: Instrument
    quote_matched: str                    # which quote preference was selected
    planned_notional_usd: float
    planned_qty_base: float               # notional / mid_price, rounded to qty_step
    estimated_fill: EstimatedFill
    estimated_fee_usd: float
    funding_rate: float | None = None
    next_funding_time: float | None = None
    selection_log: list[dict] = field(default_factory=list)
    # selection_log entries: {"candidate": "BTC/USDT", "action": "selected", "reason": "matched preference[0]=USDT, balance OK"}
    # or {"candidate": "BTC/USDC", "action": "skipped", "reason": "account has no USDC balance"}

@dataclass
class Plan:
    intent: Intent
    legs: list[PlannedLeg]
    rejected_venues: list[tuple[str, str]]    # (venue_name, rejection_reason)
    aggregate_estimated_avg_price: float      # weighted by notional across legs
    aggregate_estimated_fee_usd: float
    is_acceptable: bool                       # all threshold checks passed
    rejection_reasons: list[str]              # human-readable explanations
```

### 8. `src/coordinator/state_machine.py`

```python
# Intent-level states (see PRD §7.1)
INTENT_STATES = [
    ("PENDING",       "Intent created, not yet processed"),
    ("VALIDATED",     "Passed validation, about to execute"),
    ("EXECUTING",     "Orders being sent/polled"),
    ("ALL_FILLED",    "All legs filled — terminal"),
    ("PARTIAL_FILLED","Some legs filled, some failed — entering reconciliation"),
    ("ROLLING_BACK",  "Reverse orders being sent for filled legs"),
    ("ROLLED_BACK",   "Compensation succeeded — terminal"),
    ("ROLLED_BACK_FAILED", "Compensation failed — terminal, blocks further Intents"),
    ("REJECTED",      "Plan or validation rejected before any orders — terminal"),
]

TERMINAL_STATES = {"ALL_FILLED", "ROLLED_BACK", "ROLLED_BACK_FAILED", "REJECTED"}
BLOCKING_STATE = "ROLLED_BACK_FAILED"   # also referred to as NEEDS_MANUAL

# Leg-level states
LEG_STATES = [
    ("PENDING_SEND",  "Leg created, not yet sent"),
    ("SENT",          "Order sent, awaiting fill"),
    ("FILLED",        "Fully filled"),
    ("PARTIAL_FILLED","Partially filled"),
    ("REJECTED",      "Order rejected by venue"),
    ("TIMEOUT",       "Fill polling timed out"),
    ("CANCELLED",     "Canceled before fill"),
    ("COMPENSATING",  "Reverse order in flight to flatten this leg"),
    ("COMPENSATED",   "Reverse order filled"),
    ("COMPENSATION_FAILED", "Reverse order failed"),
]

def is_valid_transition(from_state: str, to_state: str) -> bool:
    "Check whether the state machine allows this transition."
    raise NotImplementedError  # Subagent C implements this
```

### 9. `src/persistence/schema.py`

```python
# SQLite table definitions as constants.
# Subagent B uses these to CREATE TABLE; CLI query commands use them for column names.

INTENTS_TABLE = """
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'PENDING',
    raw_intent_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

LEGS_TABLE = """
CREATE TABLE IF NOT EXISTS legs (
    leg_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL REFERENCES intents(intent_id),
    venue TEXT NOT NULL,
    instrument_venue_symbol TEXT NOT NULL,
    instrument_base TEXT NOT NULL,
    instrument_quote TEXT NOT NULL,
    instrument_market_type TEXT NOT NULL,
    quote_preference_matched TEXT,
    planned_notional_usd REAL NOT NULL,
    planned_qty_base REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_SEND',
    sent_at TEXT,
    order_id TEXT,
    filled_amount REAL,
    avg_price REAL,
    fee_usd REAL,
    error_msg TEXT,
    compensation_order_id TEXT,
    compensation_filled_amount REAL,
    instrument_selection_log TEXT,
    funding_rate_at_plan REAL,
    next_funding_time_at_plan REAL
)
"""

AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
)
"""
```

### 10. `BaseExchange.list_markets()` — new abstract method

Add to `src/core/base_exchange.py` (the **only** legacy file touched in Stage 0):

```python
@abstractmethod
async def list_markets(self) -> list["Instrument"]:
    """Return all tradable instruments on this exchange.

    Each Instrument must have venue_symbol set to the native symbol
    format for this exchange (e.g. "BTCUSDT" for Binance spot,
    "BTC/USDC:USDC" for Hyperliquid perp).
    """
    ...
```

Add `from src.market.instrument import Instrument` at the top of `base_exchange.py` (circular-safe — Instrument doesn't import base_exchange).

Both `CCXTExchange` and `LighterExchange` will get concrete implementations. For Stage 0, `CCXTExchange.list_markets()` can raise `NotImplementedError` (Subagent A tests with MockExchange; real implementation comes in Stage 3/E).

### 11. `pyproject.toml` additions

Add these to the existing `[project]`/`[project.optional-dependencies]` section (merge, don't replace anything):

```toml
[project]
dependencies = [
    # ... existing ...
    "typer>=0.15",
    "rich>=13.0",
    "aiosqlite>=0.20",
]

[project.optional-dependencies]
dev = [
    # ... existing ...
    "pytest-aiohttp",
]

[project.scripts]
onefill = "src.cli.main:app"
```

### 12. `src/cli/main.py` — CLI stub

```python
"""oneFill CLI — multi-venue coordinated order execution."""

import typer

app = typer.Typer(
    name="onefill",
    help="Multi-venue coordinated order execution.",
    no_args_is_help=True,
)


@app.command()
def order(
    base: str = typer.Option(..., help="Base asset, e.g. BTC"),
    quote_preference: str = typer.Option("USDT,USDC", help="Comma-separated quote preference"),
    product: str = typer.Option(..., help="spot or perp"),
    side: str = typer.Option(..., help="buy or sell"),
    order_type: str = typer.Option(..., help="market or limit"),
    total_notional_usd: float = typer.Option(..., help="Total notional in USD"),
    split: str = typer.Option(..., help="venue1=ratio,venue2=ratio, e.g. binance=0.5,hyperliquid=0.5"),
    leverage: int = typer.Option(1, help="Leverage (perp only)"),
    limit_price: float = typer.Option(None, help="Limit price (limit orders only)"),
    max_slippage_pct: float = typer.Option(None, help="Max slippage %"),
    max_fee_usd: float = typer.Option(None, help="Max total fee USD"),
    max_funding_rate_pct: float = typer.Option(None, help="Max funding rate % (perp)"),
    execute_timeout: int = typer.Option(30, help="Execute phase timeout seconds"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan + validate only, do not send orders"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="Output as machine-readable JSON"),
):
    """Submit a coordinated multi-venue order."""
    raise NotImplementedError("Stage 2+3")


@app.command()
def query(intent_id: str = typer.Argument(...)):
    """Query an intent by ID."""
    raise NotImplementedError("Stage 3")


@app.command()
def list_intents(
    status: str = typer.Option(None, "--status", help="Filter by status"),
):
    """List recent intents."""
    raise NotImplementedError("Stage 3")


@app.command()
def cancel(intent_id: str = typer.Argument(...)):
    """Cancel a non-terminal intent."""
    raise NotImplementedError("Stage 3")


@app.command()
def recover():
    """List NEEDS_MANUAL intents and guide resolution."""
    raise NotImplementedError("Stage 3")


@app.command()
def venues():
    """List configured venues and their connection status."""
    raise NotImplementedError("Stage 3")


if __name__ == "__main__":
    app()
```

### 13. `src/market/__init__.py` exports

```python
from .asset import Asset
from .instrument import Instrument
from .quote import Quote, EstimatedFill
from .registry import InstrumentRegistry

__all__ = ["Asset", "Instrument", "Quote", "EstimatedFill", "InstrumentRegistry"]
```

### 14. `src/coordinator/__init__.py` exports

```python
from .intent import Intent
from .plan import Plan, PlannedLeg
from .state_machine import INTENT_STATES, TERMINAL_STATES, BLOCKING_STATE, LEG_STATES

__all__ = ["Intent", "Plan", "PlannedLeg", "INTENT_STATES", "TERMINAL_STATES", "BLOCKING_STATE", "LEG_STATES"]
```

### 15. `src/persistence/__init__.py` exports

```python
from .schema import INTENTS_TABLE, LEGS_TABLE, AUDIT_TABLE

__all__ = ["INTENTS_TABLE", "LEGS_TABLE", "AUDIT_TABLE"]
```

## What NOT to do

- ❌ No method bodies with real logic — all behavior methods raise `NotImplementedError`
- ❌ No `MockExchange`, no test infrastructure
- ❌ No imports from ccxt/aiohttp (these are type definitions only)
- ❌ No `Planner`, `Validator`, `Executor`, `Reconciler`, `Orchestrator` classes
- ❌ No SQLite connection code — only the schema strings
- ❌ Do not touch `src/core/` or `src/exchanges/` (legacy code)

## Verification

After this step:

```bash
uv sync --extra dev                                   # installs new deps (typer, rich, aiosqlite)
uv run onefill --help                                 # shows 6 command stubs
uv run python -c "from src.market import Asset, Instrument, Quote; print('market ok')"
uv run python -c "from src.coordinator import Intent, Plan; print('coordinator ok')"
uv run python -c "from src.persistence import INTENTS_TABLE; print('persistence ok')"
uv run python -m src.main --help                      # legacy bot still works
uv run pytest                                         # no regressions
```

## Commit message

```
stage 0: oneFill contract types + project skeleton

Define shared dataclass types (Asset, Instrument, Quote, Intent, Plan,
PlannedLeg, state enums, SQLite schema strings) that subagents A/B/C
will code against. Add CLI stub (onefill command) via typer. Add deps:
typer, rich, aiosqlite, pytest-aiohttp.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```
