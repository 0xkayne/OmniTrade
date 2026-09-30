---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-30
applies_to: src/coordinator/state_machine.py
---

# State Machine

Omnitrade uses a deterministic state machine to track every intent and its legs through the execution lifecycle.

## Intent states

<figure markdown="span">
  <img src="../../../assets/base-state-machine-intent.svg" alt="base-state-machine-intent" width="100%">
</figure>

（图源码 `docs/assets/base-state-machine-intent.dot`，重新生成：`scripts/render_diagrams.sh base-state-machine-intent`）

**`ROLLED_BACK_FAILED` 是唯一的阻断态**：它虽是终态，却有一条出边——人工 `onefill ack` 把它推进
`RESOLVED_MANUAL`，后者才真正无出边。图上这条边是刻意画出来的：没有它，读图的人会以为
系统一旦进入阻断态就无法恢复。**不存在自动重试路径**，见下方「The blocking state」。

开仓的以下两条路径汇入 `ROLLING_BACK`；显式平仓失败直接进入 `ROLLED_BACK_FAILED`，不重新开仓：直接超时（`EXECUTE_TIMEOUT`）与部分成交（`PARTIAL_FILLED`）
在补偿阶段被同等对待——两者的共同点是**都有可能留下净敞口**，而补偿的目的正是压回它。
显式恢复发现已持久化 Leg 的上下文缺失或契约无法核对时，`PENDING`、`VALIDATED`
也可直接进入阻断态；缺失的执行事实不能用猜测补齐。

### Terminal states

| State | Meaning | Blocks future intents? |
|---|---|---|
| `ALL_FILLED` | Every leg filled within tolerances | No |
| `REJECTED` | Plan, validate, or risk check failed — no orders sent | No |
| `DRY_RUN` | Preview completed; no orders sent | No |
| `ROLLED_BACK` | Opening compensation restored the saved pre-send baseline | No |
| `ROLLED_BACK_FAILED` | Unresolved exposure or incomplete close — manual intervention required | **Yes** |
| `RESOLVED_MANUAL` | Human acknowledged a `ROLLED_BACK_FAILED` via `onefill ack` | No |

### The blocking state

`ROLLED_BACK_FAILED` is the only blocking state. When any intent enters this state:

1. All subsequent `onefill order` submissions are rejected with a clear message.
2. The system must be manually unblocked by acknowledging the failed intent:
   ```bash
   onefill ack <intent-id>
   ```
3. `ack --network <original-network>` queries persisted orders and positions before transitioning to `RESOLVED_MANUAL`; it rejects unresolved orders or a mismatched target position. `status --refresh` is read-only and never clears the block.

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
| `UNKNOWN` | Submission or fill status is ambiguous; query before further action |
| `FILLED` | Order fully filled |
| `PARTIAL_FILLED` | Order partially filled |
| `REJECTED` | Order rejected by exchange |
| `TIMEOUT` | Order didn't fill within `execute_timeout_seconds` |
| `CANCELLED` | Leg order successfully cancelled |
| `COMPENSATING` | Reverse order in flight |
| `COMPENSATED` | Reverse order filled |
| `COMPENSATION_FAILED` | Reverse order failed |

### 中断恢复

`EXECUTE_TIMEOUT` 已登记在 `INTENT_STATES`。中断的 `PENDING`、`VALIDATED` 或 `EXECUTING`
可以在显式恢复时进入 `ROLLING_BACK`；没有持久化 Leg 的中断 Intent 可拒绝结束。
普通执行把未知订单交给 Reconciler，不能把网络超时等同于拒单。

## Transition enforcement

The state machine module (`src/coordinator/state_machine.py`) exports:

- `INTENT_STATES` — list of all valid intent states with descriptions
- `LEG_STATES` — list of all valid leg states with descriptions
- `TERMINAL_STATES` — `ALL_FILLED`, `ROLLED_BACK`, `ROLLED_BACK_FAILED`, `RESOLVED_MANUAL`, `REJECTED`, `DRY_RUN`
- `BLOCKING_STATE` — `"ROLLED_BACK_FAILED"`
- `is_valid_transition(from_state, to_state)` — validates state transitions

State updates go through `PersistenceStore.update_intent_status()` and `PersistenceStore.update_leg()`.
SQLite and JSONL writes are sequential, not a cross-file atomic transaction. The store does not enforce domain transitions;
the coordinator and its tests own state semantics. Per-order requests and cumulative snapshots are persisted separately in `orders`.
