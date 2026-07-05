# Agent SDK Integration

oneFill Stage 5 ships a Python-callable API (`src/cli/agent_api.py`) that can be
registered as an Anthropic Claude Agent SDK tool in Phase 2.

## Quick start

```python
from src.cli.agent_api import submit_intent_from_dict

result = await submit_intent_from_dict(
    {
        "base": "BTC",
        "product": "spot",
        "side": "buy",
        "total_notional_usd": 1000,
        "split": {"binance": 0.5, "hyperliquid": 0.5},
    },
    dry_run=True,
)
print(result["status"])  # "DRY_RUN"
```

The function returns the same JSON-friendly dict that `onefill order --json`
produces.

## Intent dictionary schema

| Key | Type | Required | Default |
|---|---|---|---|
| `base` | str | yes | — |
| `total_notional_usd` | float | yes | — |
| `split` | dict[str, float] | yes | — |
| `product` | str | no | `"spot"` |
| `side` | str | no | `"buy"` |
| `order_type` | str | no | `"market"` |
| `leverage` | int | no | `1` |
| `max_slippage_pct` | float | no | `None` |
| `max_fee_usd` | float | no | `None` |
| `max_funding_rate_pct` | float | no | `None` |
| `execute_timeout_seconds` | int | no | `30` |
| `leg_configs` | dict[str, dict] | no | `{}` |

## Phase 2 (future)

The Phase 2 project will register `submit_intent_from_dict` as a Claude Agent
SDK tool so users can express intent in natural language:

> "buy $1000 of BTC across Binance and Hyperliquid, 50/50 split"

Phase 2 lives in a separate repository and depends on oneFill as a library.
