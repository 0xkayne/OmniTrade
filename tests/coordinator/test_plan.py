"""Tests for Plan / PlannedLeg construction and aggregate calculations."""

import time

from src.coordinator.plan import Plan, PlannedLeg
from src.market.quote import EstimatedFill
from tests.coordinator.conftest import (
    make_btc_usdt_spot,
    make_intent,
    make_quote,
)


class TestPlanConstruction:
    def test_empty_plan(self):
        intent = make_intent()
        plan = Plan(
            intent=intent,
            legs=[],
            rejected_venues=[("binance", "no instrument")],
            aggregate_estimated_avg_price=0.0,
            aggregate_estimated_fee_usd=0.0,
            is_acceptable=False,
            rejection_reasons=["no instruments found"],
        )
        assert len(plan.legs) == 0
        assert len(plan.rejected_venues) == 1
        assert not plan.is_acceptable

    def test_single_leg_plan(self):
        intent = make_intent(total_notional_usd=500.0)
        inst = make_btc_usdt_spot("binance")
        quote = make_quote(inst, mid=50000.0)
        fill = quote.estimate_fill(0.01, "buy")

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
        assert len(plan.legs) == 1
        assert plan.aggregate_estimated_fee_usd == 0.45
        assert plan.is_acceptable

    def test_two_leg_plan_aggregates(self):
        intent = make_intent(total_notional_usd=1000.0)
        inst1 = make_btc_usdt_spot("binance")
        inst2 = make_btc_usdt_spot("hyperliquid")
        q1 = make_quote(inst1, mid=50000.0)
        q2 = make_quote(inst2, mid=50100.0)
        f1 = q1.estimate_fill(0.01, "buy")
        f2 = q2.estimate_fill(0.00998, "buy")

        leg1 = PlannedLeg(
            venue="binance", instrument=inst1, quote_matched="USDT",
            planned_notional_usd=500.0, planned_qty_base=0.01,
            estimated_fill=f1, estimated_fee_usd=0.45,
        )
        leg2 = PlannedLeg(
            venue="hyperliquid", instrument=inst2, quote_matched="USDT",
            planned_notional_usd=500.0, planned_qty_base=0.00998,
            estimated_fill=f2, estimated_fee_usd=0.45,
        )
        plan = Plan(
            intent=intent,
            legs=[leg1, leg2],
            rejected_venues=[],
            aggregate_estimated_avg_price=(f1.avg_price + f2.avg_price) / 2,
            aggregate_estimated_fee_usd=0.90,
            is_acceptable=True,
            rejection_reasons=[],
        )
        assert len(plan.legs) == 2
        assert plan.aggregate_estimated_fee_usd == 0.90

    def test_rejected_venues(self):
        intent = make_intent()
        plan = Plan(
            intent=intent,
            legs=[],
            rejected_venues=[("binance", "no USDT instrument"), ("hyperliquid", "listing not active")],
            aggregate_estimated_avg_price=0.0,
            aggregate_estimated_fee_usd=0.0,
            is_acceptable=False,
            rejection_reasons=["no instruments found"],
        )
        assert len(plan.rejected_venues) == 2
        assert plan.rejected_venues[0] == ("binance", "no USDT instrument")

    def test_planned_leg_fields(self):
        now = time.time()
        inst = make_btc_usdt_spot("binance")
        fill = EstimatedFill(avg_price=50010.0, slippage_pct=0.02, depth_consumed_levels=1, filled_fully=True)
        leg = PlannedLeg(
            venue="binance",
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=1000.0,
            planned_qty_base=0.02,
            estimated_fill=fill,
            estimated_fee_usd=0.90,
            funding_rate=0.0001,
            next_funding_time=now + 3600,
            selection_log=[{"quote_preference": ["USDT", "USDC"], "selected": "USDT"}],
        )
        assert leg.venue == "binance"
        assert leg.planned_qty_base == 0.02
        assert leg.funding_rate == 0.0001
        assert len(leg.selection_log) == 1
