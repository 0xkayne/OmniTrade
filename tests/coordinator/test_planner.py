"""Tests for Planner."""

import pytest

from src.coordinator.planner import Planner
from src.market.quote import EstimatedFill, Quote
from tests.coordinator.conftest import (
    FakeQuoteFetcher,
    make_btc_usdt_spot,
    make_eth_usdt_spot,
    make_intent,
    make_quote,
    make_shallow_quote,
)


@pytest.fixture
def planner(sample_registry, quote_fetcher) -> Planner:
    return Planner(sample_registry, quote_fetcher)


@pytest.mark.asyncio
class TestPlanner:
    async def test_happy_path_two_venues(self, planner, quote_fetcher, sample_registry):
        """Both venues match, plan is acceptable."""
        inst_binance = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        inst_hl = sample_registry.find_one(base="BTC", venue="hyperliquid", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst_binance, make_quote(inst_binance, mid=50000.0))
        quote_fetcher.set_quote(inst_hl, make_quote(inst_hl, mid=50100.0))

        intent = make_intent(total_notional_usd=1000.0)
        plan = await planner.plan(intent)

        assert plan.is_acceptable
        assert len(plan.legs) == 2
        assert len(plan.rejected_venues) == 0
        # Each leg gets ~$500 notional
        assert plan.legs[0].planned_notional_usd == pytest.approx(500.0, rel=0.01)
        assert plan.legs[1].planned_notional_usd == pytest.approx(500.0, rel=0.01)

    async def test_quote_preference_matches_usdc_when_usdt_unavailable(self, planner, quote_fetcher, sample_registry):
        """binance has both USDT and USDC; with preference [USDC, USDT] it picks USDC."""
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDC"])
        assert inst is not None and inst.quote.symbol == "USDC"
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        intent = make_intent(
            base="BTC", total_notional_usd=500.0,
            split={"binance": 1.0},
        )
        intent.quote_preference = ["USDC", "USDT"]
        plan = await planner.plan(intent)

        assert plan.is_acceptable
        assert len(plan.legs) == 1
        assert plan.legs[0].quote_matched == "USDC"

    async def test_venue_with_no_instrument_rejected(self, planner, quote_fetcher, sample_registry):
        """A venue not in the registry is rejected."""
        intent = make_intent(
            base="BTC", total_notional_usd=1000.0,
            split={"binance": 0.5, "nonexistent_venue": 0.5},
        )
        # Give binance a quote
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        plan = await planner.plan(intent)

        assert not plan.is_acceptable  # one venue rejected
        assert len(plan.legs) == 1  # binance still planned
        assert len(plan.rejected_venues) == 1
        assert plan.rejected_venues[0][0] == "nonexistent_venue"

    async def test_all_venues_rejected(self, planner):
        intent = make_intent(
            base="BTC", total_notional_usd=1000.0,
            split={"venue_x": 0.5, "venue_y": 0.5},
        )
        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.legs) == 0
        assert len(plan.rejected_venues) == 2

    async def test_slippage_threshold_rejects_leg(self, planner, quote_fetcher, sample_registry):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        # Create a quote with wider spread to generate slippage
        q = make_quote(inst, mid=50000.0)
        q._asks = [(50500.0, 10.0)]  # big spread for buy side
        quote_fetcher.set_quote(inst, q)

        intent = make_intent(
            total_notional_usd=500.0, split={"binance": 1.0},
            max_slippage_pct=0.05,  # very tight
        )
        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.rejected_venues) == 1
        assert "slippage" in plan.rejected_venues[0][1]

    async def test_fee_threshold_rejects_leg(self, planner, quote_fetcher, sample_registry):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        intent = make_intent(
            total_notional_usd=500.0, split={"binance": 1.0},
            max_fee_usd=0.10,  # fee would be ~$0.45 (0.0009 * 500)
        )
        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.rejected_venues) == 1
        assert "fee" in plan.rejected_venues[0][1]

    async def test_insufficient_depth_rejects_leg(self, planner, quote_fetcher, sample_registry):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        # Very shallow book
        quote_fetcher.set_quote(inst, make_shallow_quote(inst, mid=50000.0))

        intent = make_intent(
            total_notional_usd=10000.0, split={"binance": 1.0},
        )
        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.rejected_venues) == 1
        assert "insufficient depth" in plan.rejected_venues[0][1]

    async def test_sell_side_estimates_correctly(self, planner, quote_fetcher, sample_registry):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        q = make_quote(inst, mid=50000.0)
        q._bids = [(49900.0, 10.0)]  # below mid for sell side
        quote_fetcher.set_quote(inst, q)

        intent = make_intent(
            side="sell", total_notional_usd=500.0, split={"binance": 1.0},
        )
        plan = await planner.plan(intent)

        assert plan.is_acceptable
        assert len(plan.legs) == 1
        # Sell should consume bids, so avg_price should be near 49900
        assert plan.legs[0].estimated_fill.avg_price < 50000.0
