import asyncio
import time
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.arbitrage.canary import CONFIRMATION_TOKEN, CanaryRequest
from src.arbitrage.canary import TestnetCanary as CanaryRunner
from src.exchange.order import OrderCapabilities, OrderPositionSnapshot, OrderSnapshot
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.persistence.store import PersistenceStore


class CanaryExchange:
    network_type = NetworkType.TESTNET
    supports_user_fills = False

    def __init__(self, name: str, symbol: str, quote: str):
        self.name = name
        self.instrument = Instrument(
            name,
            NetworkType.TESTNET,
            "perp",
            Asset("BTC"),
            Asset(quote),
            symbol,
            qty_step=0.001,
            price_step=0.01,
        )
        self.requests = []
        self.position_qty = Decimal(0)
        self.position_queries = []
        self.open_order_queries = []
        self.books = [{"bids": [[99.9, 1.0]], "asks": [[100.1, 1.0]]}]
        self.book_calls = 0

    async def list_markets(self):
        return [self.instrument]

    def has_credentials(self, _instrument=None):
        return True

    def account_params(self, _instrument):
        return {"type": "future", "subType": "linear"}

    def price_tick(self, _symbol, _price):
        return self.instrument.price_step

    async def fetch_balance(self, params=None):
        return {"free": {self.instrument.quote.symbol: 1000.0}}

    def order_capabilities(self, _instrument):
        return OrderCapabilities(True, ("IOC",), True)

    async def fetch_order_position(self, instrument):
        self.position_queries.append(instrument)
        return OrderPositionSnapshot(
            instrument.venue_symbol, float(self.position_qty), None, None, 1, "cross", time.time()
        )

    async def fetch_open_orders(self, symbol, params=None):
        self.open_order_queries.append((symbol, params))
        return []

    async def fetch_orderbook(self, _symbol, limit=5):
        book = self.books[min(self.book_calls, len(self.books) - 1)]
        self.book_calls += 1
        if isinstance(book, Exception):
            raise book
        return book

    async def submit_order(self, request, _instrument):
        self.requests.append(request)
        self.position_qty += Decimal(str(request.amount)) * (1 if request.side == "buy" else -1)
        return OrderSnapshot(f"{self.name}-{len(self.requests)}", "closed", request.amount, request.price, 0.0)


@pytest.fixture
def exchanges():
    return {
        "arcus": CanaryExchange("arcus", "BTC-USD", "USD"),
        "hyperliquid": CanaryExchange("hyperliquid", "BTC/USDC:USDC", "USDC"),
    }


@pytest.fixture
def canary_request():
    return CanaryRequest("BTC", "arcus", "hyperliquid", 0.001, confirmation=CONFIRMATION_TOKEN)


@pytest.fixture
async def store(tmp_path):
    value = PersistenceStore(tmp_path / "canary.db", tmp_path / "audit")
    await value.initialize()
    yield value
    await value.close()


@pytest.mark.asyncio
async def test_canary_requires_explicit_confirmation(store):
    exchanges = {
        "arcus": CanaryExchange("arcus", "BTC-USD", "USD"),
        "hyperliquid": CanaryExchange("hyperliquid", "BTC/USDC:USDC", "USDC"),
    }
    result = await CanaryRunner(exchanges, store).run(
        CanaryRequest(
            "BTC",
            "arcus",
            "hyperliquid",
            0.001,
        )
    )
    assert result.status == "REJECTED"
    assert CONFIRMATION_TOKEN in (result.error or "")
    assert not exchanges["arcus"].requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("venue_b", "symbol_b", "quote_b"),
    [("hyperliquid", "BTC/USDC:USDC", "USDC"), ("binance", "BTC/USDT:USDT", "USDT")],
)
async def test_canary_opens_and_closes_one_bounded_cycle(store, venue_b, symbol_b, quote_b):
    exchanges = {
        "arcus": CanaryExchange("arcus", "BTC-USD", "USD"),
        venue_b: CanaryExchange(venue_b, symbol_b, quote_b),
    }
    if venue_b == "binance":
        exchanges[venue_b].instrument = replace(exchanges[venue_b].instrument, quantity_unit="contracts")
    result = await CanaryRunner(exchanges, store).run(
        CanaryRequest(
            "BTC",
            "arcus",
            venue_b,
            0.001,
            confirmation=CONFIRMATION_TOKEN,
        )
    )
    assert result.status == "CLOSED"
    assert result.opening is not None and result.opening.status == "OPEN"
    assert result.closing is not None and result.closing.status == "CLOSED"
    assert all(len(exchange.requests) == 2 for exchange in exchanges.values())
    for exchange in exchanges.values():
        assert len(exchange.position_queries) == 2
        assert exchange.open_order_queries == [(exchange.instrument.venue_symbol, exchange.account_params(None))] * 2
        assert exchange.position_qty == 0
        assert exchange.requests[0].is_reduce_only is False
        assert exchange.requests[1].is_reduce_only is True
    assert (await store.get_arbitrage_cycle(result.cycle_id))["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_canary_rejects_notional_limit_before_submission(store):
    exchanges = {
        "arcus": CanaryExchange("arcus", "BTC-USD", "USD"),
        "binance": CanaryExchange("binance", "BTC/USDT:USDT", "USDT"),
    }
    result = await CanaryRunner(exchanges, store).run(
        CanaryRequest(
            "BTC",
            "arcus",
            "binance",
            1.0,
            max_notional_usd=10.0,
            confirmation=CONFIRMATION_TOKEN,
        )
    )
    assert result.status == "REJECTED"
    assert "max_notional_usd" in (result.error or "")
    assert not exchanges["arcus"].requests


@pytest.mark.asyncio
@pytest.mark.parametrize("position", [0.001, -0.001, float("nan"), float("inf")])
async def test_canary_rejects_nonzero_or_invalid_baseline_position(store, exchanges, canary_request, position):
    exchanges["hyperliquid"].position_qty = Decimal(str(position))
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "REJECTED"
    assert "baseline check failed" in result.error
    assert not any(exchange.requests for exchange in exchanges.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fetch_order_position", "fetch_open_orders"])
async def test_canary_requires_successful_baseline_reads(store, exchanges, canary_request, method):
    setattr(exchanges["arcus"], method, AsyncMock(side_effect=NotImplementedError("unsupported account read")))
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "REJECTED"
    assert "arcus:BTC-USD baseline check failed" in result.error
    assert not any(exchange.requests for exchange in exchanges.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("orders", [[{"id": "existing-order"}], None, {}])
async def test_canary_rejects_open_orders_or_unknown_order_response(store, exchanges, canary_request, orders):
    exchanges["arcus"].fetch_open_orders = AsyncMock(return_value=orders)
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "REJECTED"
    assert "empty open-order list" in result.error
    assert not any(exchange.requests for exchange in exchanges.values())


@pytest.mark.asyncio
async def test_canary_rejects_position_for_another_symbol(store, exchanges, canary_request):
    exchanges["arcus"].fetch_order_position = AsyncMock(
        return_value=OrderPositionSnapshot("other", 0, None, None, None, None, time.time())
    )
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "REJECTED"
    assert "verified zero position" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["buy_a_sell_b", "buy_b_sell_a"])
async def test_canary_reprices_close_inside_fresh_half_percent_band(store, exchanges, canary_request, direction):
    for exchange in exchanges.values():
        exchange.books.append({"bids": [[109.9, 1.0]], "asks": [[110.1, 1.0]]})
    result = await CanaryRunner(exchanges, store).run(replace(canary_request, direction=direction))
    assert result.status == "CLOSED"
    for exchange in exchanges.values():
        opened, closed = exchange.requests
        assert opened.price == (100.40 if opened.side == "buy" else 99.60)
        assert closed.price == (110.44 if closed.side == "buy" else 109.56)
        assert exchange.book_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instrument_fields", "quantity", "error"),
    [
        ({"qty_step": 0.01}, 0.001, "qty_step"),
        ({"min_qty": 0.01}, 0.001, "min_qty"),
        ({"min_notional": 10.0}, 0.001, "min_notional"),
        ({"price_step": 2.0}, 0.001, "0.5% price protection"),
    ],
)
async def test_canary_rejects_untradeable_precision_or_minimum(
    store, exchanges, canary_request, instrument_fields, quantity, error
):
    exchanges["arcus"].instrument = replace(exchanges["arcus"].instrument, **instrument_fields)
    result = await CanaryRunner(exchanges, store).run(replace(canary_request, quantity_base=quantity))
    assert result.status == "REJECTED"
    assert error in result.error
    assert not any(exchange.requests for exchange in exchanges.values())


@pytest.mark.asyncio
async def test_canary_rejects_spread_outside_midpoint_price_protection(store, exchanges, canary_request):
    exchanges["arcus"].books = [{"bids": [[99.0, 1.0]], "asks": [[101.0, 1.0]]}]
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "REJECTED"
    assert "spread exceeds the 0.5% price protection" in result.error
    assert not any(exchange.requests for exchange in exchanges.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("tick", [0.1, 1.0])
async def test_canary_uses_arcus_effective_price_tier_before_either_leg_is_sent(store, exchanges, canary_request, tick):
    exchange = exchanges["arcus"]
    exchange.books = [{"bids": [[100.0, 1.0]], "asks": [[100.2, 1.0]]}]
    exchange.price_tick = lambda _symbol, _price: tick
    result = await CanaryRunner(exchanges, store).run(canary_request)
    if tick == 1.0:
        assert result.status == "REJECTED"
        assert "0.5% price protection" in result.error
        assert not any(item.requests for item in exchanges.values())
    else:
        assert result.status == "CLOSED"
        assert exchange.requests[0].price == 100.5
        assert exchange.requests[1].price == 99.7


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["open", "close"])
async def test_canary_cancellation_preserves_recovery_and_order_identities(store, exchanges, canary_request, purpose):
    submitted = {venue: asyncio.Event() for venue in exchanges}
    release_ack = asyncio.Event()
    for venue, exchange in exchanges.items():

        async def interrupted_ack(order, instrument, submit=exchange.submit_order, ready=submitted[venue]):
            snapshot = await submit(order, instrument)
            if order.is_reduce_only == (purpose == "close"):
                ready.set()
                await release_ack.wait()
            return snapshot

        exchange.submit_order = interrupted_ack
    task = asyncio.create_task(CanaryRunner(exchanges, store, timeout_seconds=30).run(canary_request))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in submitted.values())), 10)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    cycles = await store.list_arbitrage_cycles()
    assert len(cycles) == 1
    assert cycles[0]["status"] == "RECOVERY"
    assert "cancelled" in cycles[0]["failure_reason"]
    rows = await store.get_arbitrage_cycle_legs(cycles[0]["cycle_id"])
    assert len(rows) == (2 if purpose == "open" else 4)
    assert {order.client_order_id for item in exchanges.values() for order in item.requests} == {
        row["client_order_id"] for row in rows
    }
    assert all(row["status"] == "UNKNOWN" for row in rows if f":{purpose}:" in row["role"])
    assert all(len(item.requests) == (1 if purpose == "open" else 2) for item in exchanges.values())


@pytest.mark.asyncio
async def test_canary_enforces_hard_hundred_dollar_per_order_cap(store, exchanges, canary_request):
    result = await CanaryRunner(exchanges, store).run(replace(canary_request, quantity_base=1, max_notional_usd=1000))
    assert result.status == "REJECTED"
    assert "max_notional_usd $100.00" in result.error
    assert not any(exchange.requests for exchange in exchanges.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("close_book", "error"),
    [
        (RuntimeError("close quote unavailable"), "close quote unavailable"),
        ({"bids": [[99.9, 0.0001]], "asks": [[100.1, 0.0001]]}, "0.5% price protection"),
        ({"bids": [[999.9, 1.0]], "asks": [[1000.1, 1.0]]}, "max_notional_usd"),
    ],
)
async def test_canary_close_protection_failure_preserves_exposure_and_identity(
    store, exchanges, canary_request, close_book, error
):
    for exchange in exchanges.values():
        exchange.books.append(close_book)
    result = await CanaryRunner(exchanges, store).run(replace(canary_request, max_notional_usd=0.2))
    assert result.status == "RECOVERY"
    assert error in result.error
    assert result.opening.status == "OPEN"
    assert result.closing is None
    assert all(len(exchange.requests) == 1 for exchange in exchanges.values())
    assert all(exchange.position_qty != 0 for exchange in exchanges.values())
    rows = await store.get_arbitrage_cycle_legs(result.cycle_id)
    assert len(rows) == 2
    assert {row["client_order_id"] for row in rows} == {leg.client_order_id for leg in result.opening.legs}
    assert (await store.get_arbitrage_cycle(result.cycle_id))["status"] == "RECOVERY"
    blocked = await CanaryRunner(exchanges, store).run(canary_request)
    assert blocked.status == "REJECTED"
    assert result.cycle_id in blocked.error
    assert all(len(exchange.requests) == 1 for exchange in exchanges.values())


@pytest.mark.asyncio
async def test_canary_retains_unknown_submission_without_resending(store, exchanges, canary_request):
    submit = exchanges["arcus"].submit_order

    async def timeout_after_accept(order, instrument):
        await submit(order, instrument)
        raise TimeoutError("submission acknowledgement lost")

    exchanges["arcus"].submit_order = timeout_after_accept
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "RECOVERY"
    assert all(len(exchange.requests) == 1 for exchange in exchanges.values())
    assert len(result.opening.legs) == 2
    unknown = next(leg for leg in result.opening.legs if leg.venue == "arcus")
    assert unknown.client_order_id == exchanges["arcus"].requests[0].client_order_id
    assert unknown.filled_qty_base is None
    assert unknown.order_id is None
    assert result.closing is None


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["open", "close"])
async def test_canary_exception_after_send_restores_durable_order_identities(
    store, exchanges, canary_request, monkeypatch, purpose
):
    update = store.update_arbitrage_cycle_leg

    async def fail_confirmation(leg_id, **fields):
        if fields.get("status") == "CLOSED" and f"-a-{purpose}-" in leg_id:
            raise RuntimeError(f"cannot save {purpose} acknowledgement")
        await update(leg_id, **fields)

    monkeypatch.setattr(store, "update_arbitrage_cycle_leg", fail_confirmation)
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "RECOVERY"
    assert f"cannot save {purpose} acknowledgement" in result.error
    count = 1 if purpose == "open" else 2
    affected = result.opening if purpose == "open" else result.closing
    assert all(len(exchange.requests) == count for exchange in exchanges.values())
    assert len(affected.legs) == 2
    assert {leg.client_order_id for leg in affected.legs} == {
        exchange.requests[-1].client_order_id for exchange in exchanges.values()
    }
    assert next(leg for leg in affected.legs if leg.venue == "hyperliquid").order_id == f"hyperliquid-{count}"
    assert (await store.get_arbitrage_cycle(result.cycle_id))["status"] == "RECOVERY"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["position", "orders", "read_error"])
async def test_canary_only_marks_closed_after_verified_flat_account(
    store, exchanges, canary_request, failure, monkeypatch
):
    exchange = exchanges["arcus"]
    read_position = exchange.fetch_order_position
    read_orders = exchange.fetch_open_orders
    statuses = []
    update = store.update_arbitrage_cycle

    async def record_status(cycle_id, **fields):
        if "status" in fields:
            statuses.append(fields["status"])
        await update(cycle_id, **fields)

    async def residual_position(instrument):
        snapshot = await read_position(instrument)
        if len(exchange.requests) == 2:
            if failure == "read_error":
                raise RuntimeError("final account read unavailable")
            if failure == "position":
                return replace(snapshot, qty_native=0.001)
        return snapshot

    async def remaining_orders(symbol, params=None):
        rows = await read_orders(symbol, params)
        return [{"id": "remaining"}] if len(exchange.requests) == 2 and failure == "orders" else rows

    exchange.fetch_order_position = residual_position
    exchange.fetch_open_orders = remaining_orders
    monkeypatch.setattr(store, "update_arbitrage_cycle", record_status)
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "RECOVERY"
    assert "CLOSED" not in statuses
    assert (await store.get_arbitrage_cycle(result.cycle_id))["status"] != "CLOSED"
    assert all(len(item.requests) == 2 for item in exchanges.values())


@pytest.mark.asyncio
async def test_canary_does_not_clear_manual_review_block(store, exchanges, canary_request):
    await store.create_arbitrage_cycle(
        cycle_id="blocked-canary",
        base="BTC",
        market_type="perp",
        direction=canary_request.direction,
        venue_buy=canary_request.venue_a,
        venue_sell=canary_request.venue_b,
        symbol_buy=exchanges[canary_request.venue_a].instrument.venue_symbol,
        symbol_sell=exchanges[canary_request.venue_b].instrument.venue_symbol,
        target_qty_base=canary_request.quantity_base,
        status="MANUAL_REVIEW",
    )
    result = await CanaryRunner(exchanges, store).run(canary_request)
    assert result.status == "REJECTED"
    assert "blocked-canary (MANUAL_REVIEW)" in result.error
    assert (await store.get_arbitrage_cycle("blocked-canary"))["status"] == "MANUAL_REVIEW"
    assert not any(exchange.requests for exchange in exchanges.values())
