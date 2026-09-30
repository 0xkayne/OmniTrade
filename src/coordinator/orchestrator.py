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
from .leg_context import deserialize_leg_context, validate_leg_position
from .leg_orders import LegOrderManager
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

        self._planner = Planner(registry, quote_fetcher, exchanges)
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

    async def _submit(
        self, intent: Intent, dry_run: bool, timing: TimingCollector | None, *, is_roundtrip: bool = False
    ) -> dict:
        """Run the full pipeline for an Intent.

        Returns a dict with keys: status, intent_id, plan (if dry_run), legs, summary, timing.
        """
        if timing is None:
            timing = TimingCollector()

        existing = await self._store.get_intent(intent.intent_id)
        if existing is not None:
            from dataclasses import asdict

            submitted = asdict(intent)
            previous = asdict(type(intent)(**json.loads(existing.raw_intent_json)))
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
                            "planned_qty_native": str(leg.native_qty),
                            "quantity_unit": leg.instrument.quantity_unit,
                            "settlement_asset": leg.instrument.settlement_asset.symbol
                            if leg.instrument.settlement_asset
                            else None,
                            "contract_type": "inverse"
                            if leg.instrument.is_inverse
                            else "linear"
                            if leg.instrument.market_type == "perp"
                            else None,
                            "position_effect": leg.position_effect,
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
        exec_result = await self._executor.execute(plan, timing=timing, finalize=not is_roundtrip)
        timing.execute_ms = timing.pop("execute")

        if exec_result.status == "REJECTED":
            return {
                "status": "REJECTED",
                "intent_id": intent.intent_id,
                "legs": [],
                "reason": "Execution preflight rejected; see execution_rejected audit event",
                "timing": timing.to_dict(),
            }

        if exec_result.status == "ALL_FILLED" and not is_roundtrip:
            self._metrics.increment("intent.all_filled", tags={"product": intent.product})
            return {
                "status": "ALL_FILLED",
                "intent_id": intent.intent_id,
                "legs": [self._serialize_leg(lex) for lex in exec_result.legs],
                "execution_time_s": round(exec_result.completed_at - exec_result.started_at, 3),
                "timing": timing.to_dict(),
            }

        # 7. PARTIAL_FILLED — reconcile
        if intent.position_effect != "close":
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
            "planned_qty_native": str(leg.native_qty),
            "quantity_unit": leg.instrument.quantity_unit,
            "contract_type": "inverse"
            if leg.instrument.is_inverse
            else "linear"
            if leg.instrument.market_type == "perp"
            else None,
            "settlement_asset": leg.instrument.settlement_asset.symbol if leg.instrument.settlement_asset else None,
            "position_effect": leg.position_effect,
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
            "filled_qty_native": str(lex.filled_qty_native) if lex.has_known_native_quantity else None,
            "remaining_requested_qty_native": str(max(0, leg.native_qty - lex.filled_qty_native))
            if lex.has_known_native_quantity
            else None,
            "position_before_qty_native": str(leg.position_before_qty_native)
            if leg.position_before_qty_native is not None
            else None,
            "position_after_qty_native": str(lex.position_after_qty_native)
            if lex.position_after_qty_native is not None
            else None,
            "avg_price": lex.avg_price,
            "fee": lex.fee if lex.has_complete_fees else None,
            "has_complete_fees": lex.has_complete_fees,
            "error": lex.error,
        }

    async def refresh_instruments(self) -> None:
        """Force re-fetch all instruments from exchanges and overwrite cache."""
        await self._registry.refresh(self._exchanges)

    async def _restore_execution(self, row):
        from .executor import LegExecution

        if not row.execution_context_json:
            raise ValueError(f"{row.leg_id}: legacy leg lacks execution context")
        planned = deserialize_leg_context(row.execution_context_json)
        exchange = self._exchanges.get(planned.venue)
        if exchange is None or exchange.network_type != planned.instrument.network:
            raise ValueError(f"{row.leg_id}: persisted network or venue differs from configured adapter")
        markets = await exchange.list_markets()
        current = next((inst for inst in markets if inst.instrument_key == planned.instrument.instrument_key), None)
        version = json.loads(row.execution_context_json).get("schema_version", 1)
        if current is None and (markets or (version == 1 and planned.instrument.market_type == "perp")):
            raise ValueError(f"{row.leg_id}: persisted instrument is no longer available")
        if current is not None:
            if version == 1:
                if current.is_inverse or current.contract_size != 1:
                    raise ValueError(f"{row.leg_id}: legacy contract quantity cannot be established")
                planned.instrument = current
            elif any(
                getattr(current, name) != getattr(planned.instrument, name)
                for name in (
                    "contract_size",
                    "is_inverse",
                    "settlement_asset",
                    "quantity_unit",
                    "qty_step",
                    "price_step",
                )
            ):
                raise ValueError(f"{row.leg_id}: persisted contract specification differs from current market")
        if not exchange.order_capabilities(planned.instrument).has_client_order_id:
            raise ValueError(f"{row.leg_id}: persisted contract is unsupported by this adapter")
        return LegExecution(
            planned,
            row.leg_id,
            row.status,
            planned.side,
            row.order_id,
            row.filled_amount or 0,
            row.avg_price,
            row.fee_usd or 0,
            filled_qty_native=float(row.filled_qty_native)
            if row.filled_qty_native is not None
            else (
                row.filled_amount or 0
                if not planned.instrument.is_inverse and planned.instrument.contract_size == 1
                else 0
            ),
        )

    async def recover(self, intent_id: str) -> dict:
        """Settle interrupted orders; never retry or unblock a terminal intent."""
        from .executor import ExecutionResult
        from .intent import Intent
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
            try:
                executions = [await self._restore_execution(leg) for leg in legs]
            except (ValueError, TypeError, KeyError) as exc:
                await self._store.update_intent_status(intent_id, BLOCKING_STATE)
                await self._store.append_event(intent_id, "recovery_blocked", {"reason": str(exc)})
                return {"intent_id": intent_id, "status": BLOCKING_STATE, "reason": str(exc)}
            if intent.position_effect != "close":
                await self._store.update_intent_status(intent_id, "ROLLING_BACK")
            result = await self._reconciler.reconcile(
                ExecutionResult("PARTIAL_FILLED", executions, time.monotonic(), time.monotonic(), intent)
            )
            await self._store.update_intent_status(intent_id, result.status)
            return {
                "intent_id": intent_id,
                "status": result.status,
                "legs": [self._serialize_leg(lex) for lex in executions],
                "residual_exposure_usd": result.residual_exposure_usd,
            }

    async def refresh_status(self, intent_id: str) -> dict:
        """Explicit GET-only refresh; no cancel, resend, compensation, or unblock."""
        async with self._store.execution_lock():
            return await self._refresh_status(intent_id)

    async def _refresh_status(self, intent_id: str) -> dict:
        from .leg_context import get_leg_fill_qty

        row = await self._store.get_intent(intent_id)
        if row is None:
            raise ValueError(f"Intent {intent_id} does not exist")
        observations = []
        for leg_row in await self._store.get_legs_for_intent(intent_id):
            lex = await self._restore_execution(leg_row)
            exchange = self._exchanges[lex.leg.venue]
            manager = LegOrderManager(exchange, self._store, lex.leg.instrument, use_websocket=False)
            signed_total = 0.0
            has_unknown = False
            for order in await self._store.get_orders_for_leg(lex.leg_id):
                request = manager.request_from_json(order.request_json)
                if order.status == "PENDING_SEND":
                    snapshot = await manager.confirm(order, time.monotonic())
                else:
                    previous = manager.snapshot_from_json(order.snapshot_json) if order.snapshot_json else None
                    snapshot = await exchange.fetch_order_snapshot(
                        request, lex.leg.instrument, previous.order_id if previous else None
                    )
                    await manager._record(order, snapshot)
                has_unknown |= not manager._is_confirmed(snapshot)
                native = get_leg_fill_qty(snapshot, lex.leg.instrument)
                signed_total += (1 if request.side == "buy" else -1) * (native or 0)
                if order.purpose == "original":
                    lex.snapshots.append(snapshot)
            Executor._aggregate(lex)
            error = None
            if lex.leg.position_before_qty_native is not None:
                expected = lex.leg.position_before_qty_native + signed_total
                try:
                    position = await validate_leg_position(exchange, lex.leg, expected_qty_native=expected)
                    lex.position_after_qty_native = position.qty_native
                except (ValueError, TimeoutError) as exc:
                    error = str(exc)
            await self._store.update_leg(
                lex.leg_id,
                filled_qty_native=str(lex.filled_qty_native) if lex.has_known_native_quantity else None,
                filled_amount=lex.filled_amount,
                avg_price=lex.avg_price,
            )
            observation = self._serialize_leg(lex)
            observation.update(has_unknown_orders=has_unknown, position_check_error=error)
            observations.append(observation)
        return {"intent_id": intent_id, "status": row.status, "legs": observations, "orders_sent": False}

    async def acknowledge(self, intent_id: str) -> dict:
        """Manually unblock only after order and position facts can be verified."""
        async with self._store.execution_lock():
            row = await self._store.get_intent(intent_id)
            if row is None or row.status != BLOCKING_STATE:
                raise ValueError(f"{intent_id}: acknowledgement requires {BLOCKING_STATE}")
            status = await self._refresh_status(intent_id)
            if any(leg["has_unknown_orders"] or leg["position_check_error"] for leg in status["legs"]):
                raise ValueError(f"{intent_id}: orders or positions are unresolved; manual correction is required")
            await self._store.update_intent_status(intent_id, "RESOLVED_MANUAL")
            await self._store.append_event(intent_id, "manual_acknowledgement", {"facts_verified": True})
            return {**status, "status": "RESOLVED_MANUAL"}

    async def smoke_roundtrip(self, instrument, notional_cap: float) -> dict:
        """A bounded Demo open and protected compensation on the durable pipeline."""
        from math import isfinite

        from src.market.instrument import NetworkType

        from .intent import Intent

        if instrument.venue != "binance" or instrument.network is not NetworkType.TESTNET:
            raise ValueError("Binance smoke orders require the Demo network")
        if not isfinite(notional_cap) or notional_cap <= 0:
            raise ValueError("smoke notional cap must be positive and finite")
        exchange = self._exchanges[instrument.venue]
        async with self._store.execution_lock():
            if instrument.market_type == "perp":
                position = await exchange.fetch_order_position(instrument)
                if position.qty_native != 0:
                    raise ValueError("smoke requires an initially flat contract position")
            if await exchange.fetch_open_orders(instrument.venue_symbol, params=exchange.account_params(instrument)):
                raise ValueError("smoke requires no pre-existing orders on the selected instrument")
            quote = await self._quote_fetcher.fetch(instrument, enrich_funding=False, enrich_statistics=False)
            qty = instrument.native_qty_from_notional(notional_cap, quote.mid_price * 1.005)
            if qty < instrument.min_qty or instrument.quote_notional(qty, quote.bid_price) < instrument.min_notional:
                return {"status": "SKIPPED", "orders_sent": False, "reason": "venue minimum exceeds notional cap"}
            intent = Intent(
                intent_id="",
                base=instrument.base.symbol,
                quote_preference=[instrument.quote.symbol],
                product=instrument.market_type,
                side="buy",
                order_type="market",
                total_notional_usd=notional_cap,
                split={instrument.venue: 1.0},
                quantity_native=qty,
                contract_type=("inverse" if instrument.is_inverse else "linear")
                if instrument.market_type == "perp"
                else None,
                settlement_asset=instrument.settlement_asset.symbol if instrument.settlement_asset else None,
            )
            result = await self._submit(intent, False, None, is_roundtrip=True)
            did_fill = any(float(leg.get("filled_qty_native") or 0) > 0 for leg in result.get("legs", []))
            return {
                **result,
                "status": "CLOSED" if result["status"] == "ROLLED_BACK" and did_fill else result["status"],
                "orders_sent": bool(result.get("legs")),
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
