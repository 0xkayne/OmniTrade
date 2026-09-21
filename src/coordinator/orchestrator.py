"""
Orchestrator: wires Planner -> Validator -> Executor -> Reconciler into one pipeline.

Usage:
    orch = Orchestrator(registry, quote_fetcher, exchanges, store)
    result = await orch.submit(intent)
"""

from __future__ import annotations

import contextlib
import datetime
import json
import time
from typing import TYPE_CHECKING, Any

from .executor import Executor
from .planner import Planner
from .reconciler import Reconciler
from .state_machine import BLOCKING_STATE
from .timing import TimingCollector
from .validator import Validator

if TYPE_CHECKING:
    from src.exchange.base import BaseExchange
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
        risk_validator: Any = None,  # RiskValidator
        use_websocket: bool = True,
        metrics: Any = None,  # MetricsEmitter (defaults to no-op)
    ):
        from src.observability.metrics import NoopMetrics

        self._registry = registry
        self._quote_fetcher = quote_fetcher
        self._exchanges = exchanges
        self._store = store
        self._risk_validator = risk_validator
        self._metrics = metrics or NoopMetrics()

        self._planner = Planner(registry, quote_fetcher)
        self._validator = Validator(exchanges)
        self._executor = Executor(
            exchanges,
            store,
            poll_interval_ms=poll_interval_ms,
            use_websocket=use_websocket,
            quote_fetcher=quote_fetcher,
            metrics=self._metrics,
        )
        self._reconciler = Reconciler(exchanges, store, quote_fetcher=quote_fetcher, metrics=self._metrics)

    async def submit(self, intent: Intent, dry_run: bool = False, timing: TimingCollector | None = None) -> dict:
        async with self._store.execution_lock():
            return await self._submit(intent, dry_run, timing)

    async def _submit(self, intent: Intent, dry_run: bool, timing: TimingCollector | None) -> dict:
        """Run the full pipeline for an Intent.

        Returns a dict with keys: status, intent_id, plan (if dry_run), legs, summary, timing.
        """
        if timing is None:
            timing = TimingCollector()

        existing = await self._store.get_intent(intent.intent_id)
        if existing is not None:
            from dataclasses import asdict

            submitted = asdict(intent)
            previous = json.loads(existing.raw_intent_json)
            submitted.pop("created_at", None)
            previous.pop("created_at", None)
            if submitted != previous:
                raise ValueError(f"Intent {intent.intent_id} already exists with different parameters")
            return {
                "intent_id": intent.intent_id,
                "status": existing.status,
                "is_duplicate": True,
                "legs": [asdict(row) for row in await self._store.get_legs_for_intent(intent.intent_id)],
            }

        # 1. Block if NEEDS_MANUAL
        if await self._store.count_intents_with_status(BLOCKING_STATE) > 0:
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "reason": "System is blocked by NEEDS_MANUAL (ROLLED_BACK_FAILED). Manual intervention required.",
                "legs": [],
                "timing": timing.to_dict(),
            }

        for status in ("PENDING", "VALIDATED", "EXECUTING", "PARTIAL_FILLED", "ROLLING_BACK", "EXECUTE_TIMEOUT"):
            if await self._store.count_intents_with_status(status):
                return {
                    "status": "REJECTED",
                    "intent_id": intent.intent_id,
                    "legs": [],
                    "reason": f"Unfinished {status} intent requires recovery before new orders",
                }

        # Set created_at if not already set
        if not intent.created_at:
            intent.created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 2. Persist intent as PENDING
        await self._store.create_intent(intent, status="PENDING")
        self._metrics.increment("intent.submitted", tags={"product": intent.product, "side": intent.side})

        # 3. Plan first. Balance validation depends on the resolved leg
        # market_type, because spot and perp/swap can live in different accounts.
        timing.mark("plan")
        plan = await self._planner.plan(intent, timing=timing)
        timing.plan_ms = timing.pop("plan")

        # 4. Dry run? Return plan info without executing
        if dry_run:
            validation = await self._validator.validate(plan, timing=timing) if plan.is_acceptable else None
            risk = (
                await self._risk_validator.check(intent, plan) if plan.is_acceptable and self._risk_validator else None
            )
            await self._store.update_intent_status(intent.intent_id, "DRY_RUN")
            await self._store.append_event(intent.intent_id, "dry_run_completed", {"orders_sent": False})
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
                "validation_failures": validation.failures if validation else [],
                "risk_failures": risk.failures if risk else [],
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
        validation = await self._validator.validate(plan, timing=timing)
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

        # 5.5 Risk check (after Validate, before Execute)
        if self._risk_validator is not None:
            risk_result = await self._risk_validator.check(intent, plan)
            if not risk_result.is_allowed:
                await self._store.update_intent_status(intent.intent_id, "REJECTED")
                return {
                    "status": "REJECTED",
                    "intent_id": intent.intent_id,
                    "reason": "Risk check failed",
                    "risk_failures": risk_result.failures,
                    "legs": [],
                    "timing": timing.to_dict(),
                }

        # 6. Execute
        timing.mark("execute")
        exec_result = await self._executor.execute(plan, timing=timing)
        timing.execute_ms = timing.pop("execute")

        if exec_result.status == "REJECTED":
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "legs": [],
                "reason": "Execution preflight rejected; see execution_rejected audit event",
                "timing": timing.to_dict(),
            }

        if exec_result.status == "ALL_FILLED":
            self._metrics.increment("intent.all_filled", tags={"product": intent.product})
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
        self._metrics.increment("intent.reconciled", tags={"outcome": final_status, "product": intent.product})

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
            "reference_price": leg.reference_price,
            "estimated_spread_pct": leg.estimated_spread_pct,
            "actual_slippage_pct": (
                (lex.avg_price / (leg.reference_price or leg.estimated_fill.avg_price) - 1)
                * (100 if lex.side == "buy" else -100)
            )
            if lex.avg_price
            else None,
            "order_ids": [snapshot.order_id for snapshot in lex.snapshots],
            "filled_amount": lex.filled_amount,
            "avg_price": lex.avg_price,
            "fee": lex.fee if lex.has_complete_fees else None,
            "has_complete_fees": lex.has_complete_fees,
            "error": lex.error,
        }

    async def refresh_instruments(self) -> None:
        """Force re-fetch all instruments from exchanges and overwrite cache."""
        await self._registry.refresh(self._exchanges)

    async def recover(self, intent_id: str) -> dict:
        """Settle and flatten an interrupted intent, never retry a blocking terminal intent."""
        from src.market.asset import Asset
        from src.market.instrument import Instrument, NetworkType
        from src.market.quote import EstimatedFill

        from .executor import ExecutionResult, LegExecution
        from .intent import Intent
        from .plan import PlannedLeg
        from .state_machine import TERMINAL_STATES

        async with self._store.execution_lock():
            row = await self._store.get_intent(intent_id)
            if row is None:
                raise ValueError(f"Intent {intent_id} does not exist")
            if row.status in TERMINAL_STATES:
                return {"intent_id": intent_id, "status": row.status, "reason": "Terminal intents are not retried"}
            intent = Intent(**json.loads(row.raw_intent_json))
            legs = await self._store.get_legs_for_intent(intent_id)
            if not legs:
                await self._store.update_intent_status(intent_id, "REJECTED")
                return {"intent_id": intent_id, "status": "REJECTED", "reason": "No orders were persisted"}
            executions = []
            for leg in legs:
                if not leg.execution_context_json:
                    await self._store.update_intent_status(intent_id, BLOCKING_STATE)
                    return {
                        "intent_id": intent_id,
                        "status": BLOCKING_STATE,
                        "reason": "Legacy leg lacks execution context",
                    }
                context = json.loads(leg.execution_context_json)
                instrument = context["instrument"]
                instrument["base"] = Asset(**instrument["base"])
                instrument["quote"] = Asset(**instrument["quote"])
                instrument["network"] = NetworkType(instrument["network"])
                context["instrument"] = Instrument(**instrument)
                context["estimated_fill"] = EstimatedFill(**context["estimated_fill"])
                planned = PlannedLeg(**context)
                executions.append(
                    LegExecution(
                        planned,
                        leg.leg_id,
                        leg.status,
                        planned.side,
                        leg.order_id,
                        leg.filled_amount or 0,
                        leg.avg_price,
                        leg.fee_usd or 0,
                    )
                )
            await self._store.update_intent_status(intent_id, "ROLLING_BACK")
            result = await self._reconciler.reconcile(
                ExecutionResult("PARTIAL_FILLED", executions, time.monotonic(), time.monotonic(), intent)
            )
            await self._store.update_intent_status(intent_id, result.status)
            return {
                "intent_id": intent_id,
                "status": result.status,
                "residual_exposure_usd": result.residual_exposure_usd,
            }

    async def close(self) -> None:
        """Close exchange connections, orderbook cache, and persistence store."""
        for exc in self._exchanges.values():
            with contextlib.suppress(Exception):
                await exc.close()
        with contextlib.suppress(Exception):
            await self._quote_fetcher.close()
        if isinstance(self._store, object) and hasattr(self._store, "close"):
            await self._store.close()
