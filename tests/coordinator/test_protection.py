"""Price protection invariants, including unchanged bounds after a requote."""

import time
from dataclasses import replace

import pytest

from src.coordinator.planner import Planner
from src.coordinator.protection import build_leg_protection, compute_leg_qty
from tests.coordinator.conftest import make_btc_usdt_spot, make_intent, make_quote
from tests.coordinator.test_executor import make_two_leg_plan


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_protection_rounds_towards_safety(side):
    plan = make_two_leg_plan()
    leg = replace(plan.legs[0], side=side, reference_price=50000.123)
    plan.intent.max_slippage_pct = 0.123
    protection = build_leg_protection(leg, plan.intent)
    bound = leg.reference_price * (1.00123 if side == "buy" else 0.99877)
    assert protection.limit_price <= bound if side == "buy" else protection.limit_price >= bound
    assert protection.time_in_force == "IOC"


def test_requote_does_not_widen_original_price_bound():
    plan = make_two_leg_plan()
    protection = build_leg_protection(plan.legs[0], plan.intent)
    quote = make_quote(plan.legs[0].instrument, mid=52000)
    with pytest.raises(ValueError, match="fixed protection"):
        protection.validate_quote(quote, 0.01, "buy")


def test_qty_never_rounds_up_to_venue_minimum():
    instrument = make_btc_usdt_spot()
    assert compute_leg_qty(instrument, instrument.min_qty / 2) == 0


@pytest.mark.parametrize("damage", ["stale", "spread", "crossed", "negative", "nan", "unsorted"])
def test_invalid_quote_is_rejected(damage):
    quote = make_quote(make_btc_usdt_spot())
    if damage == "stale":
        quote.exchange_at = time.time() - 10
    elif damage == "spread":
        quote.ask_price = 55000
    elif damage == "crossed":
        quote.bid_price = quote.ask_price + 1
    elif damage == "negative":
        quote._asks[0] = (50000, -1)
    elif damage == "nan":
        quote._bids[0] = (float("nan"), 1)
    elif damage == "unsorted":
        quote._asks.reverse()
    with pytest.raises(ValueError):
        quote.validate(1000, 0.1)


@pytest.mark.asyncio
async def test_fee_budget_is_aggregate(sample_registry, quote_fetcher):
    intent = make_intent(max_fee_usd=0.6)
    plan = await Planner(sample_registry, quote_fetcher).plan(intent)
    assert not plan.is_acceptable
    assert any("aggregate fee" in reason for reason in plan.rejection_reasons)


@pytest.mark.parametrize(
    "field,value", [("max_quote_age_ms", -1), ("max_slippage_pct", float("nan")), ("min_fill_ratio", 0)]
)
def test_intent_rejects_invalid_protection(field, value):
    from dataclasses import asdict

    from src.coordinator.intent import Intent

    values = asdict(make_intent())
    values[field] = value
    with pytest.raises(ValueError):
        Intent(**values)
