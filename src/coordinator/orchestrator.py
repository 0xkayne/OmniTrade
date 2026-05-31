"""
Orchestrator: wires Planner -> Validator -> Executor -> Reconciler into one pipeline.

Usage:
    orch = Orchestrator(registry, quote_fetcher, exchanges, store)
    result = await orch.submit(intent)
"""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING, Any

from .executor import Executor
from .planner import Planner
from .reconciler import Reconciler
from .timing import TimingCollector
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
        poll_interval_ms: int = 500,
    ):
        self._registry = registry
        self._quote_fetcher = quote_fetcher
        self._exchanges = exchanges
        self._store = store

        self._planner = Planner(registry, quote_fetcher)
        self._validator = Validator(exchanges)
        self._executor = Executor(exchanges, store, poll_interval_ms=poll_interval_ms)
        self._reconciler = Reconciler(exchanges, store)

    async def submit(self, intent: Intent, dry_run: bool = False, timing: TimingCollector | None = None) -> dict:
        """Run the full pipeline for an Intent.

        Returns a dict with keys: status, intent_id, plan (if dry_run), legs, summary, timing.
        """
        if timing is None:
            timing = TimingCollector()

        # 1. Block if NEEDS_MANUAL
        if await self._store.is_blocked_by_needs_manual():
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "reason": "System is blocked by NEEDS_MANUAL (ROLLED_BACK_FAILED). Manual intervention required.",
                "legs": [],
                "timing": timing.to_dict(),
            }

        # Set created_at if not already set
        if not intent.created_at:
            intent.created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 2. Persist intent as PENDING
        await self._store.create_intent(intent, status="PENDING")

        # 3. Plan + pre-fetch balances concurrently.
        # Plan's quote_fetch and Validate's balance_fetch are independent I/O.
        # Skip balance prefetch on dry-run — balances are never consumed.
        if dry_run:
            timing.mark("plan")
            plan = await self._planner.plan(intent, timing=timing)
            timing.plan_ms = timing.pop("plan")
            balances = None
        else:
            venues = list(intent.split.keys())
            plan_task = self._planner.plan(intent, timing=timing)
            balance_task = self._validator.fetch_balances(venues)
            timing.mark("plan")
            plan, balances = await asyncio.gather(plan_task, balance_task)
            timing.plan_ms = timing.pop("plan")

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
                            "market_type": leg.instrument.market_type,
                            "side": leg.side,
                            "leverage": leg.leverage,
                            "quote_matched": leg.quote_matched,
                            "planned_notional_usd": leg.planned_notional_usd,
                            "planned_qty_base": leg.planned_qty_base,
                            "estimated_avg_price": leg.estimated_fill.avg_price,
                            "estimated_slippage_pct": leg.estimated_fill.slippage_pct,
                            "estimated_fee_usd": leg.estimated_fee_usd,
                        }
                        for leg in plan.legs
                    ],
                    "rejected_venues": [{"venue": v, "reason": r} for v, r in plan.rejected_venues],
                    "aggregate": {
                        "estimated_avg_price": plan.aggregate_estimated_avg_price,
                        "estimated_fee_usd": plan.aggregate_estimated_fee_usd,
                    },
                    "is_acceptable": plan.is_acceptable,
                },
                "legs": [],
                "timing": timing.to_dict(),
            }

        if not plan.is_acceptable:
            await self._store.update_intent_status(intent.intent_id, "REJECTED")
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "reason": f"Plan not acceptable: {'; '.join(plan.rejection_reasons)}",
                "rejected_venues": [{"venue": v, "reason": r} for v, r in plan.rejected_venues],
                "legs": [],
                "timing": timing.to_dict(),
            }

        # 5. Validate
        timing.mark("validate")
        validation = await self._validator.validate(plan, timing=timing, prefetched_balances=balances)
        timing.validate_ms = timing.pop("validate")
        if not validation.is_valid:
            await self._store.update_intent_status(intent.intent_id, "REJECTED")
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "reason": "Validation failed",
                "validation_failures": [{"venue": v, "reason": r} for v, r in validation.failures],
                "legs": [],
                "timing": timing.to_dict(),
            }

        # Validation passed — transition to VALIDATED
        await self._store.update_intent_status(intent.intent_id, "VALIDATED")

        # 6. Execute
        timing.mark("execute")
        exec_result = await self._executor.execute(plan, timing=timing)
        timing.execute_ms = timing.pop("execute")

        if exec_result.status == "ALL_FILLED":
            return {
                "status": "ALL_FILLED",
                "intent_id": intent.intent_id,
                "legs": [self._serialize_leg(lex) for lex in exec_result.legs],
                "execution_time_s": round(exec_result.completed_at - exec_result.started_at, 3),
                "timing": timing.to_dict(),
            }

        # 7. PARTIAL_FILLED — reconcile
        await self._store.update_intent_status(intent.intent_id, "ROLLING_BACK")
        timing.mark("reconcile")
        rec_result = await self._reconciler.reconcile(exec_result, timing=timing)
        timing.reconcile_ms = timing.pop("reconcile")

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
            "timing": timing.to_dict(),
        }

    @staticmethod
    def _serialize_leg(lex) -> dict:
        leg = lex.leg
        return {
            "leg_id": lex.leg_id,
            "venue": leg.venue,
            "instrument_venue_symbol": leg.instrument.venue_symbol,
            "market_type": leg.instrument.market_type,
            "side": leg.side,
            "leverage": leg.leverage,
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

    async def refresh_instruments(self) -> None:
        """Force re-fetch all instruments from exchanges and overwrite cache."""
        await self._registry.refresh(self._exchanges)

    async def close(self) -> None:
        """Close exchange connections and persistence store."""
        for exc in self._exchanges.values():
            try:
                await exc.close()
            except Exception:
                pass
        if isinstance(self._store, object) and hasattr(self._store, "close"):
            await self._store.close()
