---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
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

result = asyncio.run(submit_intent_from_dict({
    "base": "BTC",
    "product": "spot",
    "side": "buy",
    "total_notional_usd": 1000,
    "split": {"binance": 0.5, "hyperliquid": 0.5},
}))
print(result["status"])
```

Pass `dry_run=True` first to check instrument selection and quote estimates before committing
funds.

## Intent dictionary

| Key | Required | Default | Meaning |
|---|---|---|---|
| `base` | yes | — | Base asset, e.g. `BTC` |
| `total_notional_usd` | yes | — | Intent size in USD |
| `split` | yes | — | `{venue: weight}`; weights must sum to 1.0 |
| `intent_id` | no | new UUID | Caller-supplied id for correlating the result |
| `quote_preference` | no | `["USDT", "USDC"]` | Quotes tried in order when matching instruments |
| `product` | no | `"spot"` | `spot` or `perp`; overridable per leg via `leg_configs` |
| `side` | no | `"buy"` | `buy` or `sell` |
| `order_type` | no | `"market"` | `market` or `limit` |
| `leverage` | no | `1` | Perp only |
| `limit_price` | no | `null` | Required for limit orders |
| `max_slippage_pct` | no | `null` | Reject if estimated slippage exceeds this |
| `max_fee_usd` | no | `null` | Reject if estimated fee exceeds this |
| `max_funding_rate_pct` | no | `null` | Reject if the perp funding rate exceeds this |
| `execute_timeout_seconds` | no | `30` | Seconds before execution times out and reconciles |
| `time_in_force` | no | `null` | `GTC`, `IOC` or `FOK` |
| `leg_configs` | no | `{}` | Per-venue `side` / `product` / `leverage` overrides |

## Keyword arguments

All optional; the defaults match the CLI:

| Argument | Default |
|---|---|
| `dry_run` | `False` |
| `exchanges_config_path` | `config/exchanges.yaml` |
| `secrets_config_path` | `config/secrets.yaml` |
| `sqlite_path` | `data/onefill.db` |
| `jsonl_dir` | `logs/` |

## Return value and status

The function returns exactly the dictionary `onefill order --json` prints. Branch on `status`:

| `status` | CLI exit code | Meaning | Extra fields on this branch |
|---|---|---|---|
| `DRY_RUN` | — | Planned and validated only; no orders sent | `plan` |
| `ALL_FILLED` | 0 | Every leg filled within tolerances | `legs`, `execution_time_s` |
| `REJECTED` | 2 | Plan, validation or risk check failed; no orders sent | `reason`, plus `rejected_venues`, `validation_failures` or `risk_failures` |
| `ROLLED_BACK` | 3 | Partial fill compensated; net exposure flat | `legs`, `reconciliation` |
| `ROLLED_BACK_FAILED` | 4 | Compensation failed; manual intervention required and further intents are blocked | `legs`, `reconciliation` |

Every branch also carries `intent_id` and `timing`.

Unlike the CLI, the function does **not** raise when an intent is rejected — the outcome is always
in `status`. Once any intent reaches `ROLLED_BACK_FAILED`, later calls return `REJECTED` with a
`reason` describing the block until an operator clears it with `onefill ack <intent-id>`. See
[Risk Controls](../configuration/risk-controls.md).

## Related

- [Examples](../examples/index.md) — the same flows through the CLI
- [CLI Reference](../cli/index.md) — flags and exit codes
- [Agent Integration](../../developer-guide/design/entry-agent-api.md) — design of this entry point
