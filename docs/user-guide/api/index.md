---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: src/cli/agent_api.py
---

# API

oneFill exposes two externally usable interfaces: the `onefill` CLI, and a single Python entry
point, `src.cli.agent_api.submit_intent_from_dict`. There is **no** HTTP / REST API — nothing in
the repository serves one.

The Python entry point is an adapter on the same `Intent` → `Orchestrator` path the CLI takes, not
a second execution path: it runs the same Planner, Validator, RiskValidator and persistence, and
only skips command-line parsing.

## `submit_intent_from_dict`

```python
import asyncio

from src.cli.agent_api import submit_intent_from_dict
from src.market.instrument import NetworkType

result = asyncio.run(submit_intent_from_dict({
    "base": "BTC",
    "product": "spot",
    "side": "buy",
    "total_notional_usd": 1000,
    "split": {"binance": 0.5, "hyperliquid": 0.5},
}, dry_run=True, target_network=NetworkType.TESTNET))
print(result["status"])
```

Pass `dry_run=True` first to check instrument selection and quote estimates before committing
funds.

## Intent dictionary

| Key | Required | Default | Meaning |
|---|---|---|---|
| `base` | yes | — | Base asset, e.g. `BTC` |
| `total_notional_usd` | except close_all | — | USD size or maximum budget when quantity_native is provided |
| `contract_type` | no | `null` (perp selects linear) | `linear` / `inverse`; spot leaves unset |
| `settlement_asset` | no | `null` | Filter settlement currency, e.g. `USDT` / `BTC` |
| `position_effect` | no | `"open"` | `open` / `close`; failed closes never reopen exposure |
| `close_all` | no | `false` | Only single-leg perp close; omit USD budget to close the full observed position |
| `quantity_native` | no | `null` | Exact single-leg spot base units or perp contracts; requires USD maximum budget, excludes close_all |
| `split` | yes | — | `{venue: weight}`; weights must sum to 1.0 |
| `intent_id` | no | new UUID | Caller-supplied id for correlating the result |
| `quote_preference` | no | `["USDT", "USDC"]` | Quotes tried in order when matching instruments |
| `product` | no | `"spot"` | `spot` or `perp`; overridable per leg via `leg_configs` |
| `side` | no | `"buy"` | `buy` or `sell` |
| `order_type` | no | `"market"` | `market` or `limit` |
| `leverage` | no | `1` | Perp only |
| `limit_price` | no | `null` | Required for limit orders |
| `max_slippage_pct` | no | `null` | Fixed protection vs planning mid-price; when unset, execution uses 0.5% |
| `max_fee_usd` | no | `null` | Reject if estimated fee exceeds this |
| `max_funding_rate_pct` | no | `null` | Reject if the perp funding rate exceeds this |
| `execute_timeout_seconds` | no | `30` | Seconds before execution times out and reconciles |
| `time_in_force` | no | `null` | `GTC`, `IOC` or `FOK`; defaults to IOC; unsupported choices reject |
| `leg_configs` | no | `{}` | Per-venue `side` / `product` / `leverage` / `contract_type` / `settlement_asset` overrides |
| `max_spread_pct` | no | `null` | Maximum book spread |
| `max_quote_age_ms` | no | `1000` | Maximum age of the quote used for sending |
| `max_total_cost_usd` | no | `null` | Aggregate adverse price deviation plus fees, without counting spread twice |
| `max_order_notional_usd` | no | `null` | Split each leg into sequential protected orders below this limit |
| `min_fill_ratio` | no | `1.0` | Lower accepted ratio permitted only for single-leg intents |
| `compensation_slippage_pct` | no | `0.5` | Compensation protection vs the actual original fill price |
| `reconcile_timeout_seconds` | no | `10` | Separate deadline for cancellation and compensation |

For COIN-M use `product="perp"`, `contract_type="inverse"`, and e.g. `settlement_asset="BTC"`,
with the `coinm` family enabled. To close, set `position_effect="close"`, select the opposite order side,
and provide a budget/native quantity or `close_all=True`. The venue must use an ordinary account,
one-way positions, and single-asset margin.

## Keyword arguments

All arguments below are optional. Default paths are relative to the working directory:

| Argument | Default |
|---|---|
| `dry_run` | `False` |
| `exchanges_config_path` | `config/exchanges.yaml` |
| `secrets_config_path` | `None` — select `secrets.<network>.yaml` beside `exchanges_config_path` |
| `target_network` | `None` — use each venue’s `default_network`, falling back to `testnet` |
| `sqlite_path` | `data/onefill.db` |
| `jsonl_dir` | `logs/` |

`target_network` accepts `NetworkType.TESTNET` or `NetworkType.MAINNET` and selects both
endpoints and credentials. An explicit `secrets_config_path` points to one network file; its
top-level `network` must match every selected venue. Use automatic file selection when venue
default networks differ. No credentials are read from the other network or the shared
`secrets.yaml`. See [Configuration](../configuration/index.md) for migration from the old file.

## Return value and status

The function returns the coordinator result dictionary. CLI JSON presents the same outcome with flattened
legs and an aggregate display; API dry runs retain `plan`. Branch on `status`:

| `status` | CLI exit code | Meaning | Extra fields on this branch |
|---|---|---|---|
| `DRY_RUN` | — | Planned and validated only; no orders sent | `plan` |
| `ALL_FILLED` | 0 | Every leg filled within tolerances | `legs`, `execution_time_s` |
| `REJECTED` | 2 | Plan, validation or risk check failed; no orders sent | `reason`, plus `rejected_venues`, `validation_failures` or `risk_failures` |
| `ROLLED_BACK` | 3 | Partial opening fill compensated back to its recorded baseline | `legs`, `reconciliation` |
| `ROLLED_BACK_FAILED` | 4 | Unresolved opening/closing execution; manual intervention required and further intents are blocked | `legs`, `reconciliation` |

Normal submission branches carry `intent_id` and `timing`. Repeating an existing ID returns its persisted
status and legs with `is_duplicate: true`; it never sends another order. Reusing the ID with different
parameters raises `ValueError`. Incomplete executions must be recovered before submitting new intents.

`reconciliation.residual_exposure_usd` is `null` when order state cannot establish an exposure amount.
`validation_failures` and `risk_failures` are also reported in dry-run responses. USD sizing assumes
USD/USDT/USDC parity. Spot quantities use base units; perpetual quantities use native contract counts
and contract size, including COIN-M inverse contracts. Native fills and base exposure are separate fields.
Unsupported quote conversions reject. See [Binance integration](../../developer-guide/design/base-binance-integration.md).

Unlike the CLI, the function does **not** raise when an intent is rejected — the outcome is always
in `status`. Once any intent reaches `ROLLED_BACK_FAILED`, later calls return `REJECTED` with a
`reason` describing the block until an operator clears it with `onefill ack <intent-id>`. See
[Risk Controls](../configuration/risk-controls.md).

## Related

- [Examples](../examples/index.md) — the same flows through the CLI
- [CLI Reference](../cli/index.md) — flags and exit codes
- [Agent Integration](../../developer-guide/design/entry-agent-api.md) — design of this entry point
