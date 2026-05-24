"""
Orchestrator: wires Planner -> Validator -> Executor -> Reconciler into one pipeline.

Usage:
    orch = Orchestrator(registry, quote_fetcher, exchanges, store)
    result = await orch.submit(intent)
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from .executor import Executor
from .planner import Planner
from .reconciler import Reconciler
from .validator import Validator

if TYPE_CHECKING:
    from src.core.base_exchange import BaseExchange
    from src.market.quote_fetcher import QuoteFetcher
    from src.market.registry import InstrumentRegistry

    from .intent import Intent


class Orchestrator:
    """Orchestrates the full oneFill pipeline: Plan -> Validate -> Execute -> Reconcile.

    All four phases run sequentially. Planner and Validator have no side effects;
    Executor and Reconciler modify state via the PersistenceStore.
    """

    def __init__(
        self,
        registry: InstrumentRegistry,
        quote_fetcher: QuoteFetcher,
        exchanges: dict[str, BaseExchange],
        store: Any,  # PersistenceStore
    ):
        self._registry = registry
        self._quote_fetcher = quote_fetcher
        self._exchanges = exchanges
        self._store = store

        self._planner = Planner(registry, quote_fetcher)
        self._validator = Validator(exchanges)
        self._executor = Executor(exchanges, store)
        self._reconciler = Reconciler(exchanges, store)

    async def submit(self, intent: Intent, dry_run: bool = False) -> dict:
        """Run the full pipeline for an Intent.

        Returns a dict with keys: status, intent_id, plan (if dry_run), legs, summary.
        """
        # 1. Block if NEEDS_MANUAL
        if await self._store.is_blocked_by_needs_manual():
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "reason": "System is blocked by NEEDS_MANUAL (ROLLED_BACK_FAILED). Manual intervention required.",
                "legs": [],
            }

        # Set created_at if not already set
        if not intent.created_at:
            intent.created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 2. Persist intent as PENDING
        await self._store.create_intent(intent, status="PENDING")

        # 3. Plan
        plan = await self._planner.plan(intent)

        # 4. Dry run? Return plan info without executing
        if dry_run:
            return {
                "status": "DRY_RUN",
                "intent_id": intent.intent_id,
                "plan": {
                    "legs": [
                        {
                            "venue": leg.venue,
                            "instrument": leg.instrument.venue_symbol,
                            "quote_matched": leg.quote_matched,
                            "planned_notional_usd": leg.planned_notional_usd,
                            "planned_qty_base": leg.planned_qty_base,
                            "estimated_avg_price": leg.estimated_fill.avg_price,
                            "estimated_slippage_pct": leg.estimated_fill.slippage_pct,
                            "estimated_fee_usd": leg.estimated_fee_usd,
                        }
                        for leg in plan.legs
                    ],
                    "rejected_venues": [
                        {"venue": v, "reason": r} for v, r in plan.rejected_venues
                    ],
                    "aggregate": {
                        "estimated_avg_price": plan.aggregate_estimated_avg_price,
                        "estimated_fee_usd": plan.aggregate_estimated_fee_usd,
                    },
                    "is_acceptable": plan.is_acceptable,
                },
                "legs": [],
            }

        if not plan.is_acceptable:
            await self._store.update_intent_status(intent.intent_id, "REJECTED")
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "reason": f"Plan not acceptable: {'; '.join(plan.rejection_reasons)}",
                "rejected_venues": [
                    {"venue": v, "reason": r} for v, r in plan.rejected_venues
                ],
                "legs": [],
            }

        # 5. Validate
        validation = await self._validator.validate(plan)
        if not validation.is_valid:
            await self._store.update_intent_status(intent.intent_id, "REJECTED")
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "reason": "Validation failed",
                "validation_failures": [
                    {"venue": v, "reason": r} for v, r in validation.failures
                ],
                "legs": [],
            }

        # Validation passed — transition to VALIDATED
        await self._store.update_intent_status(intent.intent_id, "VALIDATED")

        # 6. Execute
        exec_result = await self._executor.execute(plan)

        if exec_result.status == "ALL_FILLED":
            return {
                "status": "ALL_FILLED",
                "intent_id": intent.intent_id,
                "legs": [self._serialize_leg(lex) for lex in exec_result.legs],
                "execution_time_s": round(exec_result.completed_at - exec_result.started_at, 3),
            }

        # 7. PARTIAL_FILLED — reconcile
        await self._store.update_intent_status(intent.intent_id, "ROLLING_BACK")
        rec_result = await self._reconciler.reconcile(exec_result)

        final_status = rec_result.status  # ROLLED_BACK or ROLLED_BACK_FAILED
        await self._store.update_intent_status(intent.intent_id, final_status)

        return {
            "status": final_status,
            "intent_id": intent.intent_id,
            "legs": [self._serialize_leg(lex) for lex in exec_result.legs],
            "reconciliation": {
                "status": rec_result.status,
                "legs": [
                    {
                        "leg_id": rec.leg_id,
                        "reverse_side": rec.reverse_side,
                        "compensation_status": rec.compensation_status,
                        "compensation_order_id": rec.compensation_order_id,
                    }
                    for rec in rec_result.legs
                ],
                "residual_exposure_usd": rec_result.residual_exposure_usd,
            },
        }

    @staticmethod
    def _serialize_leg(lex) -> dict:
        leg = lex.leg
        return {
            "leg_id": lex.leg_id,
            "venue": leg.venue,
            "instrument_venue_symbol": leg.instrument.venue_symbol,
            "status": lex.status,
            "order_id": lex.order_id,
            "planned_notional_usd": leg.planned_notional_usd,
            "planned_qty_base": leg.planned_qty_base,
            "estimated_avg_price": leg.estimated_fill.avg_price,
            "estimated_slippage_pct": leg.estimated_fill.slippage_pct,
            "estimated_fee_usd": leg.estimated_fee_usd,
            "filled_amount": lex.filled_amount,
            "avg_price": lex.avg_price,
            "fee": lex.fee,
            "error": lex.error,
        }
