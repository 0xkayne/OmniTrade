# Critical Invariants

These are load-bearing properties that must be preserved. Violating any of them breaks the system's guarantees.

## 1. Every `create_order` is preceded by a persisted leg row

Executor must write to SQLite/JSONL before issuing the call. Crash-after-send must be recoverable from the persistence layer.

**Why:** If the process crashes between sending an order and recording it, you have a position with no audit trail. The persist-before-send pattern eliminates that window.

**Where:** `src/coordinator/executor.py` — `_send_leg()` writes the leg row, then calls `create_order()`.

## 2. `ROLLED_BACK_FAILED` blocks all subsequent Intents

Don't add "retry" or "auto-recover from ROLLED_BACK_FAILED" paths — escalation to a human is the design. The system must be manually unblocked via `onefill ack`.

**Why:** If the automated compensation itself failed, something unexpected happened (exchange downtime, API error, partial fill on the compensation leg). That situation requires human judgment.

**Where:** `src/persistence/store.py` — `is_blocked_by_needs_manual()` checks for any intent in the blocking state.

## 3. Per-leg `product`/`side`/`leverage` override Intent defaults

`Intent.product`, `Intent.side`, and `Intent.leverage` are defaults — any leg can override them via `LegConfig` (parsed from the `--split` extended syntax). A single Intent can mix spot/perp, buy/sell, and different leverage levels across venues. Spot legs must have leverage=1 (enforced in `Intent.__post_init__`).

**Why:** The whole point of oneFill is multi-venue coordination. If you can't mix products or directions across legs, you can't do delta-neutral funding arbitrage or cross-venue hedging.

**Where:** `src/coordinator/intent.py` — `LegConfig.resolve_product()`, `resolve_side()`, `resolve_leverage()`.

## 4. The Market layer is the only place that knows venue-native symbols

Higher layers use `Instrument` objects; CLI uses `--base` and `--quote-preference`. Never let `BTCUSDT` leak into Coordinator code.

**Why:** The same base asset can trade under many symbols. `InstrumentRegistry.find_one()` handles the mapping. If raw symbols leak into Coordinator, you silently couple the pipeline to exchange-specific naming and break multi-venue abstraction.

**Where:** `src/market/registry.py`, `src/market/instrument.py`.

## 5. Coordinator phases are pure-ish

Planner and Validator have no side effects. Executor and Reconciler do. Tests rely on this — keep it.

**Why:** Planner and Validator can be tested deterministically with mock data. Executor and Reconciler need integration tests or mock exchanges. Breaking this separation makes tests flaky.

**Where:** `src/coordinator/planner.py`, `src/coordinator/validator.py` — read-only by contract.

## 6. Legacy VolumeEngine margin safety guard

Before every open, free margin is checked; on shortfall it retries 3× with 5-min sleep, then auto-closes the lowest-cost position. Don't bypass when modifying open-position paths.

**Where:** `src/core/volume_engine.py`.

## 7. Legacy volume accounting is in USD notional

`daily_max_volume`, `daily_target_volume`, stats reports — all USD. (oneFill is also USD-notional; same principle, different module.)

**Where:** `src/core/volume_engine.py`, `src/strategies/hedge_volume.py`.
