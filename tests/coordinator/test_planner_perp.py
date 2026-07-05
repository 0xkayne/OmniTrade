"""Tests for Planner perp-specific behaviour: funding rate threshold checks."""

import time

import pytest

from src.coordinator.plan import PlannedLeg
from src.market.quote import EstimatedFill
from tests.coordinator.conftest import (
    BTC,
    USDT,
    make_btc_usdt_perp,
    make_intent,
    make_quote,
)


class TestFundingRateThreshold:
    """Planner._check_thresholds rejects legs when funding rate exceeds the cap."""

    def _make_perp_leg(self, **kwargs):
        inst = make_btc_usdt_perp("binance")
        q = make_quote(inst, mid=50000.0, funding_rate=0.0001, next_funding_time=time.time() + 3600)
        fill = q.estimate_fill(0.01, "buy")
        defaults = {
            "venue": "binance",
            "instrument": inst,
            "quote_matched": "USDT",
            "planned_notional_usd": 500.0,
            "planned_qty_base": 0.01,
            "estimated_fill": fill,
            "estimated_fee_usd": 0.45,
            "funding_rate": q.funding_rate,
            "next_funding_time": q.next_funding_time,
        }
        defaults.update(kwargs)
        return PlannedLeg(**defaults)

    def test_funding_rate_above_threshold_rejected(self):
        """Quote funding_rate=0.1% (high), Intent max=0.05% — leg rejected."""
        intent = make_intent(product="perp", max_funding_rate_pct=0.05)
        leg = self._make_perp_leg(funding_rate=0.001)  # 0.1%
        from src.coordinator.planner import Planner

        violations = Planner._check_thresholds(
            "binance", leg.estimated_fill, leg.estimated_fee_usd, leg.funding_rate, intent
        )
        assert len(violations) == 1
        assert "funding rate" in violations[0]
        assert "0.1000%" in violations[0]

    def test_funding_rate_below_threshold_accepted(self):
        """Quote funding_rate=0.01%, Intent max=0.1% — accepted."""
        intent = make_intent(product="perp", max_funding_rate_pct=0.1)
        leg = self._make_perp_leg(funding_rate=0.0001)  # 0.01%
        from src.coordinator.planner import Planner

        violations = Planner._check_thresholds(
            "binance", leg.estimated_fill, leg.estimated_fee_usd, leg.funding_rate, intent
        )
        assert len(violations) == 0

    def test_funding_rate_none_skips_check(self):
        """When funding_rate=None, the threshold check is skipped (backward compat)."""
        intent = make_intent(product="perp", max_funding_rate_pct=0.001)
        leg = self._make_perp_leg(funding_rate=None, next_funding_time=None)
        from src.coordinator.planner import Planner

        violations = Planner._check_thresholds(
            "binance", leg.estimated_fill, leg.estimated_fee_usd, leg.funding_rate, intent
        )
        assert len(violations) == 0

    def test_no_max_funding_rate_set_skips_check(self):
        """When max_funding_rate_pct=None on Intent, check is skipped."""
        intent = make_intent(product="perp", max_funding_rate_pct=None)
        leg = self._make_perp_leg(funding_rate=0.01)  # very high
        from src.coordinator.planner import Planner

        violations = Planner._check_thresholds(
            "binance", leg.estimated_fill, leg.estimated_fee_usd, leg.funding_rate, intent
        )
        assert len(violations) == 0

    def test_plan_carries_funding_in_legs(self):
        """PlannedLeg carries funding_rate and next_funding_time for perp quotes."""
        inst = make_btc_usdt_perp("binance")
        now = time.time()
        q = make_quote(inst, mid=50000.0, funding_rate=0.0001, next_funding_time=now + 3600)
        fill = q.estimate_fill(0.01, "buy")

        leg = PlannedLeg(
            venue="binance",
            instrument=inst,
            quote_matched="USDT",
            planned_notional_usd=500.0,
            planned_qty_base=0.01,
            estimated_fill=fill,
            estimated_fee_usd=0.45,
            funding_rate=q.funding_rate,
            next_funding_time=q.next_funding_time,
        )
        assert leg.funding_rate == 0.0001
        assert leg.next_funding_time == pytest.approx(now + 3600, rel=0.01)
