"""Tests for premium mean-reversion FundingRateComparator."""

import time

import pytest

from src.market.pair_matcher import CrossVenuePair
from src.strategy.funding_arb.comparator import FundingRateComparator, _funding_hours
from tests.coordinator.conftest import make_btc_usdt_perp


def make_pair(venue_a="binance", venue_b="hyperliquid"):
    return CrossVenuePair(
        base="BTC",
        venue_a=venue_a,
        venue_b=venue_b,
        instrument_a=make_btc_usdt_perp(venue_a),
        instrument_b=make_btc_usdt_perp(venue_b),
    )


class TestPremiumConvergenceModel:
    """Profit = convergence_pnl + funding_collected - fees - slippage."""

    def test_same_direction_rates_no_arbitrage(self):
        """Both rates positive → no cross-venue divergence → not profitable."""
        cmp = FundingRateComparator()
        nr = cmp.compute_net_return(
            rate_a=0.0003,
            rate_b=0.0005,  # both positive
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
        )
        assert not nr.is_profitable
        assert nr.convergence_pnl_pct == 0.0

    def test_opposite_directions_with_large_premiums_profitable(self):
        """HL -0.50% premium + Binance +0.30% premium → convergence profitable."""
        cmp = FundingRateComparator()
        now = time.time()
        nr = cmp.compute_net_return(
            rate_a=-0.005,
            rate_b=0.003,
            premium_a=-0.50,
            premium_b=0.30,  # large divergence
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
            next_funding_a=now + 1800,
            next_funding_b=now + 25200,
        )
        # convergence = (0.50 + 0.30) × 0.5 = 0.40%
        # funding = 0.50% (A settles first, rate=-0.5% → receive 0.5%)
        # total revenue = 0.90%
        # costs = 0.20% + 0% slippage = 0.20%
        # net = 0.70% → PROFITABLE
        assert nr.convergence_pnl_pct == pytest.approx(0.40, rel=0.01)
        assert nr.funding_pnl_pct == pytest.approx(0.50, rel=0.01)
        assert nr.is_profitable

    def test_small_premiums_not_profitable(self):
        """Small premiums can't overcome fees."""
        cmp = FundingRateComparator()
        nr = cmp.compute_net_return(
            rate_a=-0.0005,
            rate_b=0.0003,
            premium_a=-0.05,
            premium_b=0.03,  # tiny divergence
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
        )
        # convergence = (0.05+0.03)×0.5 = 0.04%
        # funding = 0.05%
        # total = 0.09% < fees 0.20% → NOT profitable
        assert not nr.is_profitable

    def test_convergence_alone_insufficient_without_funding(self):
        """Convergence exists but expensive leg settles first → no funding."""
        cmp = FundingRateComparator()
        now = time.time()
        nr = cmp.compute_net_return(
            rate_a=-0.003,
            rate_b=0.005,
            premium_a=-0.30,
            premium_b=0.50,
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
            next_funding_a=now + 25200,  # A settles later
            next_funding_b=now + 1800,  # B (expensive) settles first → funding=0
        )
        assert nr.venue_first == "b"
        # convergence = 0.40%, funding = 0
        # total = 0.40% > fees 0.20% → still profitable
        assert nr.is_profitable
        assert nr.funding_pnl_pct == 0.0

    def test_zero_cost_always_profitable_if_opposite(self):
        """Baseline: opposite rates + zero fees → profitable."""
        cmp = FundingRateComparator()
        nr = cmp.compute_net_return(
            rate_a=-0.001,
            rate_b=0.002,
            premium_a=-0.10,
            premium_b=0.20,
            taker_fee_a=0.0,
            taker_fee_b=0.0,
            slippage_pct_a=0.0,
            slippage_pct_b=0.0,
        )
        assert nr.is_profitable
        assert nr.total_cost_pct == 0.0


class TestFundingHours:
    def test_known_future_timestamp(self):
        now = time.time()
        assert _funding_hours(now + 7200, now) == pytest.approx(2.0, rel=0.1)

    def test_past_falls_back(self):
        assert _funding_hours(time.time() - 100, time.time()) == 8.0

    def test_none_falls_back(self):
        assert _funding_hours(None, 0) == 8.0
