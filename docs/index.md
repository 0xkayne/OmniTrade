# oneFill

> Multi-venue coordinated order execution. Submit one order, fan out across exchanges in parallel, get a guaranteed coordinated final state.

## What it is

Manually placing the same order on multiple exchanges takes 30+ seconds. In that window, prices move and partial failures leave you with unwanted directional exposure. **oneFill** compresses that window to milliseconds and handles the failure cases for you.

You submit a single CLI command — for example *"buy $1000 of BTC across Binance and Hyperliquid, 50/50 split, max slippage 0.3%"*. oneFill:

1. **Plans** — selects one `Instrument` per venue (BTC/USDT spot on Binance, BTC/USDC:USDC perp on Hyperliquid, etc.), fetches live quotes, and estimates per-leg price/slippage/fee.
2. **Validates** — checks listing status, balance, qty rules, leverage feasibility on each venue.
3. **Executes** — persists the plan to SQLite, then fans out all `create_order` calls via `asyncio.gather` (target: <50ms spread between request emissions).
4. **Reconciles** — if any leg fails or times out, sends reverse market orders to flatten any leg that did fill. If reconciliation itself fails, the intent enters `ROLLED_BACK_FAILED` (also called `NEEDS_MANUAL`) and blocks all further intents until a human resolves it.

oneFill is an **execution tool, not a strategy tool**. It does not decide *whether* to trade or *how much* — the user (or, in the future, a Claude Agent SDK agent) does. It executes the user's already-decided intent.

Terminal states: `ALL_FILLED`, `REJECTED`, `ROLLED_BACK`, `ROLLED_BACK_FAILED`.

## Status

**Stage 6 shipped** (Jul 2026): funding rate arbitrage strategy (premium mean-reversion model), structured JSON logging, metrics hooks, Agent SDK integration point, chaos-test crash-recovery validation. See [`REFACTOR_PLAN.md`](REFACTOR_PLAN.md) for the full roadmap.

- **Venues:** Binance (demo / mainnet, spot + perp) · Hyperliquid (testnet / mainnet, perp + spot)
- **Tests:** 300+ non-network · 27 perp-specific · 9 network (testnet credentials required)
- **CCXT surface:** full ccxt async API mirrored on `BaseExchange` / `CCXTExchange` (~240 methods)

## Quick links

| Section | Description |
|---|---|
| [User Guide](user-guide/index.md) | Quick start, CLI reference, configuration, risk controls |
| [Developer Guide](developer-guide/index.md) | Architecture, state machine, invariants, pipeline details |
| [API Reference](api-reference/index.md) | Auto-generated Python API docs from source docstrings |
| [Design Docs](design-docs/index.md) | PRD, refactor plan, status, subagent specs |
| [PRD](PRD.md) | Full product requirements document |
| [Status](STATUS.md) | Detailed implementation status snapshot |
| [GitHub](https://github.com/0xkayne/OmniTrade) | Source code repository |

## Risk disclaimer

Cryptocurrency trading carries significant market and compliance risk. Validate strategies on testnet before using real funds. This project is for technical research and education; nothing here is investment advice.
