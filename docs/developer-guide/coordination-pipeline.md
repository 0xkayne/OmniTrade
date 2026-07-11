# Coordination Pipeline

The coordinator is the heart of oneFill. It runs four sequential phases to take an `Intent` from "user wants to trade" to "trades filled or compensated."

## Pipeline overview

```
Intent → Planner → Validator → RiskValidator → Executor → Reconciler
  ↓         ↓          ↓            ↓             ↓            ↓
(user    (pure      (pure        (pure         (side       (side
 input)   read)      read)        read)         effects)    effects)
```

The `Orchestrator` wires all phases together and enforces the blocking state check before any new intent is processed.

## Planner

**File:** `src/coordinator/planner.py`  
**Side effects:** None

Given an `Intent`, the Planner:

1. **Resolve instruments** — for each venue in `intent.split`, calls `registry.find_one(base, venue, market_type, quote_preference)`. Resolves any per-leg overrides (product, side, leverage).
2. **Fetch quotes** — calls `quote_fetcher.fetch_many()` for all resolved instruments concurrently via `asyncio.gather`.
3. **Compute per-leg estimates** — for each leg, uses `quote.estimate_fill(amount_base, side)` to compute estimated average price and slippage. Calculates estimated fee from the instrument's taker rate.
4. **Check thresholds** — compares each leg's slippage/fee/funding against user-supplied `max_slippage_pct`, `max_fee_usd`, `max_funding_rate_pct`. Rejected venues go into `rejected_venues` with reasons.
5. **Build Plan** — assembles `PlannedLeg` objects, computes `aggregate_estimated_avg_price` and `aggregate_estimated_fee_usd`, sets `is_acceptable = (len(rejected_venues) == 0 and len(rejection_reasons) == 0)`.

### Quote estimation

`Quote.estimate_fill()` walks the order book depth to estimate what price the order will actually get, accounting for order size exceeding top-of-book liquidity. It returns an `EstimatedFill` with:

- `avg_price` — volume-weighted average price through the consumed levels
- `slippage_pct` — difference from mid-price
- `depth_consumed_levels` — how far into the book the order would go
- `filled_fully` — whether the order size fits within available liquidity

## Validator

**File:** `src/coordinator/validator.py`  
**Side effects:** None

Checks per-venue constraints:

1. **Instrument listing** — is the symbol still trading? (not delisted or paused)
2. **Balance** — does the account have enough quote (for buys) or base (for sells)? Uses `exchange.fetch_balance()` with account type awareness (spot vs swap margin).
3. **Quantity rules** — is the planned qty above `min_qty`? Does it respect `qty_step`?
4. **Price rules** — is the price within `price_step` precision?
5. **Leverage** — for perp legs, does the venue support the requested leverage? Is it ≤ `max_leverage`?
6. **Notional** — does the leg meet `min_notional`?

Balances are prefetched in one batch via `fetch_balances()` (one call per unique `(venue, account_type)` pair), so validation is fast.

Returns `ValidationResult(is_valid, failures)` where `failures` is a list of `(venue, reason)` tuples.

## RiskValidator

**File:** `src/coordinator/risk.py`  
**Side effects:** None (reads from persistence)

Checks configurable guardrails from `config/risk.yaml`:

- `max_notional_per_intent` — reject oversized orders
- `daily_loss_limit_usd` — circuit breaker on daily losses
- `max_venue_exposure_usd` — cap per-venue outstanding exposure
- Rate limiting — sliding window max orders

Runs after Validator so balances and instrument checks happen first. See [Risk Controls](../user-guide/risk-controls.md) for details.

## Executor

**File:** `src/coordinator/executor.py`  
**Side effects:** Yes — real orders, persistence writes

The most critical phase. Every step is crash-safe:

1. **Persist legs** — writes a `LegRow` to SQLite for each leg with `status = PENDING_SEND`. Also appends to JSONL audit log. **This happens before any order is sent.**
2. **Concurrent dispatch** — calls `asyncio.gather(*[create_order(leg) for leg in legs])` to emit all orders with <50ms spread between request emissions.
3. **Fill confirmation** — WebSocket is primary (`ccxt.watch_orders`); HTTP polling is fallback (adaptive backoff starting at 50ms, doubling each round, capped at `poll_interval_ms`). Uses early termination: exits the poll loop immediately when a leg fills and another definitively fails.
4. **Status update** — each leg transitions to `FILLED`, `PARTIAL_FILLED`, `REJECTED`, or `TIMEOUT`. The intent transitions to `ALL_FILLED` or `PARTIAL_FILLED`.

### Persist-before-send

This is the primary defense against the "lost order" problem. The sequence for each leg is:

```
1. INSERT INTO legs (...) VALUES (...)
2. append JSONL audit event
3. exchange.create_order(...)
```

If the process crashes after step 2 but before step 3 completes, recovery can query the exchange for the order status. If it crashes after step 3 but before recording the fill, the WebSocket or HTTP poll will pick it up on restart.

## Reconciler

**File:** `src/coordinator/reconciler.py`  
**Side effects:** Yes — reverse orders, persistence writes

Triggered when any leg fails or times out:

1. **Cancel pending legs** — for legs in `SENT` state, send cancel orders.
2. **Compensate filled legs** — for legs that filled, send reverse market orders to flatten the position. Example: if leg A was a "buy 0.1 BTC" that filled, the compensation is a "sell 0.1 BTC" market order.
3. **For perp legs** — uses `reduce_only` order parameter so compensation orders don't accidentally open new positions.
4. **Status** — if all compensation orders fill, intent → `ROLLED_BACK` (net exposure flat). If any compensation order fails, intent → `ROLLED_BACK_FAILED` → **system blocked**.

### Compensation and account types

- **Spot orders:** compensation uses the standard `create_order` with reversed side
- **Perp orders:** compensation uses `create_order` with `reduce_only: True` and reversed side, ensuring the compensation reduces rather than opens a position
- **Perp with leverage:** the `set_leverage()` call is repeated before compensation to ensure the margin mode is correct

## Orchestrator

**File:** `src/coordinator/orchestrator.py`

Wires everything together:

```python
class Orchestrator:
    def __init__(self, registry, quote_fetcher, exchanges, store, ...):
        self._planner = Planner(registry, quote_fetcher)
        self._validator = Validator(exchanges)
        self._executor = Executor(exchanges, store)
        self._reconciler = Reconciler(exchanges, store)
        self._risk_validator = RiskValidator(store, risk_config)

    async def submit(self, intent, dry_run=False, timing=None) -> dict:
        # 1. Check blocking state
        if await self._store.is_blocked_by_needs_manual():
            return {"status": "REJECTED", "reason": "Blocked by NEEDS_MANUAL"}

        # 2. Plan
        plan = await self._planner.plan(intent, timing)
        if not plan.is_acceptable:
            return {"status": "REJECTED", ...}

        # 3. Validate
        validation = await self._validator.validate(plan, timing)
        if not validation.is_valid:
            return {"status": "REJECTED", ...}

        # 4. Risk check
        risk = await self._risk_validator.check(intent, plan)
        if not risk.is_allowed:
            return {"status": "REJECTED", ...}

        # 5. Dry run? Stop here
        if dry_run:
            return {"status": "VALIDATED", ...}

        # 6. Execute
        result = await self._executor.execute(plan, timing)

        # 7. Reconcile (if needed)
        if result.status == "PARTIAL_FILLED":
            reconciliation = await self._reconciler.reconcile(result, timing)
            return reconciliation
        return {"status": "ALL_FILLED", ...}
```
