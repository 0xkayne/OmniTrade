"""Real testnet E2E tests — requires funded Binance demo + Hyperliquid testnet accounts.

All tests marked @pytest.mark.network and @pytest.mark.slow.
Credentials loaded from config/exchanges.yaml and config/secrets.yaml.
"""

import asyncio

import pytest
import yaml

from src.cli.bootstrap import build_orchestrator
from src.coordinator.intent import Intent
from src.core.base_exchange import NetworkType
from src.core.exchange_factory import ExchangeFactory

pytestmark = [pytest.mark.network, pytest.mark.slow]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_configs():
    with open("config/exchanges.yaml") as f:
        config = yaml.safe_load(f)["exchanges"]
    with open("config/secrets.yaml") as f:
        secrets = yaml.safe_load(f)
    return config, secrets


async def _build_test_orchestrator():
    """Build an Orchestrator wired to real testnet exchanges."""
    config, secrets = _load_configs()
    exchanges = await ExchangeFactory.initialize_exchanges(
        config, secrets, target_network=NetworkType.TESTNET,
    )
    orch = await build_orchestrator(_exchanges=exchanges)
    return orch, exchanges


def _make_net_intent(overrides=None):
    defaults = {
        "intent_id": "net-e2e-001",
        "base": "BTC",
        "quote_preference": ["USDT", "USDC"],
        "product": "spot",
        "side": "buy",
        "order_type": "market",
        "total_notional_usd": 10.0,
        "split": {"binance": 0.5, "hyperliquid": 0.5},
        "max_slippage_pct": 5.0,
        "execute_timeout_seconds": 30,
    }
    if overrides:
        defaults.update(overrides)
    return Intent(**defaults)


# ---------------------------------------------------------------------------
# Group A: Dry run (no orders sent — always safe)
# ---------------------------------------------------------------------------


class TestDryRun:
    async def test_dry_run_binance_btc_spot(self):
        """Dry run BTC spot on Binance demo — Plan only, no orders."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            intent = _make_net_intent({"intent_id": "dry-001", "split": {"binance": 1.0}})
            result = await orch.submit(intent, dry_run=True)

            assert result["status"] == "DRY_RUN"
            assert "plan" in result
            plan = result["plan"]
            assert len(plan["legs"]) == 1
            assert plan["legs"][0]["venue"] == "binance"
        finally:
            for ex in exchanges.values():
                await ex.close()

    async def test_dry_run_hyperliquid_btc_perp(self):
        """Dry run BTC perp on Hyperliquid testnet."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            intent = _make_net_intent({
                "intent_id": "dry-002",
                "product": "perp",
                "split": {"hyperliquid": 1.0},
            })
            result = await orch.submit(intent, dry_run=True)

            assert result["status"] == "DRY_RUN"
            assert "plan" in result
            assert result["plan"]["legs"][0]["venue"] == "hyperliquid"
        finally:
            for ex in exchanges.values():
                await ex.close()

    async def test_dry_run_multi_venue_spot(self):
        """Dry run BTC spot across both Binance and Hyperliquid."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            intent = _make_net_intent({"intent_id": "dry-003"})
            result = await orch.submit(intent, dry_run=True)

            assert result["status"] == "DRY_RUN"
            plan = result["plan"]
            assert len(plan["legs"]) == 2
            venues = {leg["venue"] for leg in plan["legs"]}
            assert venues == {"binance", "hyperliquid"}
        finally:
            for ex in exchanges.values():
                await ex.close()

    async def test_dry_run_rejected_unknown_base(self):
        """Unknown base asset should be rejected at plan stage."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            intent = _make_net_intent({
                "intent_id": "dry-004",
                "base": "NOCOIN12345",
                "split": {"binance": 1.0},
            })
            result = await orch.submit(intent, dry_run=True)

            assert result["status"] in ("DRY_RUN", "REJECTED")
            if result["status"] == "DRY_RUN":
                assert result["plan"]["is_acceptable"] is False
        finally:
            for ex in exchanges.values():
                await ex.close()


# ---------------------------------------------------------------------------
# Group B: Small real orders ($10-15 max per test)
# ---------------------------------------------------------------------------


class TestSmallOrders:
    async def test_small_buy_binance_only(self):
        """$10 BTC spot buy on Binance demo."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            intent = _make_net_intent({
                "intent_id": f"real-{id(self)}",
                "total_notional_usd": 10.0,
                "split": {"binance": 1.0},
            })
            result = await orch.submit(intent)

            assert result["status"] in ("ALL_FILLED", "REJECTED", "ROLLED_BACK")
            if result["status"] == "REJECTED":
                reason = result.get("reason", "")
                assert "balance" in reason.lower() or "not acceptable" in reason.lower()
        finally:
            for ex in exchanges.values():
                await ex.close()

    async def test_small_buy_hyperliquid_only(self):
        """$10 BTC perp buy on Hyperliquid testnet."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            intent = _make_net_intent({
                "intent_id": f"real-hl-{id(self)}",
                "product": "perp",
                "total_notional_usd": 10.0,
                "split": {"hyperliquid": 1.0},
            })
            result = await orch.submit(intent)

            assert result["status"] in ("ALL_FILLED", "REJECTED", "ROLLED_BACK")
        finally:
            for ex in exchanges.values():
                await ex.close()

    async def test_small_split_order(self):
        """$10 split across Binance spot + Hyperliquid perp."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            intent = _make_net_intent({
                "intent_id": f"real-split-{id(self)}",
                "product": "perp",
                "total_notional_usd": 10.0,
                "split": {"binance": 0.5, "hyperliquid": 0.5},
            })
            result = await orch.submit(intent)

            assert result["status"] in ("ALL_FILLED", "REJECTED", "ROLLED_BACK", "ROLLED_BACK_FAILED")
            # Check that legs were persisted
            stored = orch._store
            stored_intent = await stored.get_intent(intent.intent_id)
            assert stored_intent is not None
            assert stored_intent.status == result["status"]
        finally:
            for ex in exchanges.values():
                await ex.close()


# ---------------------------------------------------------------------------
# Group C: Edge cases (dry run, no orders)
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_onefill_venues_instruments_loaded(self):
        """Verify that instruments are actually loaded from testnet exchanges."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            # After bootstrap, the registry should have real instruments
            reg = orch._registry
            binance_btc = reg.find_one(
                base="BTC", venue="binance", market_type="spot",
                quote_preference=["USDT"],
            )
            hl_btc = reg.find_one(
                base="BTC", venue="hyperliquid", market_type="perp",
                quote_preference=["USDC", "USDT"],
            )

            assert binance_btc is not None, "Binance BTC/USDT spot instrument not found"
            assert hl_btc is not None, f"Hyperliquid BTC perp instrument not found (loaded {reg.instrument_count} instruments)"
            assert binance_btc.venue_symbol is not None
            assert hl_btc.venue_symbol is not None
        finally:
            for ex in exchanges.values():
                await ex.close()

    async def test_invalid_split_rejected_in_plan(self):
        """Split that doesn't sum to 1.0 should be rejected."""
        orch, exchanges = await _build_test_orchestrator()
        try:
            with pytest.raises(ValueError):
                _make_net_intent({"split": {"binance": 0.3, "hyperliquid": 0.3}})
        finally:
            for ex in exchanges.values():
                await ex.close()
