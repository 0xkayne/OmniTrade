"""Tests for Validator."""

import pytest

from src.coordinator.plan import Plan, PlannedLeg
from src.coordinator.validator import ValidationResult, Validator
from tests.coordinator.conftest import (
    FakeExchange,
    make_btc_usdt_spot,
    make_intent,
    make_quote,
)


@pytest.fixture
def validator(fake_exchanges) -> Validator:
    return Validator(fake_exchanges)


def make_valid_plan(intent=None, legs=None) -> Plan:
    intent = intent or make_intent()
    if legs:
        return Plan(
            intent=intent, legs=legs, rejected_venues=[],
            aggregate_estimated_avg_price=50000.0, aggregate_estimated_fee_usd=0.90,
            is_acceptable=True, rejection_reasons=[],
        )
    inst = make_btc_usdt_spot("binance")
    q = make_quote(inst, mid=50000.0)
    fill = q.estimate_fill(0.01, "buy")
    leg = PlannedLeg(
        venue="binance", instrument=inst, quote_matched="USDT",
        planned_notional_usd=500.0, planned_qty_base=0.01,
        estimated_fill=fill, estimated_fee_usd=0.45,
    )
    return Plan(
        intent=intent, legs=[leg], rejected_venues=[],
        aggregate_estimated_avg_price=fill.avg_price,
        aggregate_estimated_fee_usd=0.45, is_acceptable=True, rejection_reasons=[],
    )


@pytest.mark.asyncio
class TestValidator:
    async def test_happy_path_all_valid(self, validator):
        plan = make_valid_plan()
        result = await validator.validate(plan)

        assert result.is_valid
        assert len(result.failures) == 0

    async def test_insufficient_balance(self, validator, fake_binance):
        """Balance of 100,000 USDT is fine for $500 order. Let's make it insufficient."""
        fake_binance.set_balance("USDT", 100.0)  # only $100 available
        plan = make_valid_plan()
        result = await validator.validate(plan)

        assert not result.is_valid
        assert any("insufficient balance" in f[1] for f in result.failures)

    async def test_symbol_not_trading(self, validator):
        inst = make_btc_usdt_spot("binance")
        inst = make_btc_usdt_spot("binance")
        # Create a new frozen instrument with non-trading status
        from src.market.asset import Asset
        from src.market.instrument import Instrument
        frozen = Instrument(
            venue="binance", market_type="spot", base=Asset("BTC"), quote=Asset("USDT"),
            venue_symbol="BTCUSDT", min_qty=0.00001, qty_step=0.00001, price_step=0.01,
            taker_fee_rate=0.001, maker_fee_rate=0.0008, listing_status="delisted",
        )
        q = make_quote(frozen, mid=50000.0)
        fill = q.estimate_fill(0.01, "buy")
        leg = PlannedLeg(
            venue="binance", instrument=frozen, quote_matched="USDT",
            planned_notional_usd=500.0, planned_qty_base=0.01,
            estimated_fill=fill, estimated_fee_usd=0.45,
        )
        plan = make_valid_plan(legs=[leg])
        result = await validator.validate(plan)
        assert not result.is_valid
        assert any("not trading" in f[1] for f in result.failures)

    async def test_qty_below_min_qty(self, validator):
        inst = make_btc_usdt_spot("binance")
        inst = make_btc_usdt_spot("binance")
        from src.market.asset import Asset
        from src.market.instrument import Instrument
        tiny_min = Instrument(
            venue="binance", market_type="spot", base=Asset("BTC"), quote=Asset("USDT"),
            venue_symbol="BTCUSDT", min_qty=0.01, qty_step=0.01, price_step=0.01,
            taker_fee_rate=0.001, maker_fee_rate=0.0008,
        )
        q = make_quote(tiny_min, mid=50000.0)
        fill = q.estimate_fill(0.0003, "buy")
        leg = PlannedLeg(
            venue="binance", instrument=tiny_min, quote_matched="USDT",
            planned_notional_usd=15.0, planned_qty_base=0.0003,
            estimated_fill=fill, estimated_fee_usd=0.01,
        )
        plan = make_valid_plan(legs=[leg])
        result = await validator.validate(plan)
        assert not result.is_valid
        assert any("below min_qty" in f[1] for f in result.failures)

    async def test_multi_leg_mixed_failure(self, validator):
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

        plan = make_valid_plan(make_intent(), legs=[leg1, leg2])
        result = await validator.validate(plan)
        assert result.is_valid  # both have sufficient balance

    async def test_validation_result_dataclass(self):
        vr = ValidationResult(is_valid=True, failures=[])
        assert vr.is_valid
        assert len(vr.failures) == 0

        vr2 = ValidationResult(is_valid=False, failures=[("binance", "bad")])
        assert not vr2.is_valid
        assert vr2.failures[0][0] == "binance"

    async def test_no_exchange_configured_returns_failure(self, validator):
        """A venue not in the exchanges dict should fail validation."""
        inst = make_btc_usdt_spot("unknown_venue")
        q = make_quote(inst, mid=50000.0)
        fill = q.estimate_fill(0.01, "buy")
        leg = PlannedLeg(
            venue="unknown_venue", instrument=inst, quote_matched="USDT",
            planned_notional_usd=500.0, planned_qty_base=0.01,
            estimated_fill=fill, estimated_fee_usd=0.45,
        )
        plan = make_valid_plan(legs=[leg])
        result = await validator.validate(plan)
        assert not result.is_valid
        assert any("no exchange configured" in f[1] for f in result.failures)
