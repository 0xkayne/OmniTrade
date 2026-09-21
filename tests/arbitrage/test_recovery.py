"""Restart reconciliation uses saved identities and never sends new orders."""

import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from src.arbitrage.models import ArbPair
from src.arbitrage.recovery import ArbitrageRecovery
from src.exchange.mock import MockExchange
from src.exchange.order import OrderSnapshot
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.persistence.store import PersistenceStore


@pytest.fixture
async def store(tmp_path):
    result = PersistenceStore(tmp_path / "recovery.db", tmp_path / "audit")
    await result.initialize()
    yield result
    await result.close()


def _pair(network=NetworkType.TESTNET):
    def instrument(venue, symbol):
        return Instrument(venue, network, "perp", Asset("BTC"), Asset("USD"), symbol)

    return ArbPair(
        "BTC", "perp", "arcus", "BTC-USD", "hyperliquid", "BTC/USD:USDC",
        instrument_a=instrument("arcus", "BTC-USD"),
        instrument_b=instrument("hyperliquid", "BTC/USD:USDC"),
    )


async def _persist_cycle(store, *, context=True, mode="testnet", network=NetworkType.TESTNET):
    pair = _pair(network)
    fields = {
        "cycle_id": "recover-me", "base": "BTC", "market_type": "perp", "direction": "buy_a_sell_b",
        "venue_buy": pair.venue_a, "venue_sell": pair.venue_b, "symbol_buy": pair.symbol_a,
        "symbol_sell": pair.symbol_b, "target_qty_base": 1, "status": "OPENING",
    }
    if context:
        fields["execution_context_json"] = json.dumps(
            {"execution_mode": mode, "pair": asdict(pair)}, default=lambda value: value.value,
        )
    await store.create_arbitrage_cycle(**fields)
    for role in ("a", "b"):
        await store.create_arbitrage_cycle_leg(
            leg_id=f"open-{role}", cycle_id="recover-me", role=f"{role}:0",
            venue=getattr(pair, f"venue_{role}"), symbol=getattr(pair, f"symbol_{role}"),
            side="buy" if role == "a" else "sell", target_qty_base=1,
            client_order_id=f"client-{role}", venue_order_id="server-a" if role == "a" else None,
            status="UNKNOWN",
        )


def _exchanges(*, quantity_b=1.0, status_b="closed"):
    exchanges = {venue: MockExchange(venue) for venue in ("arcus", "hyperliquid")}
    for venue, role, quantity, status in (
        ("arcus", "a", 1.0, "closed"), ("hyperliquid", "b", quantity_b, status_b),
    ):
        exchanges[venue].fetch_order_snapshot = AsyncMock(return_value=OrderSnapshot(
            f"server-{role}", status, quantity, 100.0,
            fills=[{"id": f"trade-{role}", "amount": quantity, "price": 100.0, "timestamp": None}]
            if quantity else [],
        ))
        exchanges[venue].submit_order = AsyncMock(side_effect=AssertionError("must not submit"))
        exchanges[venue].cancel_order = AsyncMock(side_effect=AssertionError("must not cancel"))
    return exchanges


def _recovery(store, **kwargs):
    return ArbitrageRecovery(store, execution_mode="testnet", testnet_confirmed=True, **kwargs)


@pytest.mark.asyncio
async def test_recovery_reconstructs_open_cycle_and_deduplicates_actual_fills(store):
    await _persist_cycle(store)
    exchanges = _exchanges()
    recovery = _recovery(store)
    first = (await recovery.recover(exchanges))[0]
    second = (await recovery.recover(exchanges))[0]

    assert first.status == second.status == "OPEN"
    assert first.cycle.filled_qty_a == first.cycle.filled_qty_b == 1.0
    assert first.net_qty_base == 0
    assert len(await store.get_arbitrage_fills("recover-me")) == 2
    assert (await store.get_arbitrage_cycle("recover-me"))["opened_qty_base"] == 1
    assert (await store.get_arbitrage_cycle_leg("open-b"))["venue_order_id"] == "server-b"
    arcus_call = exchanges["arcus"].fetch_order_snapshot.call_args_list[0].args
    hyperliquid_call = exchanges["hyperliquid"].fetch_order_snapshot.call_args_list[0].args
    assert arcus_call[2] == "server-a"
    assert hyperliquid_call[2] is None
    assert hyperliquid_call[0].client_order_id == "client-b"
    for exchange in exchanges.values():
        exchange.submit_order.assert_not_called()
        exchange.cancel_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity,status", [(0.5, "canceled"), (1.0, "open")])
async def test_recovery_retains_partial_or_active_orders(store, quantity, status):
    await _persist_cycle(store)
    result = (await _recovery(store).recover(_exchanges(quantity_b=quantity, status_b=status)))[0]

    assert result.status == "RECOVERY"
    assert result.net_qty_base == pytest.approx(1 - quantity)


@pytest.mark.asyncio
async def test_recovery_subtracts_close_orders_and_marks_flat_cycle_closed(store):
    await _persist_cycle(store)
    pair = _pair()
    exchanges = _exchanges()
    for role, venue in (("a", "arcus"), ("b", "hyperliquid")):
        await store.create_arbitrage_cycle_leg(
            leg_id=f"close-{role}", cycle_id="recover-me", role=f"{role}:close:0",
            venue=venue, symbol=getattr(pair, f"symbol_{role}"),
            side="sell" if role == "a" else "buy", target_qty_base=1,
            client_order_id=f"close-client-{role}", status="UNKNOWN",
        )
        exchanges[venue].fetch_order_snapshot.side_effect = [
            exchanges[venue].fetch_order_snapshot.return_value,
            OrderSnapshot(f"close-server-{role}", "closed", 1, 101),
        ]
    result = (await _recovery(store).recover(exchanges))[0]

    assert result.status == "CLOSED"
    assert result.net_qty_base == 0
    assert result.cycle.filled_qty_a == result.cycle.filled_qty_b == 0
    saved = await store.get_arbitrage_cycle("recover-me")
    assert saved["opened_qty_base"] == saved["closed_qty_base"] == 1
    assert exchanges["arcus"].fetch_order_snapshot.call_args.args[0].is_reduce_only


@pytest.mark.asyncio
@pytest.mark.parametrize("context,network", [(False, NetworkType.TESTNET), (True, NetworkType.MAINNET)])
async def test_recovery_requires_saved_matching_context_before_queries(store, context, network):
    await _persist_cycle(store, context=context, network=network)
    exchanges = _exchanges()
    result = (await _recovery(store).recover(exchanges))[0]

    assert result.status == "MANUAL_REVIEW"
    for exchange in exchanges.values():
        exchange.fetch_order_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_unknown_query_never_resubmits(store):
    await _persist_cycle(store)
    exchanges = _exchanges()
    exchanges["arcus"].fetch_order_snapshot.side_effect = TimeoutError("unconfirmed")
    result = (await _recovery(store).recover(exchanges))[0]

    assert result.status == "MANUAL_REVIEW"
    assert "unconfirmed" in result.reason
    assert (await store.get_arbitrage_cycle_leg("open-b"))["status"] == "CLOSED"
    for exchange in exchanges.values():
        exchange.submit_order.assert_not_called()
        exchange.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_rejects_regressed_cumulative_fills(store):
    await _persist_cycle(store)
    await store.update_arbitrage_cycle_leg("open-b", filled_qty_base=1)
    result = (await _recovery(store).recover(_exchanges(quantity_b=0.5)))[0]

    assert result.status == "MANUAL_REVIEW"
    assert "regressed" in result.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,confirmed", [("testnet", False), ("mainnet", True)])
async def test_recovery_network_gate_runs_before_queries(store, mode, confirmed):
    exchanges = _exchanges()
    recovery = ArbitrageRecovery(store, execution_mode=mode, testnet_confirmed=confirmed)

    with pytest.raises(RuntimeError):
        await recovery.recover(exchanges)
    for exchange in exchanges.values():
        exchange.fetch_order_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_context_migration_preserves_legacy_cycle(store, tmp_path):
    await _persist_cycle(store, context=False)
    await store.close()
    async with aiosqlite.connect(tmp_path / "recovery.db") as old:
        await old.execute("ALTER TABLE arbitrage_cycles DROP COLUMN execution_context_json")
        await old.commit()
    await store.initialize()

    saved = await store.get_arbitrage_cycle("recover-me")
    assert saved["execution_context_json"] is None
    assert saved["status"] == "OPENING"
    assert len(await store.get_arbitrage_cycle_legs("recover-me")) == 2


@pytest.mark.asyncio
async def test_recovery_keeps_real_fills_when_order_total_is_unknown(store):
    await _persist_cycle(store)
    exchanges = _exchanges()
    exchanges["arcus"].fetch_order_snapshot.return_value = OrderSnapshot(
        "server-a", "unknown", None, None,
        fills=[{"id": "known-trade", "amount": 0.25, "price": 100, "timestamp": None}],
    )
    result = (await _recovery(store).recover(exchanges))[0]

    assert result.status == "MANUAL_REVIEW"
    assert result.net_qty_base is None
    fills = await store.get_arbitrage_fills("recover-me", "open-a")
    assert fills[0]["trade_id"] == "known-trade"
    assert fills[0]["quantity"] == 0.25
