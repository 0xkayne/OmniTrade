"""Protected concurrent leg execution with durable, individually tracked orders."""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from src.exchange.base import BaseExchange
from src.exchange.order import OrderRequest, OrderSnapshot
from src.market.quote_fetcher import QuoteFetcher
from src.observability.metrics import MetricsEmitter, NoopMetrics
from src.persistence.store import PersistenceStore

from .intent import Intent
from .leg_orders import LegOrderManager
from .plan import Plan, PlannedLeg
from .protection import LegProtection, build_leg_protection, compute_leg_qty
from .timing import TimingCollector


@dataclass
class LegExecution:
    leg: PlannedLeg
    leg_id: str
    status: str
    side: str = "buy"
    order_id: str | None = None
    filled_amount: float = 0.0
    avg_price: float | None = None
    fee: float = 0.0
    error: str | None = None
    snapshots: list[OrderSnapshot] = field(default_factory=list)

    @property
    def has_complete_fees(self) -> bool:
        return bool(self.snapshots) and all(snapshot.fee_usd is not None for snapshot in self.snapshots)


@dataclass
class ExecutionResult:
    status: str
    legs: list[LegExecution]
    started_at: float
    completed_at: float
    intent: Intent | None = None


class Executor:
    """Preflight all legs before any order, then execute with fixed price bounds."""

    def __init__(
        self,
        exchanges: dict[str, BaseExchange],
        store: PersistenceStore,
        poll_interval_ms: int = 500,
        use_websocket: bool = True,
        quote_fetcher: QuoteFetcher | None = None,
        metrics: MetricsEmitter | None = None,
    ):
        self._exchanges = exchanges
        self._store = store
        self._poll_interval_ms = poll_interval_ms
        self._use_websocket = use_websocket
        self._quote_fetcher = quote_fetcher or QuoteFetcher(exchanges)
        self._metrics = metrics or NoopMetrics()

    async def execute(self, plan: Plan, timing: TimingCollector | None = None) -> ExecutionResult:
        started = time.monotonic()
        deadline = started + plan.intent.execute_timeout_seconds
        try:
            protections = [build_leg_protection(leg, plan.intent) for leg in plan.legs]
            if not plan.is_acceptable or not plan.legs:
                raise ValueError("Plan is not acceptable")
            if plan.intent.min_fill_ratio < 1 and len(plan.legs) > 1:
                raise ValueError("min_fill_ratio < 1 requires a single leg; multi-leg ratios must stay intact")
            await asyncio.wait_for(self._preflight(plan, protections), max(0.001, deadline - time.monotonic()))
        except Exception as exc:
            await self._store.update_intent_status(plan.intent.intent_id, "REJECTED")
            await self._store.append_event(plan.intent.intent_id, "execution_rejected", {"reason": str(exc)})
            return ExecutionResult("REJECTED", [], started, time.monotonic(), plan.intent)

        executions = []
        for leg in plan.legs:
            leg_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{plan.intent.intent_id}:{leg.venue}").hex
            await self._store.create_leg(
                leg_id=leg_id,
                intent_id=plan.intent.intent_id,
                venue=leg.venue,
                instrument_venue_symbol=leg.instrument.venue_symbol,
                instrument_base=leg.instrument.base.symbol,
                instrument_quote=leg.instrument.quote.symbol,
                instrument_market_type=leg.instrument.market_type,
                quote_preference_matched=leg.quote_matched,
                planned_notional_usd=leg.planned_notional_usd,
                planned_qty_base=leg.planned_qty_base,
                leverage=leg.leverage,
                funding_rate_at_plan=leg.funding_rate,
                next_funding_time_at_plan=leg.next_funding_time,
            )
            await self._store.update_leg(
                leg_id, execution_context_json=json.dumps(asdict(leg), default=lambda value: value.value)
            )
            executions.append(LegExecution(leg, leg_id, "PENDING_SEND", side=leg.side))
        await self._store.update_intent_status(plan.intent.intent_id, "EXECUTING")
        await asyncio.gather(
            *(
                self._execute_leg(lex, protection, plan.intent, deadline, timing)
                for lex, protection in zip(executions, protections, strict=True)
            )
        )
        all_filled = all(lex.status == "FILLED" and lex.error is None for lex in executions)
        if plan.intent.max_fee_usd is not None and sum(lex.fee for lex in executions) > plan.intent.max_fee_usd:
            all_filled = False
        if (
            plan.intent.max_total_cost_usd is not None
            and sum(self._cost(lex) for lex in executions) > plan.intent.max_total_cost_usd
        ):
            all_filled = False
        status = "ALL_FILLED" if all_filled else "PARTIAL_FILLED"
        await self._store.update_intent_status(plan.intent.intent_id, status)
        return ExecutionResult(status, executions, started, time.monotonic(), plan.intent)

    async def _preflight(self, plan: Plan, protections: list[LegProtection]) -> None:
        for leg, protection in zip(plan.legs, protections, strict=True):
            exchange = self._exchanges[leg.venue]
            capabilities = exchange.order_capabilities(leg.instrument)
            if not capabilities.has_client_order_id or protection.time_in_force not in capabilities.time_in_force:
                raise ValueError(f"{leg.venue}: requested order protection is unsupported")
            if "IOC" not in capabilities.time_in_force:
                raise ValueError(f"{leg.venue}: protected compensation is unsupported")
            if leg.instrument.market_type == "perp" and leg.leverage > 1:
                await exchange.set_leverage(leg.leverage, symbol=leg.instrument.venue_symbol)
            self._quantities(leg, protection, plan.intent)
        quotes = await asyncio.gather(
            *(
                self._quote_fetcher.fetch(leg.instrument, enrich_funding=False, enrich_statistics=False)
                for leg in plan.legs
            )
        )
        fee_usd = cost_usd = 0.0
        for leg, quote, protection in zip(plan.legs, quotes, protections, strict=True):
            protection.validate_quote(quote, leg.planned_qty_base, leg.side)
            fill = quote.estimate_fill(leg.planned_qty_base, leg.side)
            fee = fill.avg_price * leg.planned_qty_base * leg.instrument.taker_fee_rate
            adverse = (fill.avg_price - protection.reference_price) * (1 if leg.side == "buy" else -1)
            fee_usd += fee
            cost_usd += max(0, adverse) * leg.planned_qty_base + fee
        if plan.intent.max_fee_usd is not None and fee_usd > plan.intent.max_fee_usd:
            raise ValueError("Fresh aggregate fee exceeds max_fee_usd")
        if plan.intent.max_total_cost_usd is not None and cost_usd > plan.intent.max_total_cost_usd:
            raise ValueError("Fresh aggregate cost exceeds max_total_cost_usd")

    @staticmethod
    def _quantities(leg: PlannedLeg, protection: LegProtection, intent: Intent) -> list[float]:
        count = 1
        if intent.max_order_notional_usd is not None:
            count = math.ceil(leg.planned_qty_base * protection.limit_price / intent.max_order_notional_usd)
        qty = compute_leg_qty(leg.instrument, leg.planned_qty_base / count)
        if qty <= 0 or qty < leg.instrument.min_qty:
            raise ValueError(f"{leg.venue}: split quantity is below venue minimum")
        remainder = float(Decimal(str(leg.planned_qty_base)) - Decimal(str(qty)) * (count - 1))
        quantities = [qty] * (count - 1) + [compute_leg_qty(leg.instrument, remainder)]
        for amount in quantities:
            if amount < leg.instrument.min_qty or amount * protection.limit_price < leg.instrument.min_notional:
                raise ValueError(f"{leg.venue}: split notional is below venue minimum")
            if intent.max_order_notional_usd and amount * protection.limit_price > intent.max_order_notional_usd + 1e-8:
                raise ValueError(f"{leg.venue}: split rounding exceeds max_order_notional_usd")
        return quantities

    async def _execute_leg(
        self,
        lex: LegExecution,
        protection: LegProtection,
        intent: Intent,
        deadline: float,
        timing: TimingCollector | None,
    ) -> None:
        manager = LegOrderManager(
            self._exchanges[lex.leg.venue],
            self._store,
            lex.leg.instrument,
            self._poll_interval_ms,
            self._use_websocket,
            self._metrics,
        )
        started = time.monotonic()
        try:
            for index, qty in enumerate(self._quantities(lex.leg, protection, intent)):
                if time.monotonic() >= deadline:
                    raise TimeoutError("execution deadline elapsed")
                quote = await asyncio.wait_for(
                    self._quote_fetcher.fetch(lex.leg.instrument, enrich_funding=False, enrich_statistics=False),
                    max(0.001, deadline - time.monotonic()),
                )
                protection.validate_quote(quote, qty, lex.side)
                fill = quote.estimate_fill(qty, lex.side)
                fee = fill.avg_price * qty * lex.leg.instrument.taker_fee_rate
                ratio = intent.split[lex.leg.venue]
                if intent.max_fee_usd is not None and lex.fee + fee > intent.max_fee_usd * ratio:
                    raise ValueError("remaining per-leg fee budget is insufficient")
                adverse = (fill.avg_price - protection.reference_price) * (1 if lex.side == "buy" else -1)
                if (
                    intent.max_total_cost_usd is not None
                    and self._cost(lex) + max(0, adverse) * qty + fee > intent.max_total_cost_usd * ratio
                ):
                    raise ValueError("remaining per-leg cost budget is insufficient")
                self._metrics.histogram("quote.age_ms", quote.age_ms, {"venue": lex.leg.venue})
                request = OrderRequest(
                    lex.leg.instrument.venue_symbol,
                    lex.side,
                    qty,
                    "limit",
                    protection.limit_price,
                    manager.client_order_id(lex.leg_id, "original", index),
                    lex.leg.instrument.market_type,
                    protection.time_in_force,
                    expires_at=min(quote.fetched_at, quote.exchange_at or quote.fetched_at)
                    + intent.max_quote_age_ms / 1000,
                )
                snapshot = await manager.execute(request, lex.leg_id, intent.intent_id, "original", deadline)
                lex.snapshots.append(snapshot)
                lex.order_id = snapshot.order_id or lex.order_id
                if snapshot.status == "rejected":
                    row = await self._store.get_order_row(request.client_order_id)
                    lex.error = row.error_msg or f"{lex.leg_id}/{lex.leg.venue}: venue rejected order"
                self._aggregate(lex)
                if (
                    not snapshot.is_terminal
                    or snapshot.filled_qty_base is None
                    or (snapshot.filled_qty_base > 0 and snapshot.avg_price is None)
                ):
                    lex.status = "UNKNOWN"
                    row = await self._store.get_order_row(request.client_order_id)
                    lex.error = row.error_msg or f"{intent.intent_id}/{lex.leg_id}: incomplete order confirmation"
                    break
                if snapshot.avg_price is not None and (
                    (lex.side == "buy" and snapshot.avg_price > protection.limit_price + 1e-8)
                    or (lex.side == "sell" and snapshot.avg_price < protection.limit_price - 1e-8)
                ):
                    raise ValueError("actual fill exceeds protected price")
                if (
                    intent.max_fee_usd is not None or intent.max_total_cost_usd is not None
                ) and snapshot.fee_usd is None:
                    raise ValueError("actual fee cannot be valued in USD")
                if snapshot.filled_qty_base < qty - 1e-12:
                    break
            if lex.status != "UNKNOWN":
                if lex.filled_amount >= lex.leg.planned_qty_base * intent.min_fill_ratio - 1e-12:
                    lex.status = "FILLED"
                else:
                    lex.status = "PARTIAL_FILLED" if lex.filled_amount else "REJECTED"
                if lex.filled_amount > lex.leg.planned_qty_base + 1e-12:
                    raise ValueError("actual fill exceeds requested quantity")
        except Exception as exc:
            lex.error = f"{intent.intent_id}/{lex.leg_id}/{lex.leg.venue}: {type(exc).__name__}: {exc}"
            rows = await self._store.get_orders_for_leg(lex.leg_id, "original")
            lex.status = (
                "UNKNOWN"
                if any(row.status in ("UNKNOWN", "PENDING_SEND", "open") for row in rows)
                else ("PARTIAL_FILLED" if lex.filled_amount else "REJECTED")
            )
        await self._store.update_leg(
            lex.leg_id,
            status=lex.status,
            order_id=lex.order_id,
            filled_amount=lex.filled_amount,
            avg_price=lex.avg_price,
            fee_usd=lex.fee if lex.has_complete_fees else None,
            error_msg=lex.error,
        )
        if timing:
            timing.ensure_leg("execute", lex.leg.venue).update(
                create_order_ms=manager.submit_ms,
                poll_total_ms=manager.poll_total_ms,
                poll_attempts=manager.poll_attempts,
                execute_leg_ms=(time.monotonic() - started) * 1000,
            )
        if lex.avg_price:
            slippage = (lex.avg_price / protection.reference_price - 1) * (100 if lex.side == "buy" else -100)
            self._metrics.histogram("execution.slippage_pct", slippage, {"venue": lex.leg.venue})

    @staticmethod
    def _aggregate(lex: LegExecution) -> None:
        lex.filled_amount = sum(s.filled_qty_base or 0 for s in lex.snapshots)
        cost = sum((s.filled_qty_base or 0) * (s.avg_price or 0) for s in lex.snapshots)
        lex.avg_price = (
            cost / lex.filled_amount
            if lex.filled_amount and all(not s.filled_qty_base or s.avg_price is not None for s in lex.snapshots)
            else None
        )
        lex.fee = sum(s.fee_usd or 0 for s in lex.snapshots)

    @staticmethod
    def _cost(lex: LegExecution) -> float:
        reference = lex.leg.reference_price or lex.leg.estimated_fill.avg_price
        adverse = ((lex.avg_price or reference) - reference) * (1 if lex.side == "buy" else -1)
        return max(0, adverse) * lex.filled_amount + lex.fee
