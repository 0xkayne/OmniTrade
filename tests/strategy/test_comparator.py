"""Tests for worst-case settlement-aware FundingRateComparator."""

import time

import pytest

from src.market.pair_matcher import CrossVenuePair
from src.strategy.funding_arb.comparator import (
    FundingRateComparator,
    NetReturn,
    _funding_hours,
)
from tests.coordinator.conftest import make_btc_usdt_perp


def make_pair(venue_a="binance", venue_b="hyperliquid"):
    return CrossVenuePair(
        base="BTC",
        venue_a=venue_a,
        venue_b=venue_b,
        instrument_a=make_btc_usdt_perp(venue_a),
        instrument_b=make_btc_usdt_perp(venue_b),
    )


class TestWorstCaseModel:
    """The worst-case model: only the first-settling venue gives guaranteed revenue."""

    def test_hyperliquid_settles_first_with_favorable_rate_is_profitable_if_large(self):
        """HL 1h settles first, rate=-0.5% (receive). Costs=0.2% → profitable."""
        cmp = FundingRateComparator()
        now = time.time()
        nr = cmp.compute_net_return(
            rate_a=-0.005,
            rate_b=0.003,  # A=HL(cheap,long) B=Binance(exp,short)
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,  # 5bp each
            slippage_pct_a=0.01,
            slippage_pct_b=0.01,
            next_funding_a=now + 1800,  # 0.5h → HL first
            next_funding_b=now + 25200,  # 7h → Binance later
        )
        # A settles first, rate=-0.5% → we receive 0.5%
        assert nr.venue_first == "a"
        assert nr.revenue_pct == pytest.approx(0.5, rel=0.01)
        # costs = (0.05+0.05)×2 = 0.2% + 0.02% slippage = 0.22%
        assert nr.fee_cost_pct == pytest.approx(0.2, rel=0.01)
        assert nr.is_profitable  # 0.5% > 0.22%

    def test_typical_rates_are_unprofitable(self):
        """Typical spread (0.08%) with 5bp fees → unprofitable with worst-case model."""
        cmp = FundingRateComparator()
        now = time.time()
        nr = cmp.compute_net_return(
            rate_a=-0.0005,
            rate_b=0.0003,  # HL -0.05%, Binance +0.03%
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
            next_funding_a=now + 1800,  # HL 0.5h → first
            next_funding_b=now + 25200,  # Binance 7h
        )
        # revenue = 0.05%, costs = 0.2% → NOT profitable
        assert nr.venue_first == "a"
        assert nr.revenue_pct == pytest.approx(0.05, rel=0.01)
        assert not nr.is_profitable

    def test_expensive_leg_settles_first_is_always_unprofitable(self):
        """If Binance (expensive, short leg) settles first → we pay, receive 0."""
        cmp = FundingRateComparator()
        now = time.time()
        nr = cmp.compute_net_return(
            rate_a=-0.005,
            rate_b=0.003,  # A cheap, B expensive
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
            next_funding_a=now + 25200,  # HL 7h → second!
            next_funding_b=now + 1800,  # Binance 0.5h → first!
        )
        assert nr.venue_first == "b"
        assert nr.revenue_pct == 0.0  # expensive leg settles first → guaranteed 0
        assert not nr.is_profitable

    def test_equal_intervals_defaults_to_venue_a(self):
        """When intervals are equal, venue A is treated as settling first."""
        cmp = FundingRateComparator()
        nr = cmp.compute_net_return(
            rate_a=-0.005,
            rate_b=0.003,
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
            next_funding_a=None,
            next_funding_b=None,  # both default 8h
        )
        assert nr.venue_first == "a"

    def test_zero_fee_even_tiny_spread_is_profitable(self):
        """With zero fees, even a 0.01% spread is profitable (theoretical baseline)."""
        cmp = FundingRateComparator()
        now = time.time()
        nr = cmp.compute_net_return(
            rate_a=-0.0001,
            rate_b=0.0002,
            taker_fee_a=0.0,
            taker_fee_b=0.0,
            slippage_pct_a=0.0,
            slippage_pct_b=0.0,
            next_funding_a=now + 1800,
            next_funding_b=now + 25200,
        )
        assert nr.revenue_pct > 0
        assert nr.total_cost_pct == 0.0
        assert nr.is_profitable


class TestFundingHours:
    def test_known_future_timestamp(self):
        now = time.time()
        assert _funding_hours(now + 7200, now) == pytest.approx(2.0, rel=0.1)

    def test_past_timestamp_falls_back(self):
        assert _funding_hours(time.time() - 100, time.time()) == 8.0

    def test_none_falls_back(self):
        assert _funding_hours(None, 0) == 8.0

    def test_very_soon_clamped(self):
        now = time.time()
        assert _funding_hours(now + 60, now) >= 0.25
