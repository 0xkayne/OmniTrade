# Design Docs

Design documents, product specifications, and implementation plans for oneFill.

## Documents

| Document | Description |
|---|---|
| [PRD](../PRD.md) | Full product requirements document |
| [Refactor Plan](../REFACTOR_PLAN.md) | Staged implementation plan (Stages 0–6) |
| [Implementation Status](../STATUS.md) | Current implementation status snapshot |
| [Agent SDK Integration](../AGENT_INTEGRATION.md) | Claude Agent SDK integration guide |
| [Optimization Roadmap](../OPTIMIZATION_ROADMAP.md) | Post-MVP optimization tiers |
| [Funding Arb Theory (中文)](../FUNDING_ARB_THEORY.md) | Chinese-language theoretical analysis of funding rate arbitrage |
| [Exchange Integration Guide (中文)](exchange-integration-guide.md) | Chinese-language guide for adding new exchanges |
| [Volume Farming Guide (中文)](../legacy/VOLUME_FARMING_GUIDE.md) | Legacy volume farming usage guide |

## Subagent Specs

Implementation contracts for the parallel worktree development that built the oneFill MVP:

| Spec | Content |
|---|---|
| [Contract (0)](../subagent/0_CONTRACT.md) | Shared type definitions and project skeleton |
| [Market (A)](../subagent/A_MARKET.md) | Market layer implementation spec |
| [Persistence (B)](../subagent/B_PERSISTENCE.md) | Persistence layer implementation spec |
| [Coordinator (C)](../subagent/C_COORDINATOR.md) | Coordinator implementation spec |
| [CLI (D)](../subagent/D_CLI.md) | CLI implementation spec |
| [Merge (E)](../subagent/E_MERGE.md) | Merge integration spec |
