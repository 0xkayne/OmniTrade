---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-30
applies_to: src/cli/agent_api.py
---

# Agent SDK Integration

Omnitrade exposes a Python-callable API (`src/cli/agent_api.py`) that can be used
by an external Agent SDK integration. The function is an adapter over the same
Intent and Orchestrator path used by the CLI; it is not a second execution path.

## Entry points

<figure markdown="span">
  <img src="../../../assets/entry-agent-api.svg" alt="entry-agent-api" width="100%">
</figure>

（图源码 `docs/assets/entry-agent-api.dot`，重新生成：`scripts/render_diagrams.sh entry-agent-api`）

**两条入口汇聚到同一个 `Orchestrator.submit()`，这是这份设计的全部要点。**
`submit_intent_from_dict()` 只做三件事：把字典映射成 `Intent`、装配依赖、调用 `submit()`。
它**不重新实现任何执行逻辑**——没有第二套校验、没有绕过 `NEEDS_MANUAL` 阻断态的后门。
`agent_api.py` 位于 `src/cli/` 而不是 `src/coordinator/`，也是同一个理由：
它是入口适配器，不是执行内核的一部分。

## Quick start

```python
from src.cli.agent_api import submit_intent_from_dict
from src.market.instrument import NetworkType

result = await submit_intent_from_dict(
    {
        "base": "BTC",
        "product": "spot",
        "side": "buy",
        "total_notional_usd": 1000,
        "split": {"binance": 0.5, "hyperliquid": 0.5},
    },
    dry_run=True,
    target_network=NetworkType.TESTNET,
)
print(result["status"])  # "DRY_RUN"
```

The function returns the JSON-friendly coordinator result. The CLI formats the same outcome,
flattening dry-run plan legs for presentation.

`target_network` is an optional `NetworkType` override applied to both endpoints and credentials.
Without it, each venue uses its configured `default_network` (or `testnet` if absent).
`secrets_config_path=None` selects `secrets.testnet.yaml` / `secrets.mainnet.yaml` beside the
exchange configuration. An explicit path must carry a `network` marker matching every selected
venue; an old unmarked secrets file is rejected. Shared Telegram credentials remain independent.
See [Configuration](../../user-guide/configuration/index.md) for the file contract.

## Intent dictionary schema

| Key | Type | Required | Default |
|---|---|---|---|
| `base` | str | yes | — |
| `total_notional_usd` | float | except close_all | — |
| `contract_type` | str | no | `None` (perp selects linear) |
| `settlement_asset` | str | no | `None` |
| `position_effect` | str | no | `"open"` |
| `close_all` | bool | no | `False` |
| `quantity_native` | float | no | `None` |
| `split` | dict[str, float] | yes | — |
| `product` | str | no | `"spot"` |
| `side` | str | no | `"buy"` |
| `quote_preference` | list[str] | no | `[]` |
| `order_type` | str | no | `"market"` |
| `leverage` | int | no | `1` |
| `max_slippage_pct` | float | no | `None` |
| `max_fee_usd` | float | no | `None` |
| `max_funding_rate_pct` | float | no | `None` |
| `execute_timeout_seconds` | int | no | `30` |
| `time_in_force` | str | no | `None` |
| `leg_configs` | dict[str, dict] | no | `{}` |

`leg_configs` also accepts per-venue `contract_type` and `settlement_asset`. `close_all` is only
valid for a single perp close and allows an omitted notional; `quantity_native` is a single-leg
amount with a maximum USD budget and excludes `close_all`. Full field semantics and results are
in the [Python API reference](../../user-guide/api/index.md).

## External integration boundary

An external project may register `submit_intent_from_dict` as a tool so users
can express intent in natural language:

> "buy $1000 of BTC across Binance and Hyperliquid, 50/50 split"

Natural-language interpretation, tool registration, authentication and policy
remain outside this repository. The adapter accepts a structured dictionary,
constructs the canonical `Intent`, and returns the same JSON-friendly result as
the CLI. Do not add a second set of domain states or execution semantics here.
