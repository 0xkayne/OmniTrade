"""Tests for Reconciler."""

import pytest

from src.coordinator.executor import ExecutionResult, LegExecution
from src.coordinator.plan import Plan, PlannedLeg
from src.coordinator.reconciler import Reconciler
from tests.coordinator.conftest import (
    make_btc_usdt_spot,
    make_intent,
    make_quote,
)


async def _create_leg_in_store(store, lex, intent_id):
    """Create a leg row in the store from a LegExecution (needed by real PersistenceStore)."""
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
    )


def make_leg_exec(venue, status, filled_amount=0.0, order_id="test-123", leg=None, side="buy"):
    if leg is None:
        inst = make_btc_usdt_spot(venue)
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.01, "buy")
        leg = PlannedLeg(
            venue=venue,
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=500.0,
            planned_qty_base=0.01,
            estimated_fill=fill,
            estimated_fee_usd=0.45,
        )
    return LegExecution(
        leg=leg,
        leg_id=f"leg-{venue}",
        status=status,
        side=side,
        order_id=order_id,
        filled_amount=filled_amount,
        avg_price=50000.0 if filled_amount > 0 else None,
        fee=0.45,
    )


@pytest.fixture
def two_leg_fill_plan():
    intent = make_intent(total_notional_usd=1000.0, side="buy")
    inst1 = make_btc_usdt_spot("binance")
    inst2 = make_btc_usdt_spot("hyperliquid")
    q1 = make_quote(inst1, mid=50000.0)
    q2 = make_quote(inst2, mid=50100.0)
    f1 = q1.estimate_fill(0.01, "buy")
    f2 = q2.estimate_fill(0.00998, "buy")
    leg1 = PlannedLeg(
        venue="binance",
        instrument=inst1,
        quote_matched="USDT",
        planned_notional_usd=500.0,
        planned_qty_base=0.01,
        estimated_fill=f1,
        estimated_fee_usd=0.45,
    )
    leg2 = PlannedLeg(
        venue="hyperliquid",
        instrument=inst2,
        quote_matched="USDT",
        planned_notional_usd=500.0,
        planned_qty_base=0.00998,
        estimated_fill=f2,
        estimated_fee_usd=0.45,
    )
    return Plan(
        intent=intent,
        legs=[leg1, leg2],
        rejected_venues=[],
        aggregate_estimated_avg_price=50050.0,
        aggregate_estimated_fee_usd=0.90,
        is_acceptable=True,
        rejection_reasons=[],
    )


@pytest.fixture
def reconciler(fake_exchanges, fake_store):
    return Reconciler(fake_exchanges, fake_store)


@pytest.mark.asyncio
class TestReconciler:
    async def test_reverse_order_for_filled_leg(self, reconciler, fake_store, fake_binance, two_leg_fill_plan):
        """One leg filled, one rejected → reverse the filled one."""
        plan = two_leg_fill_plan
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        import time

        lex1 = make_leg_exec("binance", "FILLED", filled_amount=0.01, leg=plan.legs[0])
        lex2 = make_leg_exec("hyperliquid", "REJECTED", leg=plan.legs[1])
        await _create_leg_in_store(fake_store, lex1, plan.intent.intent_id)
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        # One leg was compensated
        assert rec_result.status == "ROLLED_BACK"
        assert len(rec_result.legs) == 1  # only the filled leg gets compensated
        assert rec_result.legs[0].reverse_side == "sell"  # original was buy
        assert rec_result.legs[0].compensation_status == "COMPENSATED"
        assert rec_result.residual_exposure_usd == 0.0
        assert fake_binance.create_order_calls[-1]["params"]["type"] == "spot"

    async def test_both_filled_both_compensated(self, reconciler, fake_store, two_leg_fill_plan):
        plan = two_leg_fill_plan
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        import time

        lex1 = make_leg_exec("binance", "FILLED", filled_amount=0.01, leg=plan.legs[0])
        lex2 = make_leg_exec("hyperliquid", "FILLED", filled_amount=0.00998, leg=plan.legs[1])
        await _create_leg_in_store(fake_store, lex1, plan.intent.intent_id)
        await _create_leg_in_store(fake_store, lex2, plan.intent.intent_id)
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        assert rec_result.status == "ROLLED_BACK"
        assert len(rec_result.legs) == 2
        assert all(r.compensation_status == "COMPENSATED" for r in rec_result.legs)

    async def test_compensation_failure_leads_to_failed(self, reconciler, fake_store, fake_binance, two_leg_fill_plan):
        """If create_order fails during compensation, status is ROLLED_BACK_FAILED."""
        plan = two_leg_fill_plan
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        fake_binance.set_fail_create(True, message="compensation failed")

        import time

        lex1 = make_leg_exec("binance", "FILLED", filled_amount=0.01, leg=plan.legs[0])
        lex2 = make_leg_exec("hyperliquid", "REJECTED", leg=plan.legs[1])
        await _create_leg_in_store(fake_store, lex1, plan.intent.intent_id)
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        assert rec_result.status == "ROLLED_BACK_FAILED"
        assert rec_result.legs[0].compensation_status == "COMPENSATION_FAILED"

    async def test_no_filled_legs_no_compensation_needed(self, reconciler, fake_store):
        """When all legs are REJECTED, no compensation needed."""
        lex1 = make_leg_exec("binance", "REJECTED")
        lex2 = make_leg_exec("hyperliquid", "REJECTED")
        import time

        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )
        rec_result = await reconciler.reconcile(result)

        assert rec_result.status == "ROLLED_BACK"
        assert len(rec_result.legs) == 0
        assert rec_result.residual_exposure_usd == 0.0

    async def test_cancel_pending_legs(self, reconciler, fake_store, two_leg_fill_plan, fake_exchanges):
        """Legs that are SENT but not filled should be cancelled."""
        plan = two_leg_fill_plan
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        import time

        lex1 = make_leg_exec("binance", "SENT", order_id="pending-1", leg=plan.legs[0])
        lex2 = make_leg_exec("hyperliquid", "FILLED", filled_amount=0.00998, leg=plan.legs[1])
        await _create_leg_in_store(fake_store, lex1, plan.intent.intent_id)
        await _create_leg_in_store(fake_store, lex2, plan.intent.intent_id)
        # Seed the pending order so cancel_order finds it
        fake_exchanges["binance"]._orders["pending-1"] = {
            "id": "pending-1",
            "status": "open",
            "symbol": "BTCUSDT",
        }
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        # hyperliquid was filled → compensated
        assert len(rec_result.legs) == 1
        # binance was pending → canceled
        assert lex1.status == "CANCELLED"
        assert fake_exchanges["binance"].cancel_order_calls[-1]["params"]["type"] == "spot"

    async def test_sell_side_reverse_is_buy(self, reconciler, fake_store, fake_binance):
        """When original side is 'sell', reverse should be 'buy'."""
        intent = make_intent(side="sell", total_notional_usd=500.0, split={"binance": 1.0})
        inst = make_btc_usdt_spot("binance")
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.01, "sell")
        leg = PlannedLeg(
            venue="binance",
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=500.0,
            planned_qty_base=0.01,
            estimated_fill=fill,
            estimated_fee_usd=0.45,
        )
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=0.45,
            is_acceptable=True,
            rejection_reasons=[],
        )
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        import time

        lex = make_leg_exec("binance", "FILLED", filled_amount=0.01, leg=leg, side="sell")
        await _create_leg_in_store(fake_store, lex, plan.intent.intent_id)
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        assert rec_result.legs[0].reverse_side == "buy"
        assert rec_result.legs[0].compensation_status == "COMPENSATED"

    async def test_residual_exposure_when_compensation_fails(self, reconciler, fake_store, fake_binance):
        """When a compensation fails, residual_exposure_usd reflects it."""
        intent = make_intent(side="buy", total_notional_usd=500.0, split={"binance": 1.0})
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
            estimated_fee_usd=0.45,
        )
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=0.45,
            is_acceptable=True,
            rejection_reasons=[],
        )
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        fake_binance.set_fail_create(True, message="reverse failed")

        import time

        lex = make_leg_exec("binance", "FILLED", filled_amount=0.01, leg=leg)
        await _create_leg_in_store(fake_store, lex, plan.intent.intent_id)
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        assert rec_result.status == "ROLLED_BACK_FAILED"
        assert rec_result.residual_exposure_usd > 0

    async def test_cancel_failure_best_effort(self, reconciler, fake_store, fake_binance):
        """When cancel_order fails, it's best-effort — no crash."""
        intent = make_intent(total_notional_usd=500.0, split={"binance": 1.0})
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
            estimated_fee_usd=0.45,
        )
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=0.45,
            is_acceptable=True,
            rejection_reasons=[],
        )
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        fake_binance.set_fail_cancel(True)

        import time

        lex = make_leg_exec("binance", "SENT", order_id="pending-1", leg=leg)
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        # Should not crash — cancel failure is silently swallowed
        assert rec_result.status == "ROLLED_BACK"
        assert len(rec_result.legs) == 0  # no filled legs to compensate

    async def test_cancel_timeout_legs(self, reconciler, fake_store, two_leg_fill_plan, fake_exchanges):
        """TIMEOUT leg with valid order_id should be cancelled."""
        plan = two_leg_fill_plan
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        import time

        lex1 = make_leg_exec("binance", "TIMEOUT", order_id="pending-1", leg=plan.legs[0])
        lex2 = make_leg_exec("hyperliquid", "REJECTED", leg=plan.legs[1])
        await _create_leg_in_store(fake_store, lex1, plan.intent.intent_id)
        # Seed the pending order so cancel_order finds it
        fake_exchanges["binance"]._orders["pending-1"] = {
            "id": "pending-1",
            "status": "open",
            "symbol": "BTCUSDT",
        }
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        # No filled legs → no compensations
        assert len(rec_result.legs) == 0
        # TIMEOUT leg was cancelled
        assert lex1.status == "CANCELLED"
        assert fake_exchanges["binance"].cancel_order_calls[-1]["params"]["type"] == "spot"

    async def test_all_timeout_all_cancelled(self, reconciler, fake_store, two_leg_fill_plan, fake_exchanges):
        """All legs TIMEOUT → all cancelled, result is ROLLED_BACK."""
        plan = two_leg_fill_plan
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        import time

        lex1 = make_leg_exec("binance", "TIMEOUT", order_id="pending-1", leg=plan.legs[0])
        lex2 = make_leg_exec("hyperliquid", "TIMEOUT", order_id="pending-2", leg=plan.legs[1])
        await _create_leg_in_store(fake_store, lex1, plan.intent.intent_id)
        await _create_leg_in_store(fake_store, lex2, plan.intent.intent_id)
        fake_exchanges["binance"]._orders["pending-1"] = {
            "id": "pending-1",
            "status": "open",
            "symbol": "BTCUSDT",
        }
        fake_exchanges["hyperliquid"]._orders["pending-2"] = {
            "id": "pending-2",
            "status": "open",
            "symbol": "BTCUSDT",
        }
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        assert rec_result.status == "ROLLED_BACK"
        # No filled legs → no compensations
        assert len(rec_result.legs) == 0
        # Both TIMEOUT legs cancelled
        assert lex1.status == "CANCELLED"
        assert lex2.status == "CANCELLED"

    async def test_filled_and_timeout_mixed(
        self, reconciler, fake_store, fake_binance, two_leg_fill_plan, fake_exchanges
    ):
        """FILLED leg compensated, TIMEOUT leg cancelled — mixed path C."""
        plan = two_leg_fill_plan
        await fake_store.create_intent(plan.intent, status="PARTIAL_FILLED")

        import time

        lex1 = make_leg_exec("binance", "TIMEOUT", order_id="pending-1", leg=plan.legs[0])
        lex2 = make_leg_exec("hyperliquid", "FILLED", filled_amount=0.00998, leg=plan.legs[1])
        await _create_leg_in_store(fake_store, lex1, plan.intent.intent_id)
        await _create_leg_in_store(fake_store, lex2, plan.intent.intent_id)
        fake_exchanges["binance"]._orders["pending-1"] = {
            "id": "pending-1",
            "status": "open",
            "symbol": "BTCUSDT",
        }
        result = ExecutionResult(
            status="PARTIAL_FILLED",
            legs=[lex1, lex2],
            started_at=time.time(),
            completed_at=time.time(),
        )

        rec_result = await reconciler.reconcile(result)

        assert rec_result.status == "ROLLED_BACK"
        # hyperliquid was filled → compensated
        assert rec_result.legs[0].reverse_side == "sell"
        assert rec_result.legs[0].compensation_status == "COMPENSATED"
        # binance was TIMEOUT → cancelled
        assert lex1.status == "CANCELLED"
        assert fake_exchanges["binance"].cancel_order_calls[-1]["params"]["type"] == "spot"
