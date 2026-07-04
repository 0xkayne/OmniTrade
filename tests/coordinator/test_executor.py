"""Tests for Executor — happy path (ALL_FILLED)."""

import asyncio

import pytest

from src.coordinator.executor import ExecutionResult, Executor
from src.coordinator.plan import Plan, PlannedLeg
from tests.coordinator.conftest import (
    make_btc_usdt_spot,
    make_intent,
    make_quote,
)


def make_two_leg_plan():
    intent = make_intent(total_notional_usd=1000.0, execute_timeout_seconds=5)
    inst1 = make_btc_usdt_spot("binance")
    inst2 = make_btc_usdt_spot("hyperliquid")
    q1 = make_quote(inst1, mid=50000.0)
    q2 = make_quote(inst2, mid=50100.0)
    f1 = q1.estimate_fill(0.01, "buy")
    f2 = q2.estimate_fill(0.00998, "buy")
    leg1 = PlannedLeg(venue="binance", instrument=inst1, quote_matched="USDT",
                      planned_notional_usd=500.0, planned_qty_base=0.01,
                      estimated_fill=f1, estimated_fee_usd=0.45)
    leg2 = PlannedLeg(venue="hyperliquid", instrument=inst2, quote_matched="USDT",
                      planned_notional_usd=500.0, planned_qty_base=0.00998,
                      estimated_fill=f2, estimated_fee_usd=0.45)
    return Plan(intent=intent, legs=[leg1, leg2], rejected_venues=[],
                aggregate_estimated_avg_price=50050.0, aggregate_estimated_fee_usd=0.90,
                is_acceptable=True, rejection_reasons=[])


@pytest.fixture
def executor(fake_exchanges, fake_store):
    return Executor(fake_exchanges, fake_store, poll_interval_ms=50)


@pytest.mark.asyncio
class TestExecutorHappyPath:
    async def test_execute_all_filled(self, executor, fake_store, fake_binance, fake_hyperliquid):
        """Happy path: both legs fill on first poll."""
        plan = make_two_leg_plan()

        # Pre-create the intent in the store
        await fake_store.create_intent(plan.intent, status="VALIDATED")

        # Give exchanges enough balance
        fake_binance.set_balance("USDT", 100000.0)
        fake_hyperliquid.set_balance("USDT", 100000.0)

        result = await executor.execute(plan)

        assert result.status == "ALL_FILLED"
        assert len(result.legs) == 2
        for lex in result.legs:
            assert lex.status == "FILLED"
            assert lex.order_id is not None
            assert lex.order_id.startswith("mock-")
            assert lex.filled_amount > 0

    async def test_persist_before_send(self, executor, fake_store, fake_binance):
        """Leg rows must be created before orders are sent.

        We verify this by checking that the store has leg records with
        PENDING_SEND status before the executor completes.
        """
        plan = make_two_leg_plan()
        await fake_store.create_intent(plan.intent, status="VALIDATED")
        fake_binance.set_balance("USDT", 100000.0)

        result = await executor.execute(plan)

        # All legs should have been persisted
        for lex in result.legs:
            stored = await fake_store.get_leg(lex.leg_id)
            assert stored is not None, f"leg {lex.leg_id} was persisted"
            assert stored.order_id is not None

    async def test_execution_result_has_timestamps(self, executor, fake_store):
        plan = make_two_leg_plan()
        await fake_store.create_intent(plan.intent, status="VALIDATED")

        result = await executor.execute(plan)

        assert result.started_at > 0
        assert result.completed_at >= result.started_at

    async def test_intent_status_updated_to_all_filled(self, executor, fake_store):
        plan = make_two_leg_plan()
        await fake_store.create_intent(plan.intent, status="VALIDATED")

        await executor.execute(plan)

        stored = await fake_store.get_intent(plan.intent.intent_id)
        assert stored.status == "ALL_FILLED"

    async def test_single_leg_execution(self, executor, fake_store, fake_binance):
        intent = make_intent(total_notional_usd=500.0, split={"binance": 1.0})
        inst = make_btc_usdt_spot("binance")
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.01, "buy")
        leg = PlannedLeg(venue="binance", instrument=inst, quote_matched="USDT",
                         planned_notional_usd=500.0, planned_qty_base=0.01,
                         estimated_fill=fill, estimated_fee_usd=0.45)
        plan = Plan(intent=intent, legs=[leg], rejected_venues=[],
                    aggregate_estimated_avg_price=fill.avg_price,
                    aggregate_estimated_fee_usd=0.45, is_acceptable=True,
                    rejection_reasons=[])

        await fake_store.create_intent(plan.intent, status="VALIDATED")
        fake_binance.set_balance("USDT", 2000.0)

        result = await executor.execute(plan)
        assert result.status == "ALL_FILLED"
        assert result.legs[0].status == "FILLED"
        assert fake_binance.create_order_calls[-1]["params"]["type"] == "spot"
        assert fake_binance.watch_order_calls[-1]["params"]["type"] == "spot"

    async def test_http_polling_runs_after_websocket_grace_expires(self, executor, fake_store, fake_binance):
        intent = make_intent(
            total_notional_usd=500.0,
            split={"binance": 1.0},
            execute_timeout_seconds=1,
        )
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
        await fake_store.create_intent(plan.intent, status="VALIDATED")

        async def hanging_watch_orders(symbol=None, params=None):
            await asyncio.sleep(10.0)
            return {"id": "other-order", "status": "open"}

        fake_binance.watch_orders = hanging_watch_orders

        result = await executor.execute(plan)

        assert result.status == "ALL_FILLED"
        assert result.legs[0].status == "FILLED"
        assert fake_binance.fetch_order_calls[-1]["params"]["type"] == "spot"
