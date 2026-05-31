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
    from .timing import TimingCollector


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

    async def execute(self, plan: Plan, timing: TimingCollector | None = None) -> ExecutionResult:
        started_at = time.perf_counter()
        deadline = time.time() + plan.intent.execute_timeout_seconds

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
                leverage=planned_leg.leverage,
            )
            leg_executions.append(
                LegExecution(
                    leg=planned_leg,
                    leg_id=leg_id,
                    status="PENDING_SEND",
                    side=planned_leg.side,
                )
            )

        # 3. Send all orders concurrently
        send_tasks = [self._send_order(lex, plan, timing=timing) for lex in leg_executions]
        await asyncio.gather(*send_tasks, return_exceptions=True)

        # 4. Poll until all filled or deadline.
        # Start with an immediate poll (no sleep) to catch orders that filled
        # during the create_order call, then use adaptive backoff.
        unfilled = [lex for lex in leg_executions if lex.status in ("SENT", "PENDING_SEND")]
        if unfilled:
            poll_tasks = [self._poll_leg(lex, timing=timing) for lex in unfilled]
            await asyncio.gather(*poll_tasks, return_exceptions=True)
            unfilled = [lex for lex in leg_executions if lex.status in ("SENT", "PENDING_SEND")]

        backoff = 0.05  # 50ms initial
        while time.time() < deadline and unfilled:
            await asyncio.sleep(backoff)
            poll_tasks = [self._poll_leg(lex, timing=timing) for lex in unfilled]
            await asyncio.gather(*poll_tasks, return_exceptions=True)
            unfilled = [lex for lex in leg_executions if lex.status in ("SENT", "PENDING_SEND")]
            backoff = min(backoff * 2, self._poll_interval)

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
            completed_at=time.perf_counter(),
        )

    async def _send_order(self, lex: LegExecution, plan: Plan, timing: TimingCollector | None = None) -> None:
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

        # Set leverage on the exchange before placing perp orders.
        # Best-effort: adapters that don't implement it raise NotImplementedError.
        if inst.market_type == "perp" and lex.leg.leverage > 1:
            if timing:
                timing.mark(f"execute.{lex.leg.venue}.set_leverage")
            try:
                await exchange.set_leverage(lex.leg.leverage, symbol=inst.venue_symbol)
            except NotImplementedError:
                pass
            finally:
                if timing:
                    leg_t = timing.ensure_leg("execute", lex.leg.venue)
                    leg_t["set_leverage_ms"] = timing.pop(f"execute.{lex.leg.venue}.set_leverage")

        params: dict[str, Any] = {}
        if (
            plan.intent.order_type == "market"
            and lex.leg.venue == "hyperliquid"
            and plan.intent.max_slippage_pct is not None
        ):
            params["slippage"] = str(plan.intent.max_slippage_pct / 100.0)

        try:
            if timing:
                timing.mark(f"execute.{lex.leg.venue}.create_order")
            order = await exchange.create_order(
                symbol=inst.venue_symbol,
                order_type=plan.intent.order_type,
                side=lex.side,
                amount=lex.leg.planned_qty_base,
                price=price,
                params=params or None,
            )
            if timing:
                leg_t = timing.ensure_leg("execute", lex.leg.venue)
                leg_t["create_order_ms"] = timing.pop(f"execute.{lex.leg.venue}.create_order")
            lex.order_id = order["id"]
            fee_cost = 0.0
            if isinstance(order.get("fee"), dict):
                fee_cost = order["fee"].get("cost", 0.0) or 0.0
            lex.fee = fee_cost

            if order.get("status") == "closed":
                await self._mark_filled(lex, order, fee_cost, order_id=order["id"], sent_at=time.time())
            else:
                lex.status = "SENT"
                await self._store.update_leg(
                    lex.leg_id,
                    status="SENT",
                    order_id=order["id"],
                    sent_at=time.time(),
                    fee_usd=fee_cost,
                )
        except Exception as e:
            if timing:
                label = f"execute.{lex.leg.venue}.create_order"
                if timing.has_mark(label):
                    leg_t = timing.ensure_leg("execute", lex.leg.venue)
                    leg_t["create_order_ms"] = timing.pop(label)
            lex.status = "REJECTED"
            lex.error = str(e)
            await self._store.update_leg(lex.leg_id, status="REJECTED", error_msg=str(e))

    async def _mark_filled(self, lex: LegExecution, order: dict, fee: float, *,
                           order_id: str | None = None, sent_at: float | None = None) -> None:
        """Record a filled leg from an order response. Shared by _send_order and _poll_leg."""
        lex.status = "FILLED"
        lex.filled_amount = order.get("filled", 0.0) or lex.leg.planned_qty_base
        lex.avg_price = order.get("average")
        lex.fee = fee
        await self._store.update_leg(
            lex.leg_id,
            status="FILLED",
            order_id=order_id or lex.order_id,
            sent_at=sent_at,
            filled_amount=lex.filled_amount,
            avg_price=lex.avg_price,
            fee_usd=fee,
        )

    async def _poll_leg(self, lex: LegExecution, timing: TimingCollector | None = None) -> None:
        """Poll one leg's order status. Update if filled."""
        exchange = self._exchanges.get(lex.leg.venue)
        if exchange is None or lex.order_id is None:
            return

        inst = lex.leg.instrument
        t0 = time.perf_counter() if timing else None
        try:
            order = await exchange.fetch_order(lex.order_id, inst.venue_symbol)
            if timing:
                leg_t = timing.ensure_leg("execute", lex.leg.venue)
                attempt_ms = (time.perf_counter() - t0) * 1000.0
                leg_t["poll_attempts"] = leg_t.get("poll_attempts", 0) + 1
                leg_t["poll_total_ms"] = leg_t.get("poll_total_ms", 0.0) + attempt_ms
                if attempt_ms > leg_t.get("poll_max_ms", 0.0):
                    leg_t["poll_max_ms"] = attempt_ms
            if order.get("status") == "closed":
                fee = 0.0
                if isinstance(order.get("fee"), dict):
                    fee = order["fee"].get("cost", 0.0) or 0.0
                await self._mark_filled(lex, order, fee)
            elif order.get("status") == "canceled":
                lex.status = "REJECTED"
                lex.error = "order canceled by venue"
                await self._store.update_leg(lex.leg_id, status="REJECTED", error_msg=lex.error)
        except Exception:
            # Log and keep polling — one fetch failure doesn't fail the whole poll
            if timing:
                leg_t = timing.ensure_leg("execute", lex.leg.venue)
                attempt_ms = (time.perf_counter() - t0) * 1000.0
                leg_t["poll_attempts"] = leg_t.get("poll_attempts", 0) + 1
                leg_t["poll_total_ms"] = leg_t.get("poll_total_ms", 0.0) + attempt_ms
