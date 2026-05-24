"""Tests for Planner."""

import pytest

from src.coordinator.planner import Planner
from src.market.quote import EstimatedFill, Quote
from tests.coordinator.conftest import (
    make_btc_usdt_spot,
    make_eth_usdt_spot,
    make_intent,
    make_quote,
    make_shallow_quote,
    set_quote_via_orderbook,
)


@pytest.fixture
def planner(sample_registry, quote_fetcher) -> Planner:
    return Planner(sample_registry, quote_fetcher)


@pytest.mark.asyncio
class TestPlanner:
    async def test_happy_path_two_venues(self, planner, sample_registry, fake_binance, fake_hyperliquid):
        inst_binance = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        inst_hl = sample_registry.find_one(base="BTC", venue="hyperliquid", market_type="spot", quote_preference=["USDT"])
        set_quote_via_orderbook(fake_binance, inst_binance.venue_symbol, mid=50000.0)
        set_quote_via_orderbook(fake_hyperliquid, inst_hl.venue_symbol, mid=50100.0)

        intent = make_intent(total_notional_usd=1000.0)
        plan = await planner.plan(intent)

        assert plan.is_acceptable
        assert len(plan.legs) == 2
        assert len(plan.rejected_venues) == 0
        assert plan.legs[0].planned_notional_usd == pytest.approx(500.0, rel=0.01)
        assert plan.legs[1].planned_notional_usd == pytest.approx(500.0, rel=0.01)

    async def test_quote_preference_matches_usdc_when_usdt_unavailable(self, planner, sample_registry, fake_binance):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDC"])
        assert inst is not None and inst.quote.symbol == "USDC"
        set_quote_via_orderbook(fake_binance, inst.venue_symbol, mid=50000.0)

        intent = make_intent(
            base="BTC", total_notional_usd=500.0,
            split={"binance": 1.0},
        )
        intent.quote_preference = ["USDC", "USDT"]
        plan = await planner.plan(intent)

        assert plan.is_acceptable
        assert len(plan.legs) == 1
        assert plan.legs[0].quote_matched == "USDC"

    async def test_venue_with_no_instrument_rejected(self, planner, sample_registry, fake_binance):
        intent = make_intent(
            base="BTC", total_notional_usd=1000.0,
            split={"binance": 0.5, "nonexistent_venue": 0.5},
        )
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        set_quote_via_orderbook(fake_binance, inst.venue_symbol, mid=50000.0)

        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.legs) == 1
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

    async def test_slippage_threshold_rejects_leg(self, planner, sample_registry, fake_binance):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        # Wide spread → big slippage for buy side
        fake_binance.set_orderbook(
            inst.venue_symbol,
            bids=[(49999.99, 10.0)],
            asks=[(50500.0, 10.0)],
        )

        intent = make_intent(
            total_notional_usd=500.0, split={"binance": 1.0},
            max_slippage_pct=0.05,
        )
        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.rejected_venues) == 1
        assert "slippage" in plan.rejected_venues[0][1]

    async def test_fee_threshold_rejects_leg(self, planner, sample_registry, fake_binance):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        set_quote_via_orderbook(fake_binance, inst.venue_symbol, mid=50000.0)

        intent = make_intent(
            total_notional_usd=500.0, split={"binance": 1.0},
            max_fee_usd=0.10,
        )
        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.rejected_venues) == 1
        assert "fee" in plan.rejected_venues[0][1]

    async def test_insufficient_depth_rejects_leg(self, planner, sample_registry, fake_binance):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        # Very shallow book
        fake_binance.set_orderbook(
            inst.venue_symbol,
            bids=[(49999.99, 0.0001)],
            asks=[(50000.01, 0.0001)],
        )

        intent = make_intent(
            total_notional_usd=10000.0, split={"binance": 1.0},
        )
        plan = await planner.plan(intent)

        assert not plan.is_acceptable
        assert len(plan.rejected_venues) == 1
        assert "insufficient depth" in plan.rejected_venues[0][1]

    async def test_sell_side_estimates_correctly(self, planner, sample_registry, fake_binance):
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        # Low bids for sell side
        fake_binance.set_orderbook(
            inst.venue_symbol,
            bids=[(49900.0, 10.0)],
            asks=[(50000.01, 10.0)],
        )

        intent = make_intent(
            side="sell", total_notional_usd=500.0, split={"binance": 1.0},
        )
        plan = await planner.plan(intent)

        assert plan.is_acceptable
        assert len(plan.legs) == 1
        assert plan.legs[0].estimated_fill.avg_price < 50000.0

    async def test_min_notional_rejects_undersized_leg(self, sample_registry, quote_fetcher, fake_binance):
        from src.market.asset import Asset
        from src.market.instrument import Instrument

        inst_with_min = Instrument(
            venue="binance",
            market_type="spot",
            base=Asset("BTC"),
            quote=Asset("USDT"),
            venue_symbol="BTCUSDT",
            min_qty=0.00001,
            qty_step=0.00001,
            price_step=0.01,
            min_notional_usd=100.0,
            taker_fee_rate=0.001,
            maker_fee_rate=0.0008,
        )
        sample_registry.add(inst_with_min)
        set_quote_via_orderbook(fake_binance, inst_with_min.venue_symbol, mid=50000.0)

        intent = make_intent(total_notional_usd=20.0, split={"binance": 1.0})
        plan = await Planner(sample_registry, quote_fetcher).plan(intent)

        assert not plan.is_acceptable
        assert len(plan.rejected_venues) == 1
        reason = plan.rejected_venues[0][1]
        assert "below binance minimum" in reason
        assert "$100.00" in reason
