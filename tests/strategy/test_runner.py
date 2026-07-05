"""Tests for AutoArbRunner — mock-exchange based integration tests."""

from pathlib import Path

import pytest

from src.market.mock_backend import MockExchange
from src.market.pair_matcher import CrossVenuePair, PairMatcher
from src.persistence.store import PersistenceStore
from src.strategy.funding_arb.comparator import FundingRateComparator, FundingSpread
from src.strategy.funding_arb.monitor import FundingRateMonitor
from src.strategy.funding_arb.position_manager import HedgedPositionManager
from src.strategy.funding_arb.runner import ArbConfig, AutoArbRunner, _find_spread
from tests.coordinator.conftest import make_btc_usdt_perp


async def _async_noop(*_args, **_kwargs):
    return None


@pytest.fixture
async def fake_store(tmp_path):
    store = PersistenceStore(Path(":memory:"), tmp_path / "jsonl")
    await store.initialize()
    yield store
    await store.close()


def make_mock_pair(base="BTC", venue_a="binance", venue_b="hyperliquid"):
    inst_a = make_btc_usdt_perp(venue_a)
    inst_b = make_btc_usdt_perp(venue_b)
    return CrossVenuePair(
        base=base,
        venue_a=venue_a,
        venue_b=venue_b,
        instrument_a=inst_a,
        instrument_b=inst_b,
    )


class MockRegistry:
    def __init__(self, instruments):
        self._instruments = instruments

    def list_instruments(self, base=None, market_type=None, venue=None):
        result = self._instruments
        if base:
            result = [i for i in result if i.base.symbol == base]
        if market_type:
            result = [i for i in result if i.market_type == market_type]
        if venue:
            result = [i for i in result if i.venue == venue]
        return result


class TestAutoArbRunner:
    @pytest.mark.asyncio
    async def test_tick_with_spreads_finds_signals(self, fake_store):
        """A scan that produces signals should trigger _should_open checks."""
        inst_a = make_btc_usdt_perp("binance")
        inst_b = make_btc_usdt_perp("hyperliquid")
        pairs = [
            CrossVenuePair(
                base="BTC",
                venue_a="binance",
                venue_b="hyperliquid",
                instrument_a=inst_a,
                instrument_b=inst_b,
            )
        ]
        registry = MockRegistry([inst_a, inst_b])

        exchanges = {"binance": MockExchange("binance"), "hyperliquid": MockExchange("hyperliquid")}
        cache = type(
            "FakeCache",
            (),
            {
                "refresh": _async_noop,
                "get": lambda self, v, s: {
                    "funding_rate": 0.0005 if v == "hyperliquid" else -0.0003,
                    "next_funding_time": None,
                },
                "all_rates": lambda self: [],
                "close": lambda self: None,
            },
        )()
        pm = HedgedPositionManager(fake_store)
        comparator = FundingRateComparator(min_spread_pct=0.0)
        monitor = FundingRateMonitor(registry, cache, PairMatcher(registry), comparator, pm, fake_store)

        intents_sent = []

        async def fake_submit(intent):
            intents_sent.append(intent)
            return {"status": "ALL_FILLED", "intent_id": "test-id", "legs": [{"leg_id": "leg-1"}, {"leg_id": "leg-2"}]}

        config = ArbConfig(dry_run=False, min_spread_pct=0.0, notional_per_leg=500)
        runner = AutoArbRunner(monitor, pm, fake_submit, config)

        # Run one tick
        await runner._tick()

        # Should have opened one position for BTC
        assert len(intents_sent) == 1
        intent = intents_sent[0]
        assert intent.base == "BTC"
        assert len(intent.split) == 2

    @pytest.mark.asyncio
    async def test_dry_run_never_submits(self, fake_store):
        """In dry-run mode, no intents are submitted."""
        inst_a = make_btc_usdt_perp("binance")
        inst_b = make_btc_usdt_perp("hyperliquid")
        pairs = [
            CrossVenuePair(
                base="BTC",
                venue_a="binance",
                venue_b="hyperliquid",
                instrument_a=inst_a,
                instrument_b=inst_b,
            )
        ]
        registry = MockRegistry([inst_a, inst_b])
        cache = type(
            "FakeCache",
            (),
            {
                "refresh": _async_noop,
                "get": lambda self, v, s: {
                    "funding_rate": 0.0005 if v == "hyperliquid" else -0.0003,
                    "next_funding_time": None,
                },
                "all_rates": lambda self: [],
                "close": lambda self: None,
            },
        )()
        pm = HedgedPositionManager(fake_store)
        comparator = FundingRateComparator(min_spread_pct=0.0)
        monitor = FundingRateMonitor(registry, cache, PairMatcher(registry), comparator, pm, fake_store)

        intents_sent = []

        async def fake_submit(intent):
            intents_sent.append(intent)
            return {}

        config = ArbConfig(dry_run=True)
        runner = AutoArbRunner(monitor, pm, fake_submit, config)

        await runner._tick()
        assert len(intents_sent) == 0  # dry-run blocks all orders

    @pytest.mark.asyncio
    async def test_should_close_when_spread_narrows(self, fake_store):
        """When spread falls below exit threshold, position is closed."""
        inst_a = make_btc_usdt_perp("binance")
        inst_b = make_btc_usdt_perp("hyperliquid")
        pair = CrossVenuePair(
            base="BTC",
            venue_a="binance",
            venue_b="hyperliquid",
            instrument_a=inst_a,
            instrument_b=inst_b,
        )
        registry = MockRegistry([inst_a, inst_b])

        # Record an existing open position
        await fake_store.create_hedged_position(
            position_id="hp-test",
            base="BTC",
            venue_long="binance",
            venue_short="hyperliquid",
            notional_usd=500,
            intent_open="intent-1",
            leg_long_id="leg-1",
            leg_short_id="leg-2",
            rate_a=-0.0003,
            rate_b=0.0005,
        )

        # Cache returns narrowed spread
        cache = type(
            "FakeCache",
            (),
            {
                "refresh": _async_noop,
                "get": lambda self, v, s: {
                    "funding_rate": 0.00001 if v == "hyperliquid" else -0.00001,
                    "next_funding_time": None,
                },
                "all_rates": lambda self: [],
                "close": lambda self: None,
            },
        )()
        pm = HedgedPositionManager(fake_store)
        comparator = FundingRateComparator(min_spread_pct=0.0)
        monitor = FundingRateMonitor(registry, cache, PairMatcher(registry), comparator, pm, fake_store)

        intents_sent = []

        async def fake_submit(intent):
            intents_sent.append(intent)
            return {"status": "ALL_FILLED", "intent_id": "close-id", "legs": [{"leg_id": "l1"}, {"leg_id": "l2"}]}

        config = ArbConfig(dry_run=False, exit_spread_pct=0.1, notional_per_leg=500)
        runner = AutoArbRunner(monitor, pm, fake_submit, config)

        await runner._tick()

        # Should have triggered close (spread ~0.002% < exit 0.1%)
        close_intents = [i for i in intents_sent if i.base == "BTC"]
        assert len(close_intents) >= 1


class TestFindSpread:
    def test_matches_by_base(self):
        pair = make_mock_pair("BTC")
        from src.strategy.funding_arb.comparator import FundingSpread

        s = FundingSpread(
            pair=pair,
            rate_a=-0.0001,
            rate_b=0.0003,
            spread=0.0004,
            spread_pct_annual=10.0,
            next_funding_a=None,
            next_funding_b=None,
            signal="open_long_a_short_b",
        )
        pos = type("Pos", (), {"pair": pair})()  # minimal fake
        assert _find_spread(pos, [s]) == s

    def test_no_match_returns_none(self):
        pair = make_mock_pair("BTC")
        pos = type("Pos", (), {"pair": pair})()
        assert _find_spread(pos, []) is None
