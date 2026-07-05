"""Tests for Validator perp-specific behaviour: margin and leverage checks."""

import pytest

from src.coordinator.intent import Intent
from src.coordinator.plan import Plan, PlannedLeg
from src.coordinator.validator import Validator
from tests.coordinator.conftest import (
    make_btc_usdt_perp,
    make_intent,
    make_quote,
)


class TestPerpMarginValidation:
    """Validator uses fetch_free_margin() for perp legs (Stage 4)."""

    @pytest.mark.asyncio
    async def test_perp_free_margin_sufficient_accepted(self, fake_exchanges):
        inst = make_btc_usdt_perp("binance", max_leverage=50.0)
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.01, "buy")
        leg = PlannedLeg(
            venue="binance",
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=5000.0,
            planned_qty_base=0.1,
            estimated_fill=fill,
            estimated_fee_usd=4.5,
            leverage=5,
        )
        intent = make_intent(product="perp", leverage=5, total_notional_usd=5000.0, split={"binance": 1.0})
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=4.5,
            is_acceptable=True,
            rejection_reasons=[],
        )
        validator = Validator(fake_exchanges)
        # fake_binance has 50,000 USDT swap margin → margin_required = 5000/5 = 1000
        result = await validator.validate(plan)
        assert result.is_valid

    @pytest.mark.asyncio
    async def test_perp_free_margin_insufficient_rejected(self, fake_exchanges, fake_binance):
        fake_binance.set_margin("USDT", 50.0, account_type="swap")  # too little
        inst = make_btc_usdt_perp("binance", max_leverage=50.0)
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.01, "buy")
        leg = PlannedLeg(
            venue="binance",
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=5000.0,
            planned_qty_base=0.1,
            estimated_fill=fill,
            estimated_fee_usd=4.5,
            leverage=5,
        )
        intent = make_intent(product="perp", leverage=5, total_notional_usd=5000.0, split={"binance": 1.0})
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=4.5,
            is_acceptable=True,
            rejection_reasons=[],
        )
        validator = Validator(fake_exchanges)
        result = await validator.validate(plan)
        assert not result.is_valid
        assert any("insufficient balance" in f[1] for f in result.failures)

    @pytest.mark.asyncio
    async def test_perp_margin_calculation_uses_leverage_division(self, fake_exchanges, fake_binance):
        """Verify margin_required = notional / leverage formula."""
        # Set exactly enough margin (25000 / 5 = 5000 USDT needed)
        fake_binance.set_margin("USDT", 5000.0, account_type="swap")
        inst = make_btc_usdt_perp("binance", max_leverage=50.0)
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.5, "buy")
        leg = PlannedLeg(
            venue="binance",
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=25000.0,
            planned_qty_base=0.5,
            estimated_fill=fill,
            estimated_fee_usd=10.0,
            leverage=5,
        )
        intent = make_intent(product="perp", leverage=5, total_notional_usd=25000.0, split={"binance": 1.0})
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=10.0,
            is_acceptable=True,
            rejection_reasons=[],
        )
        validator = Validator(fake_exchanges)
        result = await validator.validate(plan)
        assert result.is_valid  # 5000 >= 5000


class TestLeverageFeasibility:
    """Validator checks leverage <= instrument.max_leverage (Stage 4)."""

    @pytest.mark.asyncio
    async def test_perp_leverage_exceeds_max_rejected(self, fake_exchanges):
        inst = make_btc_usdt_perp("binance", max_leverage=10.0)  # venue only allows 10x
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
            leverage=100,  # user wants 100x
        )
        intent = make_intent(product="perp", leverage=100, total_notional_usd=500.0, split={"binance": 1.0})
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=0.45,
            is_acceptable=True,
            rejection_reasons=[],
        )
        validator = Validator(fake_exchanges)
        result = await validator.validate(plan)
        assert not result.is_valid
        assert any("leverage" in f[1].lower() for f in result.failures)

    @pytest.mark.asyncio
    async def test_perp_leverage_within_max_accepted(self, fake_exchanges):
        inst = make_btc_usdt_perp("binance", max_leverage=50.0)
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
            leverage=5,
        )
        intent = make_intent(product="perp", leverage=5, total_notional_usd=500.0, split={"binance": 1.0})
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=0.45,
            is_acceptable=True,
            rejection_reasons=[],
        )
        validator = Validator(fake_exchanges)
        result = await validator.validate(plan)
        assert result.is_valid

    @pytest.mark.asyncio
    async def test_perp_no_max_leverage_skips_check(self, fake_exchanges):
        inst = make_btc_usdt_perp("binance", max_leverage=None)  # no limit set
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
            leverage=100,  # would normally be rejected
        )
        intent = make_intent(product="perp", leverage=100, total_notional_usd=500.0, split={"binance": 1.0})
        plan = Plan(
            intent=intent,
            legs=[leg],
            rejected_venues=[],
            aggregate_estimated_avg_price=fill.avg_price,
            aggregate_estimated_fee_usd=0.45,
            is_acceptable=True,
            rejection_reasons=[],
        )
        validator = Validator(fake_exchanges)
        result = await validator.validate(plan)
        assert result.is_valid  # check skipped, not rejected
