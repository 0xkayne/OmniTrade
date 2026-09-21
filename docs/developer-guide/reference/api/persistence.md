---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-14
applies_to: src/persistence/
---

# Persistence Package

SQLite + JSONL dual-write persistence — PersistenceStore, data row types, and schema DDL.

::: src.persistence
    options:
      show_root_heading: false

## Cross-venue arbitrage records

Stage-two hedged execution stores its lifecycle separately from the generic
`intents`/`legs` tables. The `arbitrage_cycles` table records one paired
operation, `arbitrage_cycle_legs` records the two venue orders (with a unique
`(cycle_id, role)` and client order ID), and `arbitrage_fills` records each
venue trade. Fill insertion is idempotent on `(venue, trade_id)` so replayed
WebSocket events cannot double-count execution.

The persistence layer exposes scalar-only methods and does not import
`src.arbitrage` domain classes:

```python
await store.create_arbitrage_cycle(**fields)
await store.get_arbitrage_cycle(cycle_id)
await store.update_arbitrage_cycle(cycle_id, status="OPEN")
await store.create_arbitrage_cycle_leg(**fields)
await store.update_arbitrage_cycle_leg(leg_id, status="FILLED")
await store.insert_arbitrage_fill(**fields)  # bool: inserted or duplicate
await store.list_unfinished_arbitrage_cycles()
```

`list_unfinished_arbitrage_cycles()` is the restart-recovery entry point; it
excludes `CLOSED` and `MANUAL_REVIEW` terminal cycles. All three tables and
their indexes are created idempotently by `PersistenceStore.initialize()`.
