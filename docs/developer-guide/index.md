# Developer Guide

Welcome to the oneFill developer guide. This section covers the internal architecture, design decisions, and implementation details.

## Who this is for

- Developers integrating with or extending oneFill
- Contributors adding new venues or features
- AI agents working on the codebase (these docs serve as the primary context alongside `CLAUDE.md`)

## What you'll find here

| Page | Content |
|---|---|
| [Architecture](architecture.md) | High-level system design, three-layer architecture, data flow |
| [State Machine](state-machine.md) | Intent and leg state transitions, terminal states, the blocking mechanism |
| [Critical Invariants](invariants.md) | 7 load-bearing properties that must not be violated |
| [Coordination Pipeline](coordination-pipeline.md) | Planner → Validator → Executor → Reconciler deep dive |
| [Market Layer](market-layer.md) | Asset, Instrument, InstrumentRegistry, Quote design and rationale |
| [Persistence Layer](persistence-layer.md) | SQLite + JSONL dual-write strategy, table schemas, audit trail |
| [Exchange Layer](exchange-layer.md) | BaseExchange contract, CCXTExchange adapter, adding new venues |
| [Testing](testing.md) | Test structure, pytest configuration, MockExchange, test patterns |
| [Legacy Mode](legacy-mode.md) | How the legacy bot coexists with oneFill during the transition |

## Quick reference

- **Source code:** `src/` — 13 packages, ~45 modules
- **Tests:** `tests/` — ~300 tests, 57 files across 9 packages
- **Config:** `config/` — exchanges, secrets, risk, volume_farming YAML files
- **CLAUDE.md:** Root-level developer guide for Claude Code sessions
- **PRD:** [`PRD.md`](../PRD.md) — full product requirements document
