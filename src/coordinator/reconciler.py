"""Settle original orders before protected, confirmed compensation."""

import asyncio
import json
import time
from dataclasses import dataclass

from src.exchange.base import BaseExchange
from src.exchange.order import OrderRequest
from src.market.quote_fetcher import QuoteFetcher
from src.observability.metrics import MetricsEmitter, NoopMetrics
from src.persistence.store import PersistenceStore

from .executor import ExecutionResult, Executor, LegExecution
from .intent import Intent
from .leg_orders import LegOrderManager
from .protection import build_leg_protection, compute_leg_qty
from .timing import TimingCollector


@dataclass
class LegReconciliation:
    leg_id: str
    original_order_id: str
    reverse_side: str
    compensation_order_id: str | None = None
    compensation_status: str = "PENDING"
    filled_amount: float = 0.0
    residual_exposure_usd: float | None = None
    error: str | None = None


@dataclass
class ReconciliationResult:
    status: str
    legs: list[LegReconciliation]
    residual_exposure_usd: float | None = 0.0


class Reconciler:
    """Cancel and query every original order, then flatten only confirmed exposure."""

    def __init__(
        self,
        exchanges: dict[str, BaseExchange],
        store: PersistenceStore,
        quote_fetcher: QuoteFetcher | None = None,
        metrics: MetricsEmitter | None = None,
    ):
        self._exchanges = exchanges
        self._store = store
        self._quote_fetcher = quote_fetcher or QuoteFetcher(exchanges)
        self._metrics = metrics or NoopMetrics()

    async def reconcile(self, result: ExecutionResult, timing: TimingCollector | None = None) -> ReconciliationResult:
        active_legs = [lex for lex in result.legs if lex.status != "REJECTED" or lex.filled_amount > 0]
        if not active_legs:
            return ReconciliationResult("ROLLED_BACK", [], 0.0)
        intent = result.intent
        if intent is None:
            leg_row = await self._store.get_leg(active_legs[0].leg_id)
            if leg_row is None:
                raise ValueError(f"{active_legs[0].leg_id}: reconciliation requires a persisted leg")
            row = await self._store.get_intent(leg_row.intent_id)
            intent = Intent(**json.loads(row.raw_intent_json))
        if intent is None:
            return ReconciliationResult("ROLLED_BACK_FAILED", [], None)
        deadline = time.monotonic() + intent.reconcile_timeout_seconds
        reconciliations = await asyncio.gather(
            *(self._reconcile_leg(lex, intent, deadline, timing) for lex in active_legs)
        )
        is_complete = all(rec.compensation_status == "COMPENSATED" for rec in reconciliations)
        residual = (
            None
            if any(rec.residual_exposure_usd is None for rec in reconciliations)
            else sum(rec.residual_exposure_usd for rec in reconciliations)
        )
        return ReconciliationResult(
            "ROLLED_BACK" if is_complete else "ROLLED_BACK_FAILED",
            [rec for rec in reconciliations if rec.filled_amount > 0 or rec.error],
            residual,
        )

    async def _reconcile_leg(
        self, lex: LegExecution, intent: Intent, deadline: float, timing: TimingCollector | None
    ) -> LegReconciliation:
        rec = LegReconciliation(lex.leg_id, lex.order_id or "", "sell" if lex.side == "buy" else "buy")
        started = time.monotonic()
        try:
            if self._exchanges[lex.leg.venue].network_type != lex.leg.instrument.network:
                raise ValueError("recovery network does not match the persisted instrument")
            manager = LegOrderManager(
                self._exchanges[lex.leg.venue], self._store, lex.leg.instrument, metrics=self._metrics
            )
            rows = await self._store.get_orders_for_leg(lex.leg_id, "original")
            if rows:
                snapshots = await asyncio.gather(*(manager.cancel(row, deadline) for row in rows))
                lex.snapshots = snapshots
                Executor._aggregate(lex)
                await self._store.update_leg(
                    lex.leg_id,
                    filled_amount=lex.filled_amount,
                    avg_price=lex.avg_price,
                    fee_usd=lex.fee if lex.has_complete_fees else None,
                )
                if not all(manager._is_confirmed(snapshot) for snapshot in snapshots):
                    raise ValueError("original order remains unknown; compensation size is not established")
            elif lex.status not in ("REJECTED", "FILLED", "PARTIAL_FILLED", "PENDING_SEND", "CANCELLED"):
                raise ValueError("legacy order lacks recoverable client order ID")

            target_qty = lex.filled_amount
            if lex.leg.instrument.market_type == "spot":
                base_fee = sum(
                    float(fee.get("cost") or 0)
                    for snapshot in lex.snapshots
                    for fee in snapshot.fees
                    if fee.get("currency") == lex.leg.instrument.base.symbol
                )
                target_qty += -base_fee if lex.side == "buy" else base_fee
            rec.filled_amount = target_qty
            reference = lex.avg_price or lex.leg.reference_price or lex.leg.estimated_fill.avg_price
            rec.residual_exposure_usd = abs(target_qty) * reference
            if target_qty <= 1e-12:
                rec.compensation_status = "COMPENSATED"
                rec.residual_exposure_usd = 0.0
                lex.status = "CANCELLED"
                await self._store.update_leg(lex.leg_id, status="CANCELLED")
                return rec

            compensation_rows = await self._store.get_orders_for_leg(lex.leg_id, "compensation")
            if compensation_rows:
                # Recovery queries the original request; it never creates a replacement.
                snapshot = await manager.cancel(compensation_rows[0], deadline)
            else:
                protection = build_leg_protection(lex.leg, intent, side=rec.reverse_side, reference_price=reference)
                qty = compute_leg_qty(lex.leg.instrument, target_qty)
                if (
                    qty <= 0
                    or qty < lex.leg.instrument.min_qty
                    or qty * protection.limit_price < lex.leg.instrument.min_notional
                ):
                    raise ValueError("residual quantity cannot satisfy venue minimum")
                quote = await asyncio.wait_for(
                    self._quote_fetcher.fetch(lex.leg.instrument, enrich_funding=False, enrich_statistics=False),
                    max(0.001, deadline - time.monotonic()),
                )
                protection.validate_quote(quote, qty, rec.reverse_side)
                request = OrderRequest(
                    lex.leg.instrument.venue_symbol,
                    rec.reverse_side,
                    qty,
                    "limit",
                    protection.limit_price,
                    manager.client_order_id(lex.leg_id, "compensation", 0),
                    lex.leg.instrument.market_type,
                    protection.time_in_force,
                    lex.leg.instrument.market_type == "perp",
                    expires_at=min(quote.fetched_at, quote.exchange_at or quote.fetched_at)
                    + intent.max_quote_age_ms / 1000,
                )
                await self._store.update_leg(lex.leg_id, status="COMPENSATING")
                snapshot = await manager.execute(request, lex.leg_id, intent.intent_id, "compensation", deadline)
                if not manager._is_confirmed(snapshot):
                    row = await self._store.get_order_row(request.client_order_id)
                    snapshot = await manager.cancel(row, deadline)
            rec.compensation_order_id = snapshot.order_id
            compensated_qty = snapshot.filled_qty_base or 0.0
            if not manager._is_confirmed(snapshot):
                rec.residual_exposure_usd = None
                raise ValueError("compensation order remains unknown")
            residual_qty = target_qty - compensated_qty
            if lex.leg.instrument.market_type == "spot":
                fee_qty = sum(
                    float(fee.get("cost") or 0)
                    for fee in snapshot.fees
                    if fee.get("currency") == lex.leg.instrument.base.symbol
                )
                residual_qty += fee_qty if rec.reverse_side == "buy" else -fee_qty
            rec.residual_exposure_usd = abs(residual_qty) * reference
            saved_orders = await self._store.get_orders_for_leg(lex.leg_id, "compensation")
            saved_request = OrderRequest(**json.loads(saved_orders[0].request_json))
            await self._store.update_leg(
                lex.leg_id,
                compensation_order_id=snapshot.order_id,
                compensation_filled_amount=compensated_qty,
                compensation_avg_price=snapshot.avg_price,
                compensation_fee_usd=snapshot.fee_usd,
            )
            if abs(residual_qty) > 1e-12:
                raise ValueError("compensation left nonzero residual exposure")
            if snapshot.avg_price is not None and (
                (rec.reverse_side == "buy" and snapshot.avg_price > saved_request.price + 1e-8)
                or (rec.reverse_side == "sell" and snapshot.avg_price < saved_request.price - 1e-8)
            ):
                raise ValueError("compensation execution exceeded protected price")
            rec.compensation_status = "COMPENSATED"
            await self._store.update_leg(lex.leg_id, status="COMPENSATED")
        except Exception as exc:
            rec.compensation_status = "COMPENSATION_FAILED"
            rec.error = f"{intent.intent_id}/{lex.leg_id}/{lex.leg.venue}: {type(exc).__name__}: {exc}"
            await self._store.update_leg(lex.leg_id, status="COMPENSATION_FAILED", error_msg=rec.error)
        if timing:
            timing.ensure_leg("reconcile", lex.leg.venue)["compensate_order_ms"] = (time.monotonic() - started) * 1000
        self._metrics.increment(
            "compensation.completed", tags={"venue": lex.leg.venue, "status": rec.compensation_status}
        )
        return rec
