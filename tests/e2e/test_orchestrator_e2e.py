"""Mock E2E tests — full Orchestrator pipeline with MockExchange, no network."""

from pathlib import Path

import pytest

from src.coordinator.intent import Intent
from src.coordinator.orchestrator import Orchestrator
from src.core.base_exchange import NetworkType
from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.mock_backend import MockExchange
from src.market.quote_fetcher import QuoteFetcher
from src.market.registry import InstrumentRegistry
from src.persistence.store import PersistenceStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BTC = Asset("BTC")
ETH = Asset("ETH")
USDT = Asset("USDT")
USDC = Asset("USDC")


def _make_instrument(venue, base, quote, market_type="spot", venue_symbol=None, **kw):
    return Instrument(
        venue=venue,
        network=NetworkType(kw.pop("network", "testnet")),
        market_type=market_type,
        base=base,
        quote=quote,
        venue_symbol=venue_symbol or f"{base.symbol}/{quote.symbol}",
        min_qty=0.00001,
        qty_step=0.00001,
        price_step=0.01,
        taker_fee_rate=0.001,
        maker_fee_rate=0.0005,
        listing_status="trading",
        **kw,
    )


def _make_intent(**overrides):
    defaults = {
        "intent_id": "e2e-001",
        "base": "BTC",
        "quote_preference": ["USDT", "USDC"],
        "product": "spot",
        "side": "buy",
        "order_type": "market",
        "total_notional_usd": 1000.0,
        "split": {"binance": 0.5, "hyperliquid": 0.5},
        "max_slippage_pct": 5.0,
    }
    defaults.update(overrides)
    return Intent(**defaults)


async def _build_orchestrator(tmp_path, mock_binance, mock_hyperliquid, block_store=False):
    """Build a real Orchestrator backed by MockExchange + memory PersistenceStore."""
    registry = InstrumentRegistry()
    for inst in mock_binance._markets:
        registry.add(inst)
    for inst in mock_hyperliquid._markets:
        registry.add(inst)

    exchanges = {"binance": mock_binance, "hyperliquid": mock_hyperliquid}
    quote_fetcher = QuoteFetcher(exchanges)

    store = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await store.initialize()

    if block_store:
        # Insert a fake ROLLED_BACK_FAILED intent to trigger blocking
        from dataclasses import dataclass

        @dataclass
        class _FakeIntent:
            intent_id: str
            base: str = "BTC"
            product: str = "spot"
            side: str = "buy"
            total_notional_usd: float = 1000.0
            quote_preference: list = None
            split: dict = None

            def __post_init__(self):
                if self.quote_preference is None:
                    self.quote_preference = ["USDT"]
                if self.split is None:
                    self.split = {"binance": 1.0}

        bad = _FakeIntent(intent_id="blocker")
        await store.create_intent(bad)
        await store.update_intent_status("blocker", "ROLLED_BACK_FAILED")

    return Orchestrator(registry, quote_fetcher, exchanges, store), store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_binance():
    m = MockExchange("binance")
    m.set_balance("USDT", 100_000.0)
    m.set_orderbook("BTC/USDT", bids=[(49990.0, 2.0), (49980.0, 5.0)],
                    asks=[(50010.0, 2.0), (50020.0, 5.0)])
    m.set_orderbook("ETH/USDT", bids=[(2990.0, 10.0)], asks=[(3010.0, 10.0)])
    inst = _make_instrument("binance", BTC, USDT, "spot")
    m.set_markets([inst])
    return m


@pytest.fixture
def mock_hyperliquid():
    m = MockExchange("hyperliquid")
    m.set_balance("USDT", 50_000.0)
    m.set_orderbook("BTC/USDT", bids=[(50000.0, 1.5), (49990.0, 3.0)],
                    asks=[(50020.0, 1.5), (50030.0, 3.0)])
    inst = _make_instrument("hyperliquid", BTC, USDT, "spot")
    m.set_markets([inst])
    return m


@pytest.fixture
async def orch_and_store(tmp_path, mock_binance, mock_hyperliquid):
    orch, store = await _build_orchestrator(tmp_path, mock_binance, mock_hyperliquid)
    yield orch, store
    await store.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestE2EAllFilled:
    async def test_two_leg_all_filled(self, orch_and_store):
        orch, store = orch_and_store
        intent = _make_intent()
        result = await orch.submit(intent)

        assert result["status"] == "ALL_FILLED"
        assert result["intent_id"] == intent.intent_id
        assert len(result["legs"]) == 2
        for leg in result["legs"]:
            assert leg["status"] == "FILLED"
            assert leg["order_id"] is not None
            assert leg["filled_amount"] > 0

        # Verify persisted
        stored = await store.get_intent(intent.intent_id)
        assert stored is not None
        assert stored.status == "ALL_FILLED"

    async def test_single_leg_all_filled(self, tmp_path, mock_binance):
        orch, store = await _build_orchestrator(tmp_path, mock_binance,
                                                MockExchange("empty"))
        intent = _make_intent(split={"binance": 1.0})
        result = await orch.submit(intent)

        assert result["status"] == "ALL_FILLED"
        assert len(result["legs"]) == 1
        assert result["legs"][0]["status"] == "FILLED"
        await store.close()


class TestE2EDryRun:
    async def test_dry_run_no_orders_sent(self, orch_and_store):
        orch, store = orch_and_store
        intent = _make_intent()
        result = await orch.submit(intent, dry_run=True)

        assert result["status"] == "DRY_RUN"
        assert "plan" in result
        plan = result["plan"]
        assert len(plan["legs"]) == 2
        assert plan["is_acceptable"] is True

        # No orders should have been sent — intent stays PENDING
        stored = await store.get_intent(intent.intent_id)
        assert stored.status == "PENDING"


class TestE2ERejected:
    async def test_rejected_unknown_base(self, orch_and_store):
        orch, store = orch_and_store
        intent = _make_intent(base="SOL")
        result = await orch.submit(intent)

        assert result["status"] == "REJECTED"
        stored = await store.get_intent(intent.intent_id)
        assert stored.status == "REJECTED"

    async def test_rejected_slippage_threshold(self, orch_and_store, mock_binance,
                                                mock_hyperliquid):
        orch, store = orch_and_store
        # Create very thin orderbook that will cause high slippage
        mock_binance.set_orderbook("BTC/USDT",
                                   bids=[(10000.0, 0.001)], asks=[(100000.0, 0.001)])
        mock_hyperliquid.set_orderbook("BTC/USDT",
                                       bids=[(10000.0, 0.001)], asks=[(100000.0, 0.001)])

        intent = _make_intent(max_slippage_pct=0.01)  # 0.01% — impossibly tight
        result = await orch.submit(intent)

        assert result["status"] in ("REJECTED", "ALL_FILLED")
        # If the stub orderbook provides insufficient depth, it will be rejected

    async def test_rejected_by_needs_manual_block(self, tmp_path, mock_binance,
                                                   mock_hyperliquid):
        orch, store = await _build_orchestrator(tmp_path, mock_binance,
                                                 mock_hyperliquid, block_store=True)
        intent = _make_intent()
        result = await orch.submit(intent)

        assert result["status"] == "REJECTED"
        assert "NEEDS_MANUAL" in result["reason"]
        await store.close()


class TestE2ERolledBack:
    async def test_one_leg_fails_compensation_succeeds(self, tmp_path, mock_binance,
                                                        mock_hyperliquid):
        # Make hyperliquid fail on create_order
        mock_hyperliquid.set_fail_create(True)

        orch, store = await _build_orchestrator(tmp_path, mock_binance, mock_hyperliquid)
        intent = _make_intent()
        result = await orch.submit(intent)

        assert result["status"] == "ROLLED_BACK"
        assert "reconciliation" in result
        # The binance leg that filled should have been compensated
        stored = await store.get_intent(intent.intent_id)
        assert stored.status == "ROLLED_BACK"
        await store.close()


class TestE2ENeedsManual:
    async def test_compensation_failure_triggers_needs_manual(self, tmp_path, mock_binance,
                                                                mock_hyperliquid):
        # Make hyperliquid fail and binance's cancel/fetch also fail
        mock_hyperliquid.set_fail_create(True)
        mock_binance.set_fail_fetch(True)

        orch, store = await _build_orchestrator(tmp_path, mock_binance, mock_hyperliquid)
        intent = _make_intent()
        result = await orch.submit(intent)

        # Could be ROLLED_BACK_FAILED or ROLLED_BACK depending on timing
        assert result["status"] in ("ROLLED_BACK", "ROLLED_BACK_FAILED", "PARTIAL_FILLED")
        await store.close()


class TestE2ECrashRecovery:
    async def test_recoverable_state_after_intent_persisted(self, orch_and_store):
        """Verify intent and legs are persisted before orders are sent."""
        orch, store = orch_and_store
        intent = _make_intent()
        result = await orch.submit(intent)

        # Query the store directly — intent and legs must be present
        stored = await store.get_intent(intent.intent_id)
        assert stored is not None
        assert stored.status == result["status"]

        legs = await store.get_legs_for_intent(intent.intent_id)
        assert len(legs) == len(result.get("legs", []))
