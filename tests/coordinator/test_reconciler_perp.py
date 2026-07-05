"""Tests for Reconciler perp-specific behaviour: reduce_only reverse orders."""

import pytest

from src.coordinator.executor import ExecutionResult, LegExecution
from src.coordinator.plan import PlannedLeg
from src.coordinator.reconciler import Reconciler
from tests.coordinator.conftest import (
    make_btc_usdt_perp,
    make_btc_usdt_spot,
    make_intent,
    make_quote,
)


async def _create_leg_in_store(store, lex, intent_id, **overrides):
    await store.create_leg(
        leg_id=lex.leg_id,
        intent_id=intent_id,
        venue=lex.leg.venue,
        instrument_venue_symbol=lex.leg.instrument.venue_symbol,
        instrument_base=lex.leg.instrument.base.symbol,
        instrument_quote=lex.leg.instrument.quote.symbol,
        instrument_market_type=lex.leg.instrument.market_type,
        quote_preference_matched=lex.leg.quote_matched,
        planned_notional_usd=lex.leg.planned_notional_usd,
        planned_qty_base=lex.leg.planned_qty_base,
        **overrides,
    )


def _make_leg_exec(
    inst, venue="binance", status="FILLED", filled_amount=0.01, side="buy", order_id="perp-test-123", suffix=""
):
    q = make_quote(inst, mid=50000.0)
    fill = q.estimate_fill(0.01, "buy")
    leg = PlannedLeg(
        venue=venue,
        instrument=inst,
        quote_matched="USDT",
        planned_notional_usd=500.0,
        planned_qty_base=0.01,
        estimated_fill=fill,
        estimated_fee_usd=0.25,
        leverage=3,
    )
    return LegExecution(
        leg=leg,
        leg_id=f"leg-{venue}-perp{suffix}",
        status=status,
        side=side,
        order_id=order_id,
        filled_amount=filled_amount,
        avg_price=50000.0,
    )


class TestPerpReconciler:
    """Reconciler adds reduceOnly=True for perp reverse orders (Stage 4)."""

    @pytest.mark.asyncio
    async def test_perp_reverse_order_has_reduce_only(self, fake_exchanges, fake_store):
        inst = make_btc_usdt_perp("binance")
        lex = _make_leg_exec(inst, status="FILLED", filled_amount=0.01)
        intent = make_intent(product="perp")
        await fake_store.create_intent(intent, status="EXECUTING")
        await _create_leg_in_store(fake_store, lex, intent.intent_id, leverage=3)

        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex],
            started_at=0.0,
            completed_at=0.0,
        )
        reconciler = Reconciler(fake_exchanges, fake_store)
        await reconciler.reconcile(result)

        # The create_order call should include reduceOnly=True in params
        binance: object = fake_exchanges["binance"]
        assert len(binance.create_order_calls) >= 1
        call = binance.create_order_calls[-1]
        assert call["params"].get("reduceOnly") is True

    @pytest.mark.asyncio
    async def test_spot_reverse_order_no_reduce_only(self, fake_exchanges, fake_store):
        inst = make_btc_usdt_spot("binance")
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.01, "buy")
        leg = PlannedLeg(
            venue="binance",
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=500.0,
            planned_qty_base=0.01,
            estimated_fill=fill,
            estimated_fee_usd=0.25,
        )
        lex = LegExecution(
            leg=leg,
            leg_id="leg-spot",
            status="FILLED",
            side="buy",
            order_id="spot-123",
            filled_amount=0.01,
            avg_price=50000.0,
        )
        intent = make_intent(product="spot")
        await fake_store.create_intent(intent, status="EXECUTING")
        await _create_leg_in_store(fake_store, lex, intent.intent_id)

        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex],
            started_at=0.0,
            completed_at=0.0,
        )
        reconciler = Reconciler(fake_exchanges, fake_store)
        await reconciler.reconcile(result)

        # Spot reverse order must NOT have reduceOnly
        binance: object = fake_exchanges["binance"]
        assert len(binance.create_order_calls) >= 1
        call = binance.create_order_calls[-1]
        assert call["params"].get("reduceOnly") is None

    @pytest.mark.asyncio
    async def test_perp_compensate_failure_marks_needs_manual(self, fake_exchanges, fake_store):
        """When perp compensation fails, status should be ROLLED_BACK_FAILED."""
        inst = make_btc_usdt_perp("binance")
        lex = _make_leg_exec(inst, status="FILLED", filled_amount=0.01)
        intent = make_intent(product="perp")
        await fake_store.create_intent(intent, status="EXECUTING")
        await _create_leg_in_store(fake_store, lex, intent.intent_id, leverage=3)

        # Make compensation order fail
        binance = fake_exchanges["binance"]
        binance.set_fail_create(True)

        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex],
            started_at=0.0,
            completed_at=0.0,
        )
        reconciler = Reconciler(fake_exchanges, fake_store)
        rec_result = await reconciler.reconcile(result)

        assert rec_result.status == "ROLLED_BACK_FAILED"
        assert len(rec_result.legs) == 1
        assert rec_result.legs[0].compensation_status == "COMPENSATION_FAILED"

    @pytest.mark.asyncio
    async def test_perp_cancel_pending_on_partial(self, fake_exchanges, fake_store):
        """When perp leg is 'SENT', cancels it; reverse is for filled leg only."""
        inst = make_btc_usdt_perp("binance")
        filled_lex = _make_leg_exec(inst, status="FILLED", filled_amount=0.01, order_id="filled-1", suffix="-filled")
        sent_lex = _make_leg_exec(inst, status="SENT", filled_amount=0.0, order_id="sent-1", suffix="-sent")
        intent = make_intent(product="perp")
        await fake_store.create_intent(intent, status="EXECUTING")
        for lex in [filled_lex, sent_lex]:
            await _create_leg_in_store(fake_store, lex, intent.intent_id, leverage=3)

        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[filled_lex, sent_lex],
            started_at=0.0,
            completed_at=0.0,
        )
        reconciler = Reconciler(fake_exchanges, fake_store)
        rec_result = await reconciler.reconcile(result)

        # Only the filled leg should have resulted in a compensation
        assert rec_result.status == "ROLLED_BACK"
        assert len(rec_result.legs) == 1  # only the filled leg
        assert rec_result.legs[0].original_order_id == "filled-1"
