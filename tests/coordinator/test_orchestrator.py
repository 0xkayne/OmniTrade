"""Tests for Orchestrator — full pipeline integration."""

import pytest

from src.coordinator.orchestrator import Orchestrator
from src.coordinator.plan import Plan, PlannedLeg
from tests.coordinator.conftest import (
    FakeQuoteFetcher,
    make_btc_usdt_spot,
    make_intent,
    make_quote,
)


@pytest.fixture
def orchestrator(sample_registry, quote_fetcher, fake_exchanges, fake_store) -> Orchestrator:
    return Orchestrator(sample_registry, quote_fetcher, fake_exchanges, fake_store)


@pytest.mark.asyncio
class TestOrchestrator:
    async def test_full_pipeline_happy_path(self, orchestrator, quote_fetcher, sample_registry):
        """E2E: plan -> validate -> execute -> ALL_FILLED."""
        inst_binance = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        inst_hl = sample_registry.find_one(base="BTC", venue="hyperliquid", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst_binance, make_quote(inst_binance, mid=50000.0))
        quote_fetcher.set_quote(inst_hl, make_quote(inst_hl, mid=50100.0))

        intent = make_intent(total_notional_usd=1000.0)
        result = await orchestrator.submit(intent)

        assert result["status"] == "ALL_FILLED"
        assert result["intent_id"] == intent.intent_id
        assert len(result["legs"]) == 2
        for leg in result["legs"]:
            assert leg["status"] == "FILLED"
            assert leg["order_id"] is not None

    async def test_dry_run_stops_after_plan(self, orchestrator, quote_fetcher, sample_registry):
        """Dry run returns plan info without executing."""
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        intent = make_intent(total_notional_usd=500.0, split={"binance": 1.0})
        result = await orchestrator.submit(intent, dry_run=True)

        assert result["status"] == "DRY_RUN"
        assert "plan" in result
        assert len(result["plan"]["legs"]) == 1
        assert result["plan"]["is_acceptable"] is True

    async def test_blocked_by_needs_manual(self, orchestrator, fake_store):
        """When store says blocked, reject immediately."""
        fake_store.set_blocked(True)

        intent = make_intent()
        result = await orchestrator.submit(intent)

        assert result["status"] == "REJECTED"
        assert "NEEDS_MANUAL" in result["reason"]

    async def test_pipeline_rejected_when_no_instruments(self, orchestrator):
        """When no venues match, Plan is rejected."""
        intent = make_intent(base="SOL", split={"binance": 1.0})
        result = await orchestrator.submit(intent)

        assert result["status"] == "REJECTED"
        assert "not acceptable" in result["reason"]

    async def test_plan_not_acceptable_returns_rejected(self, orchestrator, quote_fetcher, sample_registry):
        """When Plan.is_acceptable is False, return REJECTED."""
        intent = make_intent(
            total_notional_usd=500.0, split={"binance": 1.0},
            max_fee_usd=0.01,  # impossibly tight
        )
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        result = await orchestrator.submit(intent)

        assert result["status"] == "REJECTED"

    async def test_result_dict_has_expected_shape(self, orchestrator, quote_fetcher, sample_registry):
        """Verify the return dict has the expected top-level keys."""
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        intent = make_intent(total_notional_usd=500.0, split={"binance": 1.0})
        result = await orchestrator.submit(intent)

        assert "status" in result
        assert "intent_id" in result
        assert "legs" in result
        assert isinstance(result["legs"], list)

    async def test_unacceptable_plan_sets_rejected_status(self, orchestrator, fake_store):
        intent = make_intent(base="SOL", split={"binance": 1.0})
        result = await orchestrator.submit(intent)

        assert result["status"] == "REJECTED"
        status = await fake_store.get_intent_status(intent.intent_id)
        assert status == "REJECTED"

    async def test_validation_failure_rejects(self, orchestrator, quote_fetcher, sample_registry, fake_binance):
        """Insuffient balance during validation should reject."""
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        fake_binance.set_balance("USDT", 10.0)  # way too little for $500 order

        intent = make_intent(total_notional_usd=500.0, split={"binance": 1.0})
        result = await orchestrator.submit(intent)

        assert result["status"] == "REJECTED"
        assert "Validation failed" in result["reason"]

    async def test_reconciler_triggered_on_partial(self, orchestrator, quote_fetcher, sample_registry, fake_binance):
        """When one leg fails execution, reconciler should be triggered and produce ROLLED_BACK."""
        inst_binance = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        inst_hl = sample_registry.find_one(base="BTC", venue="hyperliquid", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst_binance, make_quote(inst_binance, mid=50000.0))
        quote_fetcher.set_quote(inst_hl, make_quote(inst_hl, mid=50100.0))

        # Make binance's create_order fail
        fake_binance.set_fail_create(True, message="network error")

        intent = make_intent(total_notional_usd=1000.0)
        result = await orchestrator.submit(intent)

        # One leg fails → executor returns PARTIAL_FILLED → reconciler runs
        assert result["status"] in ("ROLLED_BACK", "ROLLED_BACK_FAILED")
        assert "reconciliation" in result

    async def test_full_pipeline_with_sell_side(self, orchestrator, quote_fetcher, sample_registry):
        """E2E with sell side."""
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        q = make_quote(inst, mid=50000.0)
        # For sell, bids matter
        q._bids = [(49900.0, 10.0), (49800.0, 20.0)]
        quote_fetcher.set_quote(inst, q)

        intent = make_intent(side="sell", total_notional_usd=500.0, split={"binance": 1.0})
        result = await orchestrator.submit(intent)

        assert result["status"] == "ALL_FILLED"
        assert result["legs"][0]["status"] == "FILLED"

    async def test_dry_run_rejected_venues_in_output(self, orchestrator, quote_fetcher, sample_registry):
        """Dry run should show which venues were rejected and why."""
        intent = make_intent(
            base="BTC",
            total_notional_usd=1000.0,
            split={"binance": 0.5, "nonexistent": 0.5},
        )
        # Only binance has instruments
        inst = sample_registry.find_one(base="BTC", venue="binance", market_type="spot", quote_preference=["USDT"])
        quote_fetcher.set_quote(inst, make_quote(inst, mid=50000.0))

        result = await orchestrator.submit(intent, dry_run=True)

        assert result["status"] == "DRY_RUN"
        assert len(result["plan"]["rejected_venues"]) == 1
        assert result["plan"]["rejected_venues"][0]["venue"] == "nonexistent"
