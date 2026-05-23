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

    async def reconcile(self, result: ExecutionResult) -> ReconciliationResult:
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
                compensation_tasks.append(self._compensate(rec, lex))
            elif lex.status in ("SENT", "PENDING_SEND") and lex.order_id:
                # Try to cancel unfilled pending orders
                await self._cancel_pending(lex)

        # Run compensations concurrently
        if compensation_tasks:
            await asyncio.gather(*compensation_tasks, return_exceptions=True)

        # Determine final status
        all_compensated = all(
            rec.compensation_status == "COMPENSATED" for rec in leg_recons
        )
        all_failed = all(
            rec.compensation_status == "COMPENSATION_FAILED" for rec in leg_recons
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

    async def _compensate(self, rec: LegReconciliation, lex) -> None:
        """Send a reverse market order for the filled amount."""
        exchange = self._exchanges.get(lex.leg.venue)
        if exchange is None:
            rec.compensation_status = "COMPENSATION_FAILED"
            return

        inst = lex.leg.instrument
        # Reverse qty must equal filled amount (in base units).
        # For spot: filled_amount is already in base units via the execution,
        # but it's the full amount we originally sent. Use exact filled amount.
        # In our fake exchange, filled == planned qty.
        reverse_qty = rec.filled_amount

        try:
            # Update store to COMPENSATING
            await self._store.update_leg(rec.leg_id, status="COMPENSATING")

            order = await exchange.create_order(
                symbol=inst.venue_symbol,
                order_type="market",
                side=rec.reverse_side,
                amount=reverse_qty,
            )
            rec.compensation_order_id = order["id"]
            rec.compensation_status = "COMPENSATED"
            await self._store.update_leg(
                rec.leg_id,
                status="COMPENSATED",
                compensation_order_id=order["id"],
                compensation_filled_amount=reverse_qty,
            )
        except Exception:
            rec.compensation_status = "COMPENSATION_FAILED"
            await self._store.update_leg(
                rec.leg_id,
                status="COMPENSATION_FAILED",
            )

    async def _cancel_pending(self, lex) -> None:
        """Try to cancel a still-pending order before it fills."""
        exchange = self._exchanges.get(lex.leg.venue)
        if exchange is None or lex.order_id is None:
            return
        try:
            await exchange.cancel_order(lex.order_id, lex.leg.instrument.venue_symbol)
            lex.status = "CANCELLED"
            await self._store.update_leg(lex.leg_id, status="CANCELLED")
        except Exception:
            pass  # cancel is best-effort
