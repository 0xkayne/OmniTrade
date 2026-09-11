---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-11
applies_to: src/coordinator/state_machine.py
---

# State Machine

oneFill uses a deterministic state machine to track every intent and its legs through the execution lifecycle.

## Intent states

<figure markdown="span">
  <img src="../../../assets/base-state-machine-intent.svg" alt="base-state-machine-intent" width="100%">
</figure>

（图源码 `docs/assets/base-state-machine-intent.dot`，重新生成：`scripts/render_diagrams.sh base-state-machine-intent`）

**`ROLLED_BACK_FAILED` 是唯一的阻断态**：它虽是终态，却有一条出边——人工 `onefill ack` 把它推进
`RESOLVED_MANUAL`，后者才真正无出边。图上这条边是刻意画出来的：没有它，读图的人会以为
系统一旦进入阻断态就无法恢复。**不存在自动重试路径**，见下方「The blocking state」。

这两条路径都汇入 `ROLLING_BACK`：直接超时（`EXECUTE_TIMEOUT`）与部分成交（`PARTIAL_FILLED`）
在补偿阶段被同等对待——两者的共同点是**都有可能留下净敞口**，而补偿的目的正是压回它。

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

<figure markdown="span">
  <img src="../../../assets/base-state-machine-leg.svg" alt="base-state-machine-leg" width="100%">
</figure>

（图源码 `docs/assets/base-state-machine-leg.dot`，重新生成：`scripts/render_diagrams.sh base-state-machine-leg`）

`PENDING_SEND` 是 leg 的**起点而不是可选项**：`Executor` 必须先把 leg 行落盘，再 `create_order`。
这条顺序是崩溃后可恢复的前提——见[协调流程](base-coordination-pipeline.md) 的 Executor 一节。

| State | Description |
|---|---|
| `PENDING_SEND` | Leg persisted to SQLite, order not yet sent |
| `SENT` | Order submitted to exchange, awaiting fill |
| `FILLED` | Order fully filled |
| `PARTIAL_FILLED` | Order partially filled |
| `REJECTED` | Order rejected by exchange |
| `TIMEOUT` | Order didn't fill within `execute_timeout_seconds` |
| `CANCELLED` | Leg order successfully cancelled |
| `COMPENSATING` | Reverse order in flight |
| `COMPENSATED` | Reverse order filled |
| `COMPENSATION_FAILED` | Reverse order failed |

### 源码中的一处不一致（待处理）

**`EXECUTE_TIMEOUT` 是 intent 的合法转移目标，却不在 `INTENT_STATES` 里。**
`state_machine.py` 的 `_TRANSITIONS` 有 `"EXECUTING": {..., "EXECUTE_TIMEOUT"}` 与
`"EXECUTE_TIMEOUT": {"ROLLING_BACK"}`，`tests/coordinator/test_state_machine.py` 也覆盖了这两条边；
但 `INTENT_STATES` 的十项里没有它。而 `TERMINAL_STATES` 与 `BLOCKING_STATE` 都是从状态名手写的集合，
`Orchestrator` 只写入 `PARTIAL_FILLED` 与 `ROLLING_BACK`，所以这条边目前**可达但不可写**。

本节按 `_TRANSITIONS`（权威转移表）作图，因为它才是 `is_valid_transition` 的实际依据。
这不影响运行时行为，但意味着「合法状态集合」有两个互相不一致的来源。
处置：要么把 `EXECUTE_TIMEOUT` 补进 `INTENT_STATES`，要么把它从 `_TRANSITIONS` 移除——
在决定之前，修改状态机时以 `_TRANSITIONS` 为准。

## Transition enforcement

The state machine module (`src/coordinator/state_machine.py`) exports:

- `INTENT_STATES` — list of all valid intent states with descriptions
- `LEG_STATES` — list of all valid leg states with descriptions
- `TERMINAL_STATES` — `{"ALL_FILLED", "ROLLED_BACK", "ROLLED_BACK_FAILED", "RESOLVED_MANUAL", "REJECTED"}`
- `BLOCKING_STATE` — `"ROLLED_BACK_FAILED"`
- `is_valid_transition(from_state, to_state)` — validates state transitions

All state updates go through `PersistenceStore.update_intent_status()` and `PersistenceStore.update_leg()`, which write to both SQLite and JSONL atomically.
