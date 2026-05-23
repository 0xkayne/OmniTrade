# Subagent C — Coordinator Pipeline Implementation

> **Depends on:** Subagent 0 (contract types must be committed on main)
> **Runs in:** a git worktree branched from the post-Stage-0 commit
> **Parallel with:** Subagent A (Market) and Subagent B (Persistence)
> **Estimated LOC:** ~1500 new

## Purpose

Implement the full Coordinator pipeline:
- **Planner** → selects Instruments, fetches Quotes, computes estimated fill/slippage/fee, compares against thresholds
- **Validator** → per-venue pre-flight checks (balance, symbol, qty rules)
- **Executor** → persist, send all orders concurrently, poll fills
- **Reconciler** → reverse filled legs on partial failure; `NEEDS_MANUAL` on compensation failure
- **Orchestrator** → wires Planner → Validator → Executor → Reconciler into a single `submit(intent)` call
- **State machine** → `is_valid_transition()` implementation

The `Intent`, `Plan`, `PlannedLeg`, and state constants are already defined in `src/coordinator/` by Stage 0. **You fill in the behavior.**

## Key dependency note

You code against the **Market and Persistence interfaces**, not implementations. Your tests use a minimal mock that satisfies the `BaseExchange` protocol (you don't need Subagent A's full `MockExchange` — just enough to return canned orderbooks and accept orders). Your code imports `InstrumentRegistry`, `QuoteFetcher`, `Quote`, `EstimatedFill` from `src.market` — these exist as contracts with `NotImplementedError` bodies. Your tests can mock them out.

## Files to create

### NEW: `src/coordinator/planner.py`

```python
class Planner:
    """
    Given an Intent:
    1. For each venue in intent.split:
       a. Call registry.find_one(base, venue, product, quote_preference)
       b. If no instrument found → add to rejected_venues
       c. If instrument found → fetch Quote via QuoteFetcher
    2. For each matched (venue, instrument, quote):
       a. Compute notional for this venue = intent.total_notional_usd × split[venue]
       b. Compute qty_base = notional / quote.mid_price, rounded via instrument.round_qty()
       c. Call quote.estimate_fill(qty_base, side) → EstimatedFill
       d. If not estimated_fill.filled_fully → add to rejected_venues with reason
          "insufficient depth: only X of Y filled"
       e. Compute estimated_fee_usd = notional × (taker_fee_rate + maker_fee_rate) / 2
          (conservative: assume half taker, half maker)
       f. Check thresholds: slippage ≤ max_slippage_pct, fee ≤ max_fee_usd,
          funding_rate ≤ max_funding_rate_pct (perp)
    3. Build Plan with legs, rejected_venues, aggregate stats, is_acceptable flag
    """
    def __init__(self, registry: "InstrumentRegistry", quote_fetcher: "QuoteFetcher"): ...

    async def plan(self, intent: "Intent") -> "Plan":
        """
        Returns a Plan. This method has NO side effects — it only reads from
        the registry and fetcher.
        """
        ...

    def _compute_notional(self, total: float, split_ratio: float) -> float: ...
    def _check_thresholds(self, leg: "PlannedLeg", intent: "Intent") -> list[str]: ...
```

### NEW: `src/coordinator/validator.py`

```python
class Validator:
    """
    Pre-flight checks before any orders are sent:
    - Symbol is active (instrument.listing_status == "trading")
    - Account balance ≥ required (for spot: notional; for perp: notional / leverage)
    - Quantity respects min_qty and qty_step
    - (perp) leverage ≤ venue_max_leverage
    - Each check runs concurrently across venues
    """
    def __init__(self, exchanges: dict[str, "BaseExchange"]): ...

    async def validate(self, plan: "Plan") -> "ValidationResult":
        """
        Returns ValidationResult with:
        - is_valid: bool
        - failures: list[(venue, reason_str)]
        """
        ...

@dataclass
class ValidationResult:
    is_valid: bool
    failures: list[tuple[str, str]]  # [(venue, "insufficient balance: need $500, have $200"), ...]
```

### NEW: `src/coordinator/executor.py`

```python
class Executor:
    """
    Executes a validated Plan.
    1. Persist: update intent to EXECUTING, create leg rows (one per venue)
    2. Send: asyncio.gather all create_order calls (< 50ms spread)
    3. Record: update each leg with order_id → SENT
    4. Poll: every poll_interval_ms, call exchange.fetch_order(order_id)
       for each unfilled leg. Continue until:
       - All legs FILLED → ALL_FILLED
       - Any leg REJECTED/TIMEOUT → PARTIAL_FILLED (enter reconcile)
       - Total timeout exceeded → PARTIAL_FILLED
    """
    def __init__(
        self,
        exchanges: dict[str, "BaseExchange"],
        store: "PersistenceStore",
        poll_interval_ms: int = 500,
    ): ...

    async def execute(self, plan: "Plan") -> "ExecutionResult": ...

@dataclass
class LegExecution:
    leg: "PlannedLeg"
    status: str              # FILLED, PARTIAL_FILLED, REJECTED, TIMEOUT, SENT(pending)
    order_id: str | None
    filled_amount: float
    avg_price: float | None
    fee: float               # actual fee from the exchange response, NOT the estimate
    error: str | None

@dataclass
class ExecutionResult:
    status: str                 # ALL_FILLED or PARTIAL_FILLED
    legs: list[LegExecution]
    started_at: float
    completed_at: float
```

**Critical invariant:** `create_leg()` on the store MUST be called before `create_order()` on the exchange. No exception.

**Error handling in `asyncio.gather`:** If `create_order` itself raises (network error, auth error), catch it via `return_exceptions=True` and treat that leg as REJECTED. The leg's `error` field stores the exception message.

**Poll strategy detail:**
```
deadline = time.time() + plan.intent.execute_timeout_seconds
while time.time() < deadline:
    for leg in unfilled_legs:
        order = await exchange.fetch_order(leg.order_id, leg.instrument.venue_symbol)
        if order["status"] == "closed":
            leg.status = FILLED
            leg.filled_amount = order["filled"]
            leg.avg_price = order["average"]
            leg.fee = order.get("fee", {}).get("cost", 0)  # ccxt fee format
            store.update_leg(leg.leg_id, status="FILLED", ...)
        elif order["status"] == "canceled":
            leg.status = REJECTED
    if all_filled: break
    await asyncio.sleep(poll_interval_ms / 1000)
```

**Fee extraction:** ccxt's `create_order` response includes a `"fee"` dict with `"cost"` (quote currency amount). Use `fee.get("cost", 0)` as the actual fee. For native exchanges (Lighter), parse whatever fee field the exchange provides. If no fee data is available, fall back to the estimated fee from the Plan.

### NEW: `src/coordinator/reconciler.py`

```python
class Reconciler:
    """
    Handles PARTIAL_FILLED execution results:
    - For each leg that is FILLED or PARTIAL_FILLED:
      → Send a reverse market order (opposite side) for the EXACT filled amount
      → Mark leg as COMPENSATING → COMPENSATED or COMPENSATION_FAILED
    - For each leg that is SENT but not filled:
      → Try to cancel it via exchange.cancel_order()
    - If any compensation order fails → set intent to ROLLED_BACK_FAILED (= NEEDS_MANUAL)

    MVP note (Stage 2): all orders are spot. Reverse orders are simple
    opposite-side market orders (buy→sell, sell→buy). No reduce_only,
    no position-size tracking. Perp-specific Reconciler logic (reduce_only,
    position-aware close) comes in Stage 4.
    """
    def __init__(self, exchanges: dict[str, "BaseExchange"], store: "PersistenceStore"): ...

    async def reconcile(self, result: "ExecutionResult") -> "ReconciliationResult": ...

@dataclass
class LegReconciliation:
    leg_id: str
    original_order_id: str
    reverse_side: str              # "sell" (if original was buy) or "buy"
    compensation_order_id: str | None
    compensation_status: str       # COMPENSATED or COMPENSATION_FAILED
    filled_amount: float           # how much was compensated

@dataclass
class ReconciliationResult:
    status: str                    # ROLLED_BACK or ROLLED_BACK_FAILED
    legs: list[LegReconciliation]
    residual_exposure_usd: float   # any amount that couldn't be compensated
```

### NEW: `src/coordinator/orchestrator.py`

```python
class Orchestrator:
    """
    Wires Planner → Validator → Executor → Reconciler into one pipeline.

    Usage:
        orch = Orchestrator(registry, quote_fetcher, exchanges, store)
        result = await orch.submit(intent)
        # result is a dict with status, legs, summary for the CLI to render
    """
    def __init__(
        self,
        registry: "InstrumentRegistry",
        quote_fetcher: "QuoteFetcher",
        exchanges: dict[str, "BaseExchange"],
        store: "PersistenceStore",
    ): ...

    async def submit(self, intent: "Intent", dry_run: bool = False) -> dict:
        """
        1. Check store.is_blocked_by_needs_manual() → reject if blocked
        2. Persist intent as PENDING
        3. Planner.plan(intent)
        4. If dry_run: return plan dict, stop here
        5. Validator.validate(plan)
        6. If not valid: store.update_intent_status(REJECTED), return rejected result
        7. Executor.execute(plan)
        8. If ALL_FILLED: store.update_intent_status(ALL_FILLED), return result
        9. If PARTIAL_FILLED:
           a. Reconciler.reconcile(result)
           b. Update intent status to ROLLED_BACK or ROLLED_BACK_FAILED
           c. Return reconciliation result
        """
        ...
```

### MODIFY: `src/coordinator/state_machine.py` — implement `is_valid_transition`

Fill in the transition table (see PRD §7.1):

```
PENDING → VALIDATED, REJECTED
VALIDATED → EXECUTING, REJECTED
EXECUTING → ALL_FILLED, PARTIAL_FILLED, EXECUTE_TIMEOUT
PARTIAL_FILLED → ROLLING_BACK
EXECUTE_TIMEOUT → ROLLING_BACK
ROLLING_BACK → ROLLED_BACK, ROLLED_BACK_FAILED
ALL_FILLED → (terminal)
ROLLED_BACK → (terminal)
ROLLED_BACK_FAILED → (terminal)
REJECTED → (terminal)
```

### MODIFY: `src/coordinator/__init__.py`

Add exports for all new classes.

## Tests to write (`tests/coordinator/`)

All tests use a **minimal in-process mock exchange** — you can define a `FakeExchange` class in `tests/coordinator/conftest.py` that returns canned orderbooks, balances, and order results. It doesn't need to be as full-featured as Subagent A's `MockExchange` — just enough for the coordinator to function.

| Test file | What it covers |
|---|---|
| `conftest.py` | `FakeExchange`, `fake_registry()`, `fake_quote_fetcher()`, `fake_store()` fixtures |
| `test_intent.py` | `Intent.__post_init__` validation (split sum, product enum, limit_price requirement, leverage=1 for spot) |
| `test_plan.py` | `Plan`/`PlannedLeg` construction, aggregate calculation |
| `test_state_machine.py` | `is_valid_transition` — all valid transitions pass, all invalid ones raise |
| `test_planner.py` | Happy path: 2-venue plan with quote preference matching; rejected venue when no instrument; slippage threshold rejection; fee threshold rejection |
| `test_validator.py` | Balance insufficient → reject; symbol not trading → reject; qty below min → reject; multi-failure listing |
| `test_executor.py` | Happy path → ALL_FILLED; persist-before-send ordering; poll until all filled |
| `test_executor_partial.py` | Inject failure on one leg → PARTIAL_FILLED; verify reconciler triggered |
| `test_reconciler.py` | Reverse order success → ROLLED_BACK; reverse order fail → ROLLED_BACK_FAILED; cancel pending legs |
| `test_orchestrator.py` | Full pipeline happy path; dry_run stops after plan; blocked by NEEDS_MANUAL; pipeline returns expected dict shape |

## Verification

```bash
uv run pytest tests/coordinator -v    # all green, >80% line coverage
uv run python -c "
from src.coordinator.planner import Planner
from src.coordinator.validator import Validator
from src.coordinator.executor import Executor
from src.coordinator.reconciler import Reconciler
from src.coordinator.orchestrator import Orchestrator
from src.coordinator.state_machine import is_valid_transition
print('coordinator ok')
"
```

## Commit message

```
stage 2: coordinator pipeline (plan/validate/execute/reconcile)

Implement Planner (instrument selection + Quote-based estimation),
Validator (pre-flight balance/symbol/qty checks), Executor (persist-
before-send, concurrent order dispatch, fill polling), Reconciler
(reverse-order compensation with NEEDS_MANUAL escalation), and
Orchestrator (end-to-end pipeline). Full test suite with in-process
mock exchange.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Out of scope

- ❌ CLI wiring (Subagent D)
- ❌ Real Binance/Hyperliquid integration (Stage 3, after merge)
- ❌ Perp-specific margin/funding logic beyond what's in the contract types (Stage 4)
- ❌ Importing from `src.market.registry` or `src.market.quote_fetcher` at the **implementation** level — you import the **types** only. Your tests mock the behavior.
