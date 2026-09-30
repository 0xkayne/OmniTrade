"""Durable order identity and ambiguous-response recovery using real SQLite."""

import asyncio
import json
import time
from dataclasses import asdict

import pytest

from src.coordinator.leg_orders import LegOrderManager
from src.exchange.order import OrderRequest, OrderSnapshot
from tests.coordinator.conftest import make_btc_usdt_spot, make_intent


@pytest.fixture
async def order_context(fake_store, fake_binance):
    intent = make_intent(split={"binance": 1.0})
    await fake_store.create_intent(intent)
    instrument = make_btc_usdt_spot()
    leg_id = await fake_store.create_leg(
        intent_id=intent.intent_id,
        venue="binance",
        instrument_venue_symbol=instrument.venue_symbol,
        instrument_base="BTC",
        instrument_quote="USDT",
        instrument_market_type="spot",
    )
    manager = LegOrderManager(fake_binance, fake_store, instrument, use_websocket=False)
    request = OrderRequest(
        instrument.venue_symbol,
        "buy",
        0.01,
        "limit",
        50250,
        manager.client_order_id(leg_id, "original", 0),
        "spot",
        "IOC",
    )
    return manager, request, leg_id, intent


@pytest.mark.asyncio
async def test_accepted_but_response_lost_is_queried_not_resent(order_context, fake_binance, fake_store):
    manager, request, leg_id, intent = order_context
    original = fake_binance.create_order

    async def lost_response(*args, **kwargs):
        row = await fake_store.get_order_row(request.client_order_id)
        assert row.status == "UNKNOWN"
        assert await fake_store.get_leg(leg_id) is not None
        await original(*args, **kwargs)
        raise TimeoutError("response lost after matching engine accepted")

    fake_binance.create_order = lost_response
    snapshot = await manager.execute(request, leg_id, intent.intent_id, "original", time.monotonic() + 1)
    assert snapshot.filled_qty_base == 0.01
    assert snapshot.status == "closed"
    repeated = await manager.execute(request, leg_id, intent.intent_id, "original", time.monotonic() + 1)
    assert repeated == snapshot
    assert len(fake_binance.create_order_calls) == 1


@pytest.mark.asyncio
async def test_unknown_order_is_not_treated_as_rejected(order_context, fake_binance):
    manager, request, leg_id, intent = order_context
    fake_binance.inject_order_error(request.symbol, TimeoutError("ambiguous"))
    snapshot = await manager.execute(request, leg_id, intent.intent_id, "original", time.monotonic() + 1)
    assert snapshot.status == "unknown"
    assert snapshot.filled_qty_base is None
    assert len(fake_binance.create_order_calls) == 1


@pytest.mark.asyncio
async def test_closed_without_quantity_is_not_fabricated(order_context, fake_binance):
    manager, request, leg_id, intent = order_context
    fake_binance.inject_next_order_result(request.symbol, {"id": "missing-qty", "status": "closed"})
    snapshot = await manager.execute(request, leg_id, intent.intent_id, "original", time.monotonic() + 1)
    assert snapshot.status == "unknown"
    assert snapshot.filled_qty_base is None


@pytest.mark.asyncio
async def test_cancellation_race_retains_final_partial_fill(order_context, fake_binance, fake_store):
    manager, request, leg_id, intent = order_context
    order = await fake_binance.create_order(request.symbol, "limit", "buy", request.amount, request.price)
    await fake_store.create_order_row(
        request.client_order_id, leg_id, intent.intent_id, "original", json.dumps(asdict(request))
    )
    await fake_store.update_order_row(
        request.client_order_id, "open", json.dumps(asdict(OrderSnapshot(order["id"], "open", 0, None)))
    )
    original_cancel = fake_binance.cancel_order

    async def cancel_after_fill(*args, **kwargs):
        fake_binance._orders[order["id"]].update(filled=0.004, average=50000)
        return await original_cancel(*args, **kwargs)

    fake_binance.cancel_order = cancel_after_fill
    snapshot = await manager.cancel(await fake_store.get_order_row(request.client_order_id), time.monotonic() + 1)
    assert snapshot.status == "canceled"
    assert snapshot.filled_qty_base == 0.004


@pytest.mark.asyncio
async def test_hanging_send_is_bounded(order_context, fake_binance):
    manager, request, leg_id, intent = order_context

    async def hanging(*args, **kwargs):
        await asyncio.sleep(60)

    fake_binance.create_order = hanging
    start = time.monotonic()
    snapshot = await manager.execute(request, leg_id, intent.intent_id, "original", start + 1)
    assert time.monotonic() - start < 2
    assert snapshot.status == "unknown"


@pytest.mark.asyncio
async def test_ws_list_filters_unrelated_order(order_context, fake_binance):
    manager, request, leg_id, intent = order_context
    manager.use_websocket = True
    original_watch = fake_binance.watch_orders

    async def updates(*args, **kwargs):
        snapshot = await original_watch(*args, **kwargs)
        return [{"id": "unrelated", "filled": 100, "status": "closed"}, snapshot]

    fake_binance.watch_orders = updates
    snapshot = await manager.execute(request, leg_id, intent.intent_id, "original", time.monotonic() + 1)
    assert snapshot.filled_qty_base == request.amount


@pytest.mark.asyncio
async def test_crash_before_send_marker_does_not_query_or_send(order_context, fake_store, fake_binance):
    manager, request, leg_id, intent = order_context
    await fake_store.create_order_row(
        request.client_order_id, leg_id, intent.intent_id, "original", json.dumps(asdict(request))
    )
    row = await fake_store.get_order_row(request.client_order_id)
    snapshot = await manager.cancel(row, time.monotonic() + 1)
    assert snapshot.status == "rejected"
    assert snapshot.filled_qty_base == 0
    assert not fake_binance.create_order_calls
    assert not fake_binance.fetch_order_calls


@pytest.mark.asyncio
async def test_post_ack_record_error_never_erases_accepted_fill(order_context, fake_binance, monkeypatch):
    from ccxt.base.errors import InvalidOrder

    manager, request, leg_id, intent = order_context
    original_record = manager._record
    failed = False

    async def flaky_record(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise InvalidOrder("post-ACK enrichment failed")
        return await original_record(*args, **kwargs)

    monkeypatch.setattr(manager, "_record", flaky_record)
    snapshot = await manager.execute(request, leg_id, intent.intent_id, "original", time.monotonic() + 2)
    assert snapshot.status == "closed"
    assert snapshot.filled_qty_base == request.amount
    assert len(fake_binance.create_order_calls) == 1
    assert fake_binance.fetch_order_calls


@pytest.mark.asyncio
async def test_expired_quote_during_persistence_is_not_sent(order_context, fake_binance):
    from dataclasses import replace

    manager, request, leg_id, intent = order_context
    request = replace(request, expires_at=time.time() - 1)
    snapshot = await manager.execute(request, leg_id, intent.intent_id, "original", time.monotonic() + 1)
    assert snapshot.status == "rejected"
    assert not fake_binance.create_order_calls
