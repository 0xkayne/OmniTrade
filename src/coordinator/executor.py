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

from .account_type import account_type_params, extract_fee_usd

if TYPE_CHECKING:
    from src.core.base_exchange import BaseExchange

    from .plan import Plan, PlannedLeg
    from .timing import TimingCollector


WEBSOCKET_CONFIRM_GRACE_SECONDS = 2.0
WEBSOCKET_CONFIRM_GRACE_FRACTION = 0.2


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


def _is_early_terminate(legs: list[LegExecution]) -> bool:
    """Some legs filled AND some definitively failed — let Reconciler handle the rest."""
    return (
        any(lex.status == "FILLED" for lex in legs)
        and any(lex.status in ("REJECTED", "CANCELLED") for lex in legs)
    )


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
        use_websocket: bool = True,
    ):
        self._exchanges = exchanges
        self._store = store
        self._poll_interval = poll_interval_ms / 1000.0
        self._use_websocket = use_websocket

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

        # 4. Confirm fills — try WebSocket first, fall back to HTTP polling.
        unfilled = [lex for lex in leg_executions if lex.status in ("SENT", "PENDING_SEND")]
        if unfilled:
            await self._confirm_fills(unfilled, leg_executions, deadline, timing)

        # 5. Mark remaining unfilled legs.
        # If some leg filled AND some leg definitively failed, leave SENT legs
        # as-is for Reconciler to cancel (early termination). Otherwise mark TIMEOUT.
        early_terminate = _is_early_terminate(leg_executions)

        for lex in leg_executions:
            if lex.status in ("SENT", "PENDING_SEND"):
                if early_terminate:
                    pass  # Reconciler will cancel
                else:
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
        params: dict[str, Any] = account_type_params(inst.market_type)
        if inst.market_type == "perp" and lex.leg.leverage > 1:
            if timing:
                timing.mark(f"execute.{lex.leg.venue}.set_leverage")
            try:
                await exchange.set_leverage(lex.leg.leverage, symbol=inst.venue_symbol, params=params)
            except NotImplementedError:
                pass
            finally:
                if timing:
                    leg_t = timing.ensure_leg("execute", lex.leg.venue)
                    leg_t["set_leverage_ms"] = timing.pop(f"execute.{lex.leg.venue}.set_leverage")

        if plan.intent.time_in_force is not None:
            params["timeInForce"] = plan.intent.time_in_force
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
            fee_cost = extract_fee_usd(order)
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
            order = await exchange.fetch_order(
                lex.order_id,
                inst.venue_symbol,
                params=account_type_params(inst.market_type),
            )
            if timing:
                leg_t = timing.ensure_leg("execute", lex.leg.venue)
                attempt_ms = (time.perf_counter() - t0) * 1000.0
                leg_t["poll_attempts"] = leg_t.get("poll_attempts", 0) + 1
                leg_t["poll_total_ms"] = leg_t.get("poll_total_ms", 0.0) + attempt_ms
                if attempt_ms > leg_t.get("poll_max_ms", 0.0):
                    leg_t["poll_max_ms"] = attempt_ms
            if order.get("status") == "closed":
                fee = extract_fee_usd(order)
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

    # ── Fill confirmation (hybrid WS + HTTP) ─────────────────────

    async def _confirm_fills(
        self,
        unfilled: list[LegExecution],
        all_legs: list[LegExecution],
        deadline: float,
        timing: TimingCollector | None = None,
    ) -> None:
        """Confirm fills using WebSocket (best-effort) then HTTP polling for remainder.

        WebSocket watching is opportunistic — venues that don't support it, or whose
        WS connection fails, are handled by the HTTP fallback after a short grace period.
        """
        order_lookup: dict[str, LegExecution] = {
            lex.order_id: lex for lex in all_legs if lex.order_id
        }

        # Phase 1: best-effort WebSocket watching per venue
        if self._use_websocket:
            ws_tasks: dict[str, asyncio.Task] = {}
            for lex in unfilled:
                venue = lex.leg.venue
                if venue in ws_tasks:
                    continue
                exchange = self._exchanges.get(venue)
                if exchange is None:
                    continue
                ws_tasks[venue] = asyncio.create_task(
                    self._ws_watch_venue(
                        exchange, venue, lex.leg.instrument.venue_symbol,
                        account_type_params(lex.leg.instrument.market_type),
                        order_lookup, deadline,
                    )
                )
            if ws_tasks:
                remaining = max(0.0, deadline - time.time())
                ws_timeout = min(
                    WEBSOCKET_CONFIRM_GRACE_SECONDS,
                    remaining * WEBSOCKET_CONFIRM_GRACE_FRACTION,
                )
                _, pending = await asyncio.wait(
                    list(ws_tasks.values()),
                    timeout=ws_timeout,
                    return_when=asyncio.ALL_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

        # Phase 2: HTTP polling for any legs still unfilled
        still_unfilled = [lex for lex in unfilled if lex.status in ("SENT", "PENDING_SEND")]
        if still_unfilled and time.time() < deadline:
            await self._poll_fills_http(still_unfilled, all_legs, deadline, timing)

    async def _ws_watch_venue(
        self,
        exchange: BaseExchange,
        venue: str,
        symbol: str,
        params: dict[str, Any],
        order_lookup: dict[str, LegExecution],
        deadline: float,
    ) -> None:
        """Watch a venue's orders via WebSocket. Marks filled legs directly."""
        backoff = 1.0
        while time.time() < deadline:
            # Stop if no more unfilled legs from this venue
            active_ids = {
                oid for oid, lex in order_lookup.items()
                if lex.leg.venue == venue and lex.status in ("SENT", "PENDING_SEND")
            }
            if not active_ids:
                return

            try:
                remaining = max(0.1, deadline - time.time())
                order = await asyncio.wait_for(
                    exchange.watch_orders(symbol, params=params),
                    timeout=min(5.0, remaining),
                )
                backoff = 1.0  # reset on success
            except asyncio.TimeoutError:
                continue
            except NotImplementedError:
                return
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            oid = order.get("id")
            if oid is None or oid not in order_lookup:
                continue

            if order.get("status") == "closed":
                lex = order_lookup[oid]
                fee = extract_fee_usd(order)
                await self._mark_filled(lex, order, fee)
            elif order.get("status") == "canceled":
                lex = order_lookup[oid]
                lex.status = "REJECTED"
                lex.error = "order canceled by venue"
                await self._store.update_leg(lex.leg_id, status="REJECTED", error_msg=lex.error)

    async def _poll_fills_http(
        self,
        unfilled: list[LegExecution],
        all_legs: list[LegExecution],
        deadline: float,
        timing: TimingCollector | None = None,
    ) -> None:
        """HTTP polling fallback with adaptive backoff and early termination."""
        # Immediate poll (no sleep first round)
        poll_tasks = [self._poll_leg(lex, timing=timing) for lex in unfilled]
        await asyncio.gather(*poll_tasks, return_exceptions=True)
        unfilled[:] = [lex for lex in unfilled if lex.status in ("SENT", "PENDING_SEND")]

        backoff = 0.05  # 50ms initial
        while time.time() < deadline and unfilled:
            await asyncio.sleep(backoff)
            poll_tasks = [self._poll_leg(lex, timing=timing) for lex in unfilled]
            await asyncio.gather(*poll_tasks, return_exceptions=True)
            unfilled[:] = [lex for lex in unfilled if lex.status in ("SENT", "PENDING_SEND")]

            if _is_early_terminate(all_legs):
                break

            backoff = min(backoff * 2, self._poll_interval)
