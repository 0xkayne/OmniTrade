# State Machine

oneFill uses a deterministic state machine to track every intent and its legs through the execution lifecycle.

## Intent states

```
PENDING → VALIDATED → EXECUTING ─┬─→ ALL_FILLED              (success)
   │          │                  │
   │          └─→ REJECTED       ├─→ PARTIAL_FILLED ─→ ROLLING_BACK ─┬─→ ROLLED_BACK     (partial; compensated)
   │                             │                                   │
   │                             └─→ EXECUTE_TIMEOUT (same path)     └─→ ROLLED_BACK_FAILED (compensation failed)
   │
   └─→ REJECTED (plan/validate/risk failed; no orders sent)
```

### Terminal states

| State | Meaning | Blocks future intents? |
|---|---|---|
| `ALL_FILLED` | Every leg filled within tolerances | No |
| `REJECTED` | Plan, validate, or risk check failed — no orders sent | No |
| `ROLLED_BACK` | Partial fill → compensation orders succeeded → net exposure flat | No |
| `ROLLED_BACK_FAILED` | Compensation failed — manual intervention required | **Yes** |
| `RESOLVED_MANUAL` | Human acknowledged a `ROLLED_BACK_FAILED` via `onefill ack` | No |

### The blocking state

`ROLLED_BACK_FAILED` is the only blocking state. When any intent enters this state:

1. All subsequent `onefill order` submissions are rejected with a clear message.
2. The system must be manually unblocked by acknowledging the failed intent:
   ```bash
   onefill ack <intent-id>
   ```
3. `ack` transitions the intent to `RESOLVED_MANUAL` (a terminal, non-blocking state).

This is intentional: if the automated compensation logic itself fails, a human must investigate. There is no automatic retry.

## Leg states

Each leg within an intent tracks its own status independently:

| State | Description |
|---|---|
| `PENDING_SEND` | Leg persisted to SQLite, order not yet sent |
| `SENT` | Order submitted to exchange, awaiting fill |
| `FILLED` | Order fully filled |
| `PARTIAL_FILLED` | Order partially filled |
| `REJECTED` | Order rejected by exchange |
| `TIMEOUT` | Order didn't fill within `execute_timeout_seconds` |
| `PENDING_CANCEL` | Cancel sent for this leg (during reconciliation) |
| `CANCELLED` | Leg order successfully cancelled |
| `COMPENSATING` | Reverse order in flight |
| `COMPENSATED` | Reverse order filled |
| `COMPENSATION_FAILED` | Reverse order failed |

## Transition enforcement

The state machine module (`src/coordinator/state_machine.py`) exports:

- `INTENT_STATES` — list of all valid intent states with descriptions
- `LEG_STATES` — list of all valid leg states with descriptions
- `TERMINAL_STATES` — `{"ALL_FILLED", "ROLLED_BACK", "ROLLED_BACK_FAILED", "RESOLVED_MANUAL", "REJECTED"}`
- `BLOCKING_STATE` — `"ROLLED_BACK_FAILED"`
- `is_valid_transition(from_state, to_state)` — validates state transitions

All state updates go through `PersistenceStore.update_intent_status()` and `PersistenceStore.update_leg()`, which write to both SQLite and JSONL atomically.
