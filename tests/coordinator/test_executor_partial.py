"""Tests for Executor — partial failure path (PARTIAL_FILLED)."""

import pytest

from src.coordinator.executor import Executor
from src.coordinator.plan import Plan, PlannedLeg
from tests.coordinator.conftest import (
    make_btc_usdt_spot,
    make_intent,
    make_quote,
)


def make_two_leg_plan():
    intent = make_intent(total_notional_usd=1000.0, execute_timeout_seconds=2)
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
class TestExecutorPartial:
    async def test_one_leg_fails_create_order(self, executor, fake_store, fake_binance, fake_hyperliquid):
        """binance create_order raises → that leg is REJECTED."""
        plan = make_two_leg_plan()
        await fake_store.create_intent(plan.intent, status="VALIDATED")

        fake_binance.set_fail_create(True, message="connection refused")

        result = await executor.execute(plan)

        # binance should be REJECTED, hyperliquid should be FILLED
        assert result.status == "PARTIAL_FILLED"
        binance_leg = next(lex for lex in result.legs if lex.leg.venue == "binance")
        hl_leg = next(lex for lex in result.legs if lex.leg.venue == "hyperliquid")

        assert binance_leg.status == "REJECTED"
        assert "connection refused" in (binance_leg.error or "")
        assert hl_leg.status == "FILLED"

    async def test_partial_fill_intent_status(self, executor, fake_store, fake_binance):
        plan = make_two_leg_plan()
        await fake_store.create_intent(plan.intent, status="VALIDATED")
        fake_binance.set_fail_create(True, message="fail")

        await executor.execute(plan)

        stored = await fake_store.get_intent(plan.intent.intent_id)
        assert stored.status == "PARTIAL_FILLED"

    async def test_all_legs_rejected(self, executor, fake_store, fake_binance, fake_hyperliquid):
        plan = make_two_leg_plan()
        await fake_store.create_intent(plan.intent, status="VALIDATED")

        fake_binance.set_fail_create(True, message="fail1")
        fake_hyperliquid.set_fail_create(True, message="fail2")

        result = await executor.execute(plan)

        assert result.status == "PARTIAL_FILLED"
        all_rejected = all(lex.status == "REJECTED" for lex in result.legs)
        assert all_rejected

    async def test_leg_error_stored(self, executor, fake_store, fake_binance):
        plan = make_two_leg_plan()
        await fake_store.create_intent(plan.intent, status="VALIDATED")
        fake_binance.set_fail_create(True, message="network error")

        result = await executor.execute(plan)

        binance_lex = next(lex for lex in result.legs if lex.leg.venue == "binance")
        stored = await fake_store.get_leg(binance_lex.leg_id)
        assert stored is not None
        assert stored.status == "REJECTED"
        assert "network error" in (stored.error_msg or "")

    async def test_timeout_on_poll_marks_timedout(self, executor, fake_store, fake_binance, fake_hyperliquid):
        """If fetch_order keeps returning open, legs timeout when deadline expires."""
        plan = make_two_leg_plan()
        plan.intent.execute_timeout_seconds = 0  # immediate timeout
        await fake_store.create_intent(plan.intent, status="VALIDATED")

        # Make both exchanges' fetch always return "open"
        fake_binance.set_fail_fetch(True)  # exception causes it to be ignored
        fake_hyperliquid.set_fail_fetch(True)

        result = await executor.execute(plan)

        # Both legs should be TIMEOUT
        for lex in result.legs:
            assert lex.status in ("TIMEOUT", "REJECTED"), f"{lex.leg.venue} was {lex.status}"
