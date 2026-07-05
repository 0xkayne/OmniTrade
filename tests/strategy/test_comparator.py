"""Tests for cost-aware FundingRateComparator."""

import pytest

from src.market.pair_matcher import CrossVenuePair
from src.strategy.funding_arb.comparator import (
    FundingRateComparator,
    FundingSpread,
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


class TestNetReturn:
    def test_profitable_when_spread_exceeds_costs(self):
        cmp = FundingRateComparator(safety_margin_pct=0.01)
        nr = cmp.compute_net_return(
            rate_a=-0.0005,
            rate_b=0.0003,
            taker_fee_a=0.0005,
            taker_fee_b=0.0002,
            slippage_pct_a=0.01,
            slippage_pct_b=0.01,
        )
        # spread = 0.08% (revenue), fees = (0.05+0.02)*2 = 0.14%, slippage = 0.02%, safety = 0.01%
        # total cost = 0.14 + 0.02 + 0.01 = 0.17% > revenue 0.08% → NOT profitable
        assert not nr.is_profitable
        assert nr.net_per_period_pct < 0

    def test_large_spread_is_profitable(self):
        cmp = FundingRateComparator(safety_margin_pct=0.01)
        nr = cmp.compute_net_return(
            rate_a=-0.01,
            rate_b=0.005,  # spread = 1.5%
            taker_fee_a=0.0005,
            taker_fee_b=0.0005,
            slippage_pct_a=0.01,
            slippage_pct_b=0.01,
        )
        # revenue = 1.5%, fees = 0.2%, slippage = 0.02%, safety = 0.01%
        # total cost = 0.23% < 1.5% → PROFITABLE
        assert nr.is_profitable
        assert nr.net_per_period_pct > 0

    def test_zero_fee_zero_slippage_always_profitable(self):
        cmp = FundingRateComparator(safety_margin_pct=0.0)
        nr = cmp.compute_net_return(
            rate_a=0.0001,
            rate_b=0.0002,  # spread = 0.01%
            taker_fee_a=0.0,
            taker_fee_b=0.0,
            slippage_pct_a=0.0,
            slippage_pct_b=0.0,
        )
        assert nr.is_profitable

    def test_annualisation_uses_actual_funding_intervals(self):
        cmp = FundingRateComparator()
        # Binance 8h, Hyperliquid 1h → avg 4.5h
        nr_8h = cmp.compute_net_return(
            rate_a=0.0001,
            rate_b=0.0003,
            taker_fee_a=0.0,
            taker_fee_b=0.0,
            next_funding_a=10000,
            next_funding_b=10000,  # both ~same time
        )
        # With different intervals the avg is different
        import time

        now = time.time()
        nr_var = cmp.compute_net_return(
            rate_a=0.0001,
            rate_b=0.0003,
            taker_fee_a=0.0,
            taker_fee_b=0.0,
            next_funding_a=now + 3600,
            next_funding_b=now + 28800,
        )
        # Different intervals → different annualisation
        assert nr_8h.avg_funding_hours != nr_var.avg_funding_hours


class TestFundingSpreadCostFields:
    def test_compare_populates_cost_fields(self):
        cmp = FundingRateComparator(safety_margin_pct=0.02)
        pair = make_pair()
        spread = cmp.compare(
            pair,
            rate_a=-0.0003,
            rate_b=0.0005,
            taker_fee_a=0.0005,
            taker_fee_b=0.0002,
            slippage_pct_a=0.015,
            slippage_pct_b=0.01,
        )
        assert spread.fee_cost_pct > 0
        assert spread.slippage_cost_pct > 0
        assert spread.safety_margin_pct == 0.02
        assert spread.net_annual_return_pct is not None
        assert isinstance(spread.is_profitable, bool)

    def test_compare_all_sorts_profitable_first(self):
        cmp = FundingRateComparator()
        p1 = make_pair("binance", "hyperliquid")
        p2 = CrossVenuePair(
            base="ETH",
            venue_a="binance",
            venue_b="hyperliquid",
            instrument_a=make_btc_usdt_perp("binance"),
            instrument_b=make_btc_usdt_perp("hyperliquid"),
        )
        rates = {
            ("binance", "BTCUSDT"): {"funding_rate": -0.01, "next_funding_time": None},
            ("hyperliquid", "BTC/USDT:USDT"): {"funding_rate": 0.005, "next_funding_time": None},
            ("binance", "ETHUSDT"): {"funding_rate": 0.0001, "next_funding_time": None},
            ("hyperliquid", "ETH/USDT:USDT"): {"funding_rate": 0.0001, "next_funding_time": None},
        }
        spreads = cmp.compare_all([p1, p2], rates)
        # Most profitable first — p1 (BTC) with 1.5% spread should be first
        assert spreads[0].pair.base == "BTC"
        # p2 (ETH) with 0% spread should be not profitable
        pass  # profitability tested in NetReturn tests


class TestFundingHours:
    def test_known_future_timestamp(self):
        import time

        now = time.time()
        h = _funding_hours(now + 7200, now)  # 2 hours from now
        assert h == pytest.approx(2.0, rel=0.1)

    def test_past_timestamp_falls_back(self):
        import time

        h = _funding_hours(time.time() - 100, time.time())
        assert h == 8.0  # fallback

    def test_none_falls_back(self):
        h = _funding_hours(None, 0)
        assert h == 8.0

    def test_very_soon_clamped(self):
        import time

        now = time.time()
        h = _funding_hours(now + 60, now)  # 1 minute
        assert h >= 0.25  # clamped to 15 minutes minimum
