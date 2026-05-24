"""
Executor: persist-before-send, concurrent order dispatch, fill polling.

Critical invariant: store.create_leg() must be called BEFORE exchange.create_order().
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.base_exchange import BaseExchange

    from .plan import Plan, PlannedLeg


@dataclass
class LegExecution:
    leg: PlannedLeg
    leg_id: str
    status: str  # FILLED, PARTIAL_FILLED, REJECTED, TIMEOUT, SENT
    side: str = "buy"  # "buy" or "sell" — needed by Reconciler for reverse direction
    order_id: str | None = None
    filled_amount: float = 0.0
    avg_price: float | None = None
    fee: float = 0.0
    error: str | None = None


@dataclass
class ExecutionResult:
    status: str  # ALL_FILLED or PARTIAL_FILLED
    legs: list[LegExecution]
    started_at: float
    completed_at: float


class Executor:
    """Executes a validated Plan: persist, send orders, poll fills.

    Usage:
        executor = Executor(exchanges, store)
        result = await executor.execute(plan)
    """

    def __init__(
        self,
        exchanges: dict[str, BaseExchange],
        store: Any,  # PersistenceStore interface
        poll_interval_ms: int = 500,
    ):
        self._exchanges = exchanges
        self._store = store
        self._poll_interval = poll_interval_ms / 1000.0

    async def execute(self, plan: Plan) -> ExecutionResult:
        started_at = time.time()
        deadline = started_at + plan.intent.execute_timeout_seconds

        # 1. Persist intent status transition
        await self._store.update_intent_status(plan.intent.intent_id, "EXECUTING")

        # 2. Create leg rows (persist BEFORE sending orders)
        leg_executions: list[LegExecution] = []
        for planned_leg in plan.legs:
            leg_id = str(uuid.uuid4())[:8]
            await self._store.create_leg(
                leg_id=leg_id,
                intent_id=plan.intent.intent_id,
                venue=planned_leg.venue,
                instrument_venue_symbol=planned_leg.instrument.venue_symbol,
                instrument_base=planned_leg.instrument.base.symbol,
                instrument_quote=planned_leg.instrument.quote.symbol,
                instrument_market_type=planned_leg.instrument.market_type,
                quote_preference_matched=planned_leg.quote_matched,
                planned_notional_usd=planned_leg.planned_notional_usd,
                planned_qty_base=planned_leg.planned_qty_base,
                funding_rate_at_plan=planned_leg.funding_rate,
                next_funding_time_at_plan=planned_leg.next_funding_time,
            )
            leg_executions.append(LegExecution(
                leg=planned_leg,
                leg_id=leg_id,
                status="PENDING_SEND",
                side=plan.intent.side,
            ))

        # 3. Send all orders concurrently
        send_tasks = [
            self._send_order(lex, plan) for lex in leg_executions
        ]
        await asyncio.gather(*send_tasks, return_exceptions=True)

        # 4. Poll until all filled or deadline
        unfilled = [lex for lex in leg_executions if lex.status not in ("FILLED", "REJECTED")]
        while time.time() < deadline and unfilled:
            await asyncio.sleep(self._poll_interval)
            poll_tasks = [self._poll_leg(lex) for lex in unfilled]
            await asyncio.gather(*poll_tasks, return_exceptions=True)
            unfilled = [lex for lex in leg_executions if lex.status in ("SENT", "PENDING_SEND")]

        # Mark any still-unfilled legs as TIMEOUT
        for lex in leg_executions:
            if lex.status in ("SENT", "PENDING_SEND"):
                lex.status = "TIMEOUT"
                lex.error = "fill polling timed out"
                await self._store.update_leg(lex.leg_id, status="TIMEOUT", error_msg=lex.error)

        # Determine overall status
        all_filled = all(lex.status == "FILLED" for lex in leg_executions)
        status = "ALL_FILLED" if all_filled else "PARTIAL_FILLED"
        await self._store.update_intent_status(plan.intent.intent_id, status)

        return ExecutionResult(
            status=status,
            legs=leg_executions,
            started_at=started_at,
            completed_at=time.time(),
        )

    async def _send_order(self, lex: LegExecution, plan: Plan) -> None:
        """Send one order and update the leg. Errors are captured in lex.error."""
        exchange = self._exchanges.get(lex.leg.venue)
        if exchange is None:
            lex.status = "REJECTED"
            lex.error = f"no exchange for venue {lex.leg.venue}"
            await self._store.update_leg(lex.leg_id, status="REJECTED", error_msg=lex.error)
            return

        inst = lex.leg.instrument
        if plan.intent.order_type == "limit":
            price = plan.intent.limit_price
        else:
            # Some venues (notably Hyperliquid) don't have a true market-order
            # primitive — they need a reference price to compute a slippage-bounded
            # IOC limit. Use the Plan's depth-aware estimated fill price; other
            # venues simply ignore the price arg for market orders.
            price = lex.leg.estimated_fill.avg_price or None

        params: dict[str, Any] = {}
        if (
            plan.intent.order_type == "market"
            and lex.leg.venue == "hyperliquid"
            and plan.intent.max_slippage_pct is not None
        ):
            params["slippage"] = str(plan.intent.max_slippage_pct / 100.0)

        try:
            order = await exchange.create_order(
                symbol=inst.venue_symbol,
                order_type=plan.intent.order_type,
                side=plan.intent.side,
                amount=lex.leg.planned_qty_base,
                price=price,
                params=params or None,
            )
            lex.order_id = order["id"]
            lex.status = "SENT"
            fee_cost = 0.0
            if isinstance(order.get("fee"), dict):
                fee_cost = order["fee"].get("cost", 0.0) or 0.0
            lex.fee = fee_cost

            await self._store.update_leg(
                lex.leg_id,
                status="SENT",
                order_id=order["id"],
                sent_at=time.time(),
                fee_usd=fee_cost,
            )
        except Exception as e:
            lex.status = "REJECTED"
            lex.error = str(e)
            await self._store.update_leg(lex.leg_id, status="REJECTED", error_msg=str(e))

    async def _poll_leg(self, lex: LegExecution) -> None:
        """Poll one leg's order status. Update if filled."""
        exchange = self._exchanges.get(lex.leg.venue)
        if exchange is None or lex.order_id is None:
            return

        inst = lex.leg.instrument
        try:
            order = await exchange.fetch_order(lex.order_id, inst.venue_symbol)
            status = order.get("status", "")
            if status == "closed":
                lex.status = "FILLED"
                lex.filled_amount = order.get("filled", 0.0) or 0.0
                lex.avg_price = order.get("average")
                fee = 0.0
                if isinstance(order.get("fee"), dict):
                    fee = order["fee"].get("cost", 0.0) or 0.0
                lex.fee = fee
                await self._store.update_leg(
                    lex.leg_id,
                    status="FILLED",
                    filled_amount=lex.filled_amount,
                    avg_price=lex.avg_price,
                    fee_usd=fee,
                )
            elif status == "canceled":
                lex.status = "REJECTED"
                lex.error = "order canceled by venue"
                await self._store.update_leg(lex.leg_id, status="REJECTED", error_msg=lex.error)
        except Exception:
            # Log and keep polling — one fetch failure doesn't fail the whole poll
            pass
