"""Agent API — programmatic Python interface for oneFill.

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


async def submit_intent_from_dict(
    intent_dict: dict[str, Any],
    *,
    dry_run: bool = False,
    exchanges_config_path: Path = Path("config/exchanges.yaml"),
    secrets_config_path: Path = Path("config/secrets.yaml"),
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
) -> dict[str, Any]:
    """Submit an order from a plain dictionary (no CLI needed).

    All keyword arguments have the same defaults as the CLI, so simple
    callers only pass *intent_dict*.

    Returns the same JSON-friendly result dict that ``onefill order --json``
    produces::

        {"status": "ALL_FILLED", "intent_id": "...", "legs": [...], ...}
    """
    from src.cli.bootstrap import build_orchestrator
    from src.coordinator.intent import Intent

    intent_id = intent_dict.get("intent_id") or str(uuid.uuid4())
    orch = await build_orchestrator(
        exchanges_config_path=exchanges_config_path,
        secrets_config_path=secrets_config_path,
        sqlite_path=sqlite_path,
        jsonl_dir=jsonl_dir,
    )

    intent = Intent(
        intent_id=intent_id,
        base=intent_dict["base"],
        quote_preference=intent_dict.get("quote_preference", ["USDT", "USDC"]),
        product=intent_dict.get("product", "spot"),
        side=intent_dict.get("side", "buy"),
        order_type=intent_dict.get("order_type", "market"),
        total_notional_usd=float(intent_dict["total_notional_usd"]),
        split=intent_dict["split"],
        leverage=intent_dict.get("leverage", 1),
        limit_price=intent_dict.get("limit_price"),
        max_slippage_pct=intent_dict.get("max_slippage_pct"),
        max_fee_usd=intent_dict.get("max_fee_usd"),
        max_funding_rate_pct=intent_dict.get("max_funding_rate_pct"),
        execute_timeout_seconds=intent_dict.get("execute_timeout_seconds", 30),
        time_in_force=intent_dict.get("time_in_force"),
        leg_configs=intent_dict.get("leg_configs", {}),
    )

    return await orch.submit(intent, dry_run=dry_run)
