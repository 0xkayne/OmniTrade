"""Tests for RiskValidator pre-execution guardrails."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.coordinator.risk import RiskResult, RiskValidator


class TestRiskValidator:
    """Unit tests for RiskValidator using a mocked store."""

    def _make_validator(self, config=None, store=None):
        if store is None:
            store = MagicMock()
            store.get_daily_pnl = AsyncMock(return_value=None)
            store.get_venue_exposure = AsyncMock(return_value=None)
        else:
            if not isinstance(store.get_daily_pnl, AsyncMock):
                store.get_daily_pnl = AsyncMock(return_value=None)
            if not isinstance(store.get_venue_exposure, AsyncMock):
                store.get_venue_exposure = AsyncMock(return_value=None)
        return RiskValidator(store, config)

    def _make_intent(self, total_notional_usd=1000.0):
        from src.coordinator.intent import Intent

        return Intent(
            intent_id="risk-test-001",
            base="BTC",
            quote_preference=["USDT"],
            product="spot",
            side="buy",
            order_type="market",
            total_notional_usd=total_notional_usd,
            split={"binance": 0.5, "hyperliquid": 0.5},
        )

    def _make_plan(self, intent=None):
        from src.coordinator.plan import Plan

        intent = intent or self._make_intent()
        return Plan(intent=intent, legs=[], rejected_venues=[],
                     aggregate_estimated_avg_price=50000.0,
                     aggregate_estimated_fee_usd=1.0,
                     is_acceptable=True, rejection_reasons=[])

    # ── max_notional_per_intent ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_max_notional_below_threshold_allowed(self):
        validator = self._make_validator({"max_notional_per_intent": 5000})
        intent = self._make_intent(total_notional_usd=1000)
        result = await validator.check(intent, self._make_plan(intent))
        assert result.is_allowed

    @pytest.mark.asyncio
    async def test_max_notional_above_threshold_rejected(self):
        validator = self._make_validator({"max_notional_per_intent": 5000})
        intent = self._make_intent(total_notional_usd=10000)
        result = await validator.check(intent, self._make_plan(intent))
        assert not result.is_allowed
        assert any("notional" in f.lower() for f in result.failures)

    @pytest.mark.asyncio
    async def test_max_notional_unset_allows_all(self):
        validator = self._make_validator({})
        intent = self._make_intent(total_notional_usd=999999)
        result = await validator.check(intent, self._make_plan(intent))
        assert result.is_allowed

    # ── daily_loss_limit ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_daily_loss_within_limit_allowed(self):
        store = MagicMock()
        store.get_daily_pnl = AsyncMock(return_value=-500.0)
        store.get_venue_exposure = AsyncMock(return_value=None)
        validator = self._make_validator({"daily_loss_limit_usd": 1000}, store=store)
        result = await validator.check(self._make_intent(), self._make_plan())
        assert result.is_allowed

    @pytest.mark.asyncio
    async def test_daily_loss_exceeds_limit_rejected(self):
        store = MagicMock()
        store.get_daily_pnl = AsyncMock(return_value=-1500.0)
        store.get_venue_exposure = AsyncMock(return_value=None)
        validator = self._make_validator({"daily_loss_limit_usd": 1000}, store=store)
        result = await validator.check(self._make_intent(), self._make_plan())
        assert not result.is_allowed
        assert any("daily" in f.lower() for f in result.failures)

    @pytest.mark.asyncio
    async def test_daily_loss_no_data_allowed(self):
        store = MagicMock()
        store.get_daily_pnl = AsyncMock(return_value=None)  # no trades today
        store.get_venue_exposure = AsyncMock(return_value=None)
        validator = self._make_validator({"daily_loss_limit_usd": 1000}, store=store)
        result = await validator.check(self._make_intent(), self._make_plan())
        assert result.is_allowed

    @pytest.mark.asyncio
    async def test_daily_loss_profitable_allowed(self):
        store = MagicMock()
        store.get_daily_pnl = AsyncMock(return_value=500.0)  # positive PnL
        store.get_venue_exposure = AsyncMock(return_value=None)
        validator = self._make_validator({"daily_loss_limit_usd": 1000}, store=store)
        result = await validator.check(self._make_intent(), self._make_plan())
        assert result.is_allowed

    # ── rate limiting ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rate_limit_allows_up_to_limit(self):
        validator = self._make_validator({"rate_limit": {"max_orders": 3, "window_seconds": 60}})
        for _ in range(3):
            result = await validator.check(self._make_intent(), self._make_plan())
            assert result.is_allowed

    @pytest.mark.asyncio
    async def test_rate_limit_rejects_exceeding(self):
        validator = self._make_validator({"rate_limit": {"max_orders": 2, "window_seconds": 60}})
        await validator.check(self._make_intent(), self._make_plan())  # 1
        await validator.check(self._make_intent(), self._make_plan())  # 2
        result = await validator.check(self._make_intent(), self._make_plan())  # 3 → reject
        assert not result.is_allowed
        assert any("rate limit" in f.lower() for f in result.failures)

    @pytest.mark.asyncio
    async def test_rate_limit_expires_after_window(self):
        validator = self._make_validator({"rate_limit": {"max_orders": 2, "window_seconds": 0.1}})
        await validator.check(self._make_intent(), self._make_plan())
        await validator.check(self._make_intent(), self._make_plan())
        # Window is 100ms — wait for it to expire
        await asyncio.sleep(0.15)
        result = await validator.check(self._make_intent(), self._make_plan())
        assert result.is_allowed

    # ── all checks pass ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_all_checks_pass_with_full_config(self):
        store = MagicMock()
        store.get_daily_pnl = AsyncMock(return_value=-100.0)
        store.get_venue_exposure = AsyncMock(return_value=200.0)
        validator = self._make_validator(
            {
                "max_notional_per_intent": 5000,
                "daily_loss_limit_usd": 1000,
                "max_venue_exposure_usd": 50000,
                "rate_limit": {"max_orders": 10, "window_seconds": 60},
            },
            store=store,
        )
        result = await validator.check(self._make_intent(1000), self._make_plan())
        assert result.is_allowed
        assert len(result.failures) == 0
