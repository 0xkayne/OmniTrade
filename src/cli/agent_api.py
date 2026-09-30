"""Agent API — programmatic Python interface for Omnitrade.

Future Phase 2 will register this as a Claude Agent SDK tool.  Stage 5
only ships the function and its tests; the actual Agent comes later.

Usage::

    from src.cli.agent_api import submit_intent_from_dict
    result = await submit_intent_from_dict({
        "base": "BTC",
        "product": "spot",
        "side": "buy",
        "total_notional_usd": 1000,
        "split": {"binance": 0.5, "hyperliquid": 0.5},
    })
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from src.market.instrument import NetworkType


async def submit_intent_from_dict(
    intent_dict: dict[str, Any],
    *,
    dry_run: bool = False,
    exchanges_config_path: Path = Path("config/exchanges.yaml"),
    secrets_config_path: Path | None = None,
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
    target_network: NetworkType | None = None,
) -> dict[str, Any]:
    """Submit an order from a plain dictionary (no CLI needed).

    Network selection defaults to each venue's configured network. Credentials
    are selected automatically unless a marked network file is supplied.

    Returns the JSON-friendly coordinator result. The CLI flattens dry-run
    plan legs for presentation::

        {"status": "ALL_FILLED", "intent_id": "...", "legs": [...], ...}
    """
    from src.cli.bootstrap import build_orchestrator
    from src.coordinator.intent import Intent

    intent_id = intent_dict.get("intent_id") or str(uuid.uuid4())
    intent = Intent(
        intent_id=intent_id,
        base=intent_dict["base"],
        quote_preference=intent_dict.get("quote_preference", ["USDT", "USDC"]),
        product=intent_dict.get("product", "spot"),
        side=intent_dict.get("side", "buy"),
        order_type=intent_dict.get("order_type", "market"),
        total_notional_usd=(
            float(intent_dict["total_notional_usd"]) if intent_dict.get("total_notional_usd") is not None else None
        ),
        split=intent_dict["split"],
        leverage=intent_dict.get("leverage", 1),
        limit_price=intent_dict.get("limit_price"),
        max_slippage_pct=intent_dict.get("max_slippage_pct"),
        max_spread_pct=intent_dict.get("max_spread_pct"),
        max_quote_age_ms=intent_dict.get("max_quote_age_ms", 1000.0),
        max_total_cost_usd=intent_dict.get("max_total_cost_usd"),
        max_order_notional_usd=intent_dict.get("max_order_notional_usd"),
        min_fill_ratio=intent_dict.get("min_fill_ratio", 1.0),
        compensation_slippage_pct=intent_dict.get("compensation_slippage_pct", 0.5),
        reconcile_timeout_seconds=intent_dict.get("reconcile_timeout_seconds", 10.0),
        max_fee_usd=intent_dict.get("max_fee_usd"),
        max_funding_rate_pct=intent_dict.get("max_funding_rate_pct"),
        execute_timeout_seconds=intent_dict.get("execute_timeout_seconds", 30),
        time_in_force=intent_dict.get("time_in_force"),
        leg_configs=intent_dict.get("leg_configs", {}),
        contract_type=intent_dict.get("contract_type"),
        settlement_asset=intent_dict.get("settlement_asset"),
        position_effect=intent_dict.get("position_effect", "open"),
        close_all=intent_dict.get("close_all", False),
        quantity_native=intent_dict.get("quantity_native"),
    )

    orch = await build_orchestrator(
        exchanges_config_path=exchanges_config_path,
        secrets_config_path=secrets_config_path,
        sqlite_path=sqlite_path,
        jsonl_dir=jsonl_dir,
        target_network=target_network,
    )
    try:
        return await orch.submit(intent, dry_run=dry_run)
    finally:
        await orch.close()
