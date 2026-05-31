"""
Reconciler: handle PARTIAL_FILLED results by reverse-ordering filled legs
and canceling unfilled pending legs. MVP (spot only): simple opposite-side
market orders, no reduce_only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.base_exchange import BaseExchange

    from .executor import ExecutionResult
    from .timing import TimingCollector


@dataclass
class LegReconciliation:
    leg_id: str
    original_order_id: str
    reverse_side: str  # "sell" (if original was buy) or "buy"
    compensation_order_id: str | None = None
    compensation_status: str = "PENDING"  # COMPENSATED or COMPENSATION_FAILED
    filled_amount: float = 0.0


@dataclass
class ReconciliationResult:
    status: str  # ROLLED_BACK or ROLLED_BACK_FAILED (= NEEDS_MANUAL)
    legs: list[LegReconciliation]
    residual_exposure_usd: float = 0.0


class Reconciler:
    """Compensate partially-filled executions by reversing filled positions.

    MVP (spot only):
    - Reverse = opposite-side market order for exact filled amount.
    - No reduce_only, no position-size tracking.
    - Perp-specific logic comes in Stage 4.
    """

    def __init__(self, exchanges: dict[str, BaseExchange], store: Any):
        self._exchanges = exchanges
        self._store = store

    async def reconcile(self, result: ExecutionResult, timing: TimingCollector | None = None) -> ReconciliationResult:
        leg_recons: list[LegReconciliation] = []
        compensation_tasks = []

        for lex in result.legs:
            if lex.status in ("FILLED", "PARTIAL_FILLED") and lex.filled_amount > 0:
                rec = LegReconciliation(
                    leg_id=lex.leg_id,
                    original_order_id=lex.order_id or "",
                    reverse_side="sell" if lex.side == "buy" else "buy",
                    filled_amount=lex.filled_amount,
                )
                leg_recons.append(rec)
                compensation_tasks.append(self._compensate(rec, lex, timing=timing))
            elif lex.status in ("SENT", "PENDING_SEND") and lex.order_id:
                # Try to cancel unfilled pending orders
                await self._cancel_pending(lex, timing=timing)

        # Run compensations concurrently
        if compensation_tasks:
            await asyncio.gather(*compensation_tasks, return_exceptions=True)

        # Determine final status
        all_compensated = all(
            rec.compensation_status == "COMPENSATED" for rec in leg_recons
        )
        if not leg_recons:
            recovery_status = "ROLLED_BACK"
        elif all_compensated:
            recovery_status = "ROLLED_BACK"
        else:
            recovery_status = "ROLLED_BACK_FAILED"

        # Calculate residual exposure
        residual = sum(
            rec.filled_amount for rec in leg_recons
            if rec.compensation_status == "COMPENSATION_FAILED"
        )

        return ReconciliationResult(
            status=recovery_status,
            legs=leg_recons,
            residual_exposure_usd=residual,
        )

    async def _compensate(self, rec: LegReconciliation, lex, timing: TimingCollector | None = None) -> None:
        """Send a reverse market order for the filled amount."""
        exchange = self._exchanges.get(lex.leg.venue)
        if exchange is None:
            rec.compensation_status = "COMPENSATION_FAILED"
            return

        inst = lex.leg.instrument
        reverse_qty = rec.filled_amount

        # Some venues (Hyperliquid) require a reference price for market orders.
        # Use the actual fill price if known; otherwise fall back to the planned
        # estimated fill from the original leg.
        reference_price = lex.avg_price or lex.leg.estimated_fill.avg_price or None

        try:
            await self._store.update_leg(rec.leg_id, status="COMPENSATING")

            if timing:
                timing.mark(f"reconcile.{lex.leg.venue}.compensate_order")
            order = await exchange.create_order(
                symbol=inst.venue_symbol,
                order_type="market",
                side=rec.reverse_side,
                amount=reverse_qty,
                price=reference_price,
            )
            if timing:
                leg_t = timing.ensure_leg("reconcile", lex.leg.venue)
                leg_t["compensate_order_ms"] = timing.pop(f"reconcile.{lex.leg.venue}.compensate_order")
            rec.compensation_order_id = order["id"]
            rec.compensation_status = "COMPENSATED"
            await self._store.update_leg(
                rec.leg_id,
                status="COMPENSATED",
                compensation_order_id=order["id"],
                compensation_filled_amount=reverse_qty,
            )
        except Exception:
            if timing:
                label = f"reconcile.{lex.leg.venue}.compensate_order"
                if timing.has_mark(label):
                    leg_t = timing.ensure_leg("reconcile", lex.leg.venue)
                    leg_t["compensate_order_ms"] = timing.pop(label)
            rec.compensation_status = "COMPENSATION_FAILED"
            await self._store.update_leg(
                rec.leg_id,
                status="COMPENSATION_FAILED",
            )

    async def _cancel_pending(self, lex, timing: TimingCollector | None = None) -> None:
        """Try to cancel a still-pending order before it fills."""
        exchange = self._exchanges.get(lex.leg.venue)
        if exchange is None or lex.order_id is None:
            return
        try:
            if timing:
                timing.mark(f"reconcile.{lex.leg.venue}.cancel_order")
            await exchange.cancel_order(lex.order_id, lex.leg.instrument.venue_symbol)
            if timing:
                leg_t = timing.ensure_leg("reconcile", lex.leg.venue)
                leg_t["cancel_order_ms"] = timing.pop(f"reconcile.{lex.leg.venue}.cancel_order")
            lex.status = "CANCELLED"
            await self._store.update_leg(lex.leg_id, status="CANCELLED")
        except Exception:
            if timing:
                label = f"reconcile.{lex.leg.venue}.cancel_order"
                if timing.has_mark(label):
                    timing.pop(label)
            pass  # cancel is best-effort
