"""Offline safety and bounded execution coordinator tests."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from ccxt.base.errors import AuthenticationError, InsufficientFunds, InvalidOrder, OrderNotFound, PermissionDenied

from src.arbitrage.executor import HedgedExecutor
from src.arbitrage.models import ArbCycle, ArbPair
from src.arbitrage.recovery import ArbitrageRecovery
from src.exchange.mock import MockExchange
from src.exchange.order import OrderCapabilities, OrderRequest, OrderSnapshot
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.persistence.store import PersistenceStore


def _cycle() -> ArbCycle:
    pair = ArbPair("BTC", "perp", "arcus", "BTC-USD", "binance", "BTCUSDT")
    return ArbCycle("cycle-1", pair, "buy_a_sell_b", 1.0)


def _testnet_cycle() -> ArbCycle:
    instruments = [
        Instrument(venue, NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USDC"), symbol)
        for venue, symbol in (("arcus", "BTC-USD"), ("hyperliquid", "BTC/USDC:USDC"))
    ]
    pair = ArbPair(
        "BTC",
        "perp",
        "arcus",
        "BTC-USD",
        "hyperliquid",
        "BTC/USDC:USDC",
        instrument_a=instruments[0],
        instrument_b=instruments[1],
    )
    return ArbCycle("testnet-cycle", pair, "buy_a_sell_b", 1.0)


@pytest.fixture
async def arb_store(tmp_path):
    store = PersistenceStore(tmp_path / "executor.db", tmp_path / "audit")
    await store.initialize()
    yield store
    await store.close()


class TestnetVenue:
    """Adapter contract double; every request remains in memory."""

    __test__ = False
    network_type = NetworkType.TESTNET

    def __init__(self, name, outcomes=None):
        self.name = name
        self.requests = []
        self.outcomes = list(outcomes or [])
        self.polls = []

    def order_capabilities(self, _instrument):
        return OrderCapabilities(True, ("IOC",))

    async def submit_order(self, request, _instrument):
        self.requests.append(request)
        quantity, status = self.outcomes.pop(0) if self.outcomes else (request.amount, "closed")
        trade_id = f"{self.name}-trade-{len(self.requests)}"
        return OrderSnapshot(
            f"{self.name}-{len(self.requests)}",
            status,
            quantity,
            request.price,
            0,
            fills=[{"id": trade_id, "price": request.price, "amount": quantity, "timestamp": 1}] if quantity else [],
        )

    async def fetch_order_snapshot(self, request, _instrument, order_id=None):
        self.polls.append((request, order_id))
        return OrderSnapshot(order_id, "unknown", None, None)


def _testnet_executor(store, arcus=None, hyperliquid=None, **kwargs):
    exchanges = {"arcus": arcus or TestnetVenue("arcus"), "hyperliquid": hyperliquid or TestnetVenue("hyperliquid")}
    return HedgedExecutor(
        exchanges,
        store,
        dry_run=False,
        execution_mode="testnet",
        testnet_confirmed=True,
        timeout_seconds=0.05,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_dry_run_never_calls_exchange() -> None:
    exchange = MockExchange("arcus")
    result = await HedgedExecutor({"arcus": exchange}, dry_run=True).open_cycle(
        _cycle(), buy_price=100.0, sell_price=101.0
    )

    assert result.status == "DRY_RUN"
    assert exchange.create_order_calls == []


def test_client_order_id_is_deterministic_and_hex() -> None:
    first = HedgedExecutor.client_order_id("cycle-1", "a", 0)
    second = HedgedExecutor.client_order_id("cycle-1", "a", 0)

    assert first == second
    assert first.startswith("0x")
    assert len(first) == 34
    int(first[2:], 16)


@pytest.mark.asyncio
async def test_simulation_rejects_non_mock_adapter() -> None:
    class RealLikeExchange:
        pass

    result = await HedgedExecutor({"arcus": RealLikeExchange()}, dry_run=False, simulate=True).open_cycle(
        _cycle(), buy_price=100.0, sell_price=101.0
    )

    assert result.status == "REJECTED"
    assert "MockExchange" in (result.error or "")


@pytest.mark.asyncio
async def test_testnet_requires_explicit_confirmation() -> None:
    arcus = MockExchange("arcus")
    hyperliquid = MockExchange("hyperliquid")
    result = await HedgedExecutor(
        {"arcus": arcus, "binance": hyperliquid},
        dry_run=False,
        execution_mode="testnet",
    ).open_cycle(_cycle(), buy_price=100.0, sell_price=101.0)

    assert result.status == "REJECTED"
    assert "testnet_confirmed" in (result.error or "")
    assert arcus.create_order_calls == []


@pytest.mark.asyncio
async def test_testnet_requires_instrument_metadata() -> None:
    arcus = MockExchange("arcus")
    hyperliquid = MockExchange("hyperliquid")
    result = await HedgedExecutor(
        {"arcus": arcus, "binance": hyperliquid},
        dry_run=False,
        execution_mode="testnet",
        testnet_confirmed=True,
    ).open_cycle(_cycle(), buy_price=100.0, sell_price=101.0)

    assert result.status == "REJECTED"
    assert "instrument metadata" in (result.error or "")


@pytest.mark.asyncio
async def test_close_submits_shared_reduce_only_order_request() -> None:
    exchange_a = MockExchange("arcus")
    exchange_b = MockExchange("binance")
    requests: list[OrderRequest] = []

    async def filled(request, _instrument):
        requests.append(request)
        return SimpleNamespace(
            order_id=f"order-{request.symbol}",
            status="closed",
            filled_qty_base=request.amount,
            avg_price=request.price,
            fee_usd=0.0,
        )

    exchange_a.submit_order = filled
    exchange_b.submit_order = filled
    cycle = _cycle()
    cycle.status = "open"
    cycle.filled_qty_a = 1.0
    cycle.filled_qty_b = 1.0
    result = await HedgedExecutor(
        {"arcus": exchange_a, "binance": exchange_b}, dry_run=False, simulate=True
    ).close_cycle(cycle, buy_price=100.0, sell_price=101.0)

    assert result.status == "CLOSED"
    assert all(isinstance(request, OrderRequest) for request in requests)
    assert [request.is_reduce_only for request in requests] == [True, True]


@pytest.mark.asyncio
async def test_unknown_submit_is_retained_as_unknown() -> None:
    exchange_a = MockExchange("arcus")
    exchange_b = MockExchange("binance")

    async def ambiguous(_request, _instrument):
        raise TimeoutError("transport timeout")

    async def rejected(_request, _instrument):
        return SimpleNamespace(order_id="order-b", status="closed", filled_qty_base=1.0, avg_price=101.0, fee_usd=0.0)

    exchange_a.submit_order = ambiguous
    exchange_b.submit_order = rejected
    result = await HedgedExecutor(
        {"arcus": exchange_a, "binance": exchange_b}, dry_run=False, simulate=True
    ).open_cycle(_cycle(), buy_price=100.0, sell_price=101.0)

    assert result.status == "RECOVERY"
    assert result.legs[0].status == "unknown"
    assert result.legs[0].filled_qty_base is None


@pytest.mark.asyncio
async def test_recovery_marks_unfinished_cycle_for_manual_review(tmp_path) -> None:
    from src.persistence.store import PersistenceStore

    store = PersistenceStore(tmp_path / "arb.db", tmp_path / "audit")
    await store.initialize()
    await store.create_arbitrage_cycle(
        cycle_id="cycle-recovery",
        base="BTC",
        market_type="perp",
        direction="buy_a_sell_b",
        venue_buy="arcus",
        venue_sell="binance",
        symbol_buy="BTC-USD",
        symbol_sell="BTCUSDT",
        target_qty_base=1.0,
        status="OPENING",
    )
    result = await ArbitrageRecovery(store, simulate=True).recover(
        {"arcus": MockExchange("arcus"), "binance": MockExchange("binance")}
    )

    assert result[0].status == "MANUAL_REVIEW"
    assert (await store.get_arbitrage_cycle("cycle-recovery"))["status"] == "MANUAL_REVIEW"
    await store.close()


@pytest.mark.asyncio
async def test_testnet_happy_path_persists_open_and_reduce_only_close(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    opened = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert opened.status == "OPEN"
    assert (await arb_store.get_arbitrage_cycle(opened.cycle.cycle_id))["status"] == "OPEN"
    assert len(await arb_store.get_arbitrage_fills(opened.cycle.cycle_id)) == 2
    assert len(await arb_store.get_arbitrage_cycle_legs(opened.cycle.cycle_id)) == 2

    closed = await executor.close_cycle(opened.cycle, buy_price=100, sell_price=101)
    assert closed.status == "CLOSED"
    assert closed.cycle.filled_qty_a == closed.cycle.filled_qty_b == 0
    saved = await arb_store.get_arbitrage_cycle(closed.cycle.cycle_id)
    assert saved["status"] == "CLOSED"
    assert saved["opened_qty_base"] == saved["closed_qty_base"] == 1
    legs = await arb_store.get_arbitrage_cycle_legs(closed.cycle.cycle_id)
    assert {leg["role"] for leg in legs} == {"a:open:0", "b:open:0", "a:close:0", "b:close:0"}
    assert len({leg["client_order_id"] for leg in legs}) == 4
    for exchange in executor.exchanges.values():
        assert [request.is_reduce_only for request in exchange.requests] == [False, True]


@pytest.mark.asyncio
async def test_testnet_accepts_arcus_binance_pair(arb_store) -> None:
    instruments = [
        Instrument("arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD"),
        Instrument("binance", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USDT"), "BTC/USDT:USDT"),
    ]
    pair = ArbPair(
        "BTC",
        "perp",
        "arcus",
        "BTC-USD",
        "binance",
        "BTC/USDT:USDT",
        instrument_a=instruments[0],
        instrument_b=instruments[1],
    )
    cycle = ArbCycle("arcus-binance-cycle", pair, "buy_a_sell_b", 1.0)
    executor = HedgedExecutor(
        {"arcus": TestnetVenue("arcus"), "binance": TestnetVenue("binance")},
        arb_store,
        dry_run=False,
        execution_mode="testnet",
        testnet_confirmed=True,
        timeout_seconds=0.05,
    )

    result = await executor.open_cycle(cycle, buy_price=100, sell_price=101)

    assert result.status == "OPEN"


@pytest.mark.asyncio
async def test_canceled_partial_ioc_hedges_actual_delta_without_close_id_collision(arb_store) -> None:
    arcus = TestnetVenue("arcus", [(0.4, "canceled"), (0.6, "closed")])
    executor = _testnet_executor(arb_store, arcus=arcus)
    opened = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert opened.status == "OPEN"
    assert [request.amount for request in arcus.requests] == [1.0, 0.6]
    assert opened.cycle.filled_qty_a == opened.cycle.filled_qty_b == 1
    closed = await executor.close_cycle(opened.cycle, buy_price=100, sell_price=101)
    assert closed.status == "CLOSED"
    assert len({request.client_order_id for request in arcus.requests}) == 3
    assert len(await arb_store.get_arbitrage_cycle_legs(opened.cycle.cycle_id)) == 5


@pytest.mark.asyncio
async def test_partial_close_keeps_residual_position_and_requires_recovery(arb_store) -> None:
    executor = _testnet_executor(arb_store, arcus=TestnetVenue("arcus", [(1, "closed"), (0.3, "canceled")]))
    opened = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    closed = await executor.close_cycle(opened.cycle, buy_price=100, sell_price=101)
    assert closed.status == "RECOVERY"
    assert closed.cycle.filled_qty_a == pytest.approx(0.7)
    assert closed.cycle.filled_qty_b == 0
    assert (await arb_store.get_arbitrage_cycle(closed.cycle.cycle_id))["status"] == "UNHEDGED"


@pytest.mark.asyncio
async def test_duplicate_cycle_never_resends_and_snapshot_fills_are_idempotent(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    cycle = _testnet_cycle()
    opened = await executor.open_cycle(cycle, buy_price=100, sell_price=101)
    duplicate = await _testnet_executor(arb_store, **executor.exchanges).open_cycle(
        cycle, buy_price=100, sell_price=101
    )
    assert duplicate.status == "RECOVERY"
    assert [len(exchange.requests) for exchange in executor.exchanges.values()] == [1, 1]
    row = (await arb_store.get_arbitrage_cycle_legs(cycle.cycle_id))[0]
    snapshot = OrderSnapshot(
        row["venue_order_id"],
        "closed",
        1,
        100,
        0,
        fills=[{"id": "arcus-trade-1", "amount": 1, "price": 100, "timestamp": 1}],
    )
    await executor.record_snapshot(opened.cycle, row, snapshot)
    assert len(await arb_store.get_arbitrage_fills(cycle.cycle_id)) == 2


@pytest.mark.asyncio
async def test_both_legs_persist_before_any_send_and_write_failure_is_fatal(arb_store, monkeypatch) -> None:
    executor = _testnet_executor(arb_store)
    original = arb_store.create_arbitrage_cycle_leg

    async def fail_second_leg(**fields):
        if fields["role"].startswith("b:"):
            raise OSError("disk unavailable")
        await original(**fields)

    monkeypatch.setattr(arb_store, "create_arbitrage_cycle_leg", fail_second_leg)
    with pytest.raises(OSError, match="disk unavailable"):
        await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert all(exchange.requests == [] for exchange in executor.exchanges.values())


@pytest.mark.asyncio
async def test_unknown_submission_is_queried_by_client_id_and_never_resent(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    arcus = executor.exchanges["arcus"]

    async def ambiguous(request, _instrument):
        arcus.requests.append(request)
        raise TimeoutError("lost ACK")

    arcus.submit_order = ambiguous
    result = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert result.status == "RECOVERY"
    assert len(arcus.requests) == 1
    assert arcus.polls
    assert arcus.polls[0][0].client_order_id == arcus.requests[0].client_order_id
    assert arcus.polls[0][1] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [AuthenticationError, PermissionDenied, InsufficientFunds, InvalidOrder])
async def test_explicit_submit_rejection_persists_zero_without_identity_queries(arb_store, error_type) -> None:
    executor = _testnet_executor(arb_store)
    for venue in executor.exchanges.values():

        async def reject(request, _instrument, exchange=venue):
            exchange.requests.append(request)
            raise error_type("synthetic request rejection")

        venue.submit_order = reject

    result = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)

    assert result.status == "REJECTED"
    assert all(leg.status == "rejected" and leg.filled_qty_base == 0 for leg in result.legs)
    assert all(len(exchange.requests) == 1 and not exchange.polls for exchange in executor.exchanges.values())
    rows = await arb_store.get_arbitrage_cycle_legs(result.cycle.cycle_id)
    assert all(row["status"] == "REJECTED" and row["filled_qty_base"] == 0 for row in rows)


@pytest.mark.asyncio
async def test_order_not_found_during_submit_stays_ambiguous_and_is_queried(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    arcus = executor.exchanges["arcus"]

    async def not_found(request, _instrument):
        arcus.requests.append(request)
        raise OrderNotFound("accepted identity is not indexed yet")

    arcus.submit_order = not_found
    result = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    leg = next(item for item in result.legs if item.venue == "arcus")
    assert result.status == "RECOVERY"
    assert leg.status == "unknown"
    assert leg.filled_qty_base is None
    assert arcus.polls and len(arcus.requests) == 1
    saved = next(
        row for row in await arb_store.get_arbitrage_cycle_legs(result.cycle.cycle_id) if row["venue"] == "arcus"
    )
    assert saved["status"] == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [AuthenticationError, PermissionDenied, InsufficientFunds, InvalidOrder, OrderNotFound]
)
async def test_query_error_after_ack_never_reclassifies_submission_as_rejected(arb_store, error_type) -> None:
    executor = _testnet_executor(arb_store)
    arcus = executor.exchanges["arcus"]

    async def ack(request, _instrument):
        arcus.requests.append(request)
        return OrderSnapshot("accepted-order", "open", None, None)

    async def query_failure(request, _instrument, order_id=None):
        arcus.polls.append((request, order_id))
        raise error_type("synthetic confirmation failure")

    arcus.submit_order, arcus.fetch_order_snapshot = ack, query_failure
    result = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    leg = next(item for item in result.legs if item.venue == "arcus")
    assert result.status == "RECOVERY"
    assert leg.status == "unknown" and leg.filled_qty_base is None
    assert leg.order_id == "accepted-order"
    assert len(arcus.requests) == 1 and arcus.polls
    saved = next(
        row for row in await arb_store.get_arbitrage_cycle_legs(result.cycle.cycle_id) if row["venue"] == "arcus"
    )
    assert saved["status"] != "REJECTED"


@pytest.mark.asyncio
async def test_ack_recording_error_is_outside_submit_rejection_boundary(arb_store, monkeypatch) -> None:
    executor = _testnet_executor(arb_store)

    async def invalid_record(*_args):
        raise InvalidOrder("synthetic error while recording an already accepted order")

    monkeypatch.setattr(executor, "_confirm_leg", invalid_record)
    with pytest.raises(InvalidOrder, match="already accepted"):
        await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    rows = await arb_store.get_arbitrage_cycle_legs(_testnet_cycle().cycle_id)
    assert len(rows) == 2 and all(row["status"] == "UNKNOWN" for row in rows)


@pytest.mark.asyncio
async def test_private_snapshot_can_take_longer_than_two_hundred_milliseconds(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    executor.timeout_seconds = 1
    arcus = executor.exchanges["arcus"]

    async def ack(request, _instrument):
        arcus.requests.append(request)
        return OrderSnapshot("accepted-order", "open", None, None)

    async def confirmed(request, _instrument, order_id=None):
        arcus.polls.append((request, order_id))
        await asyncio.sleep(0.25)
        return OrderSnapshot(order_id, "closed", request.amount, request.price)

    arcus.submit_order, arcus.fetch_order_snapshot = ack, confirmed
    result = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert result.status == "OPEN"
    assert len(arcus.requests) == 1 and len(arcus.polls) == 1


@pytest.mark.asyncio
async def test_private_snapshot_timeout_never_outlives_confirmation_deadline(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    executor.timeout_seconds = 0.05
    arcus = executor.exchanges["arcus"]
    query_canceled = asyncio.Event()
    query_elapsed = []

    async def ack(request, _instrument):
        arcus.requests.append(request)
        return OrderSnapshot("accepted-order", "open", None, None)

    async def hanging_query(request, _instrument, order_id=None):
        arcus.polls.append((request, order_id))
        started = time.monotonic()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            query_elapsed.append(time.monotonic() - started)
            query_canceled.set()
            raise

    arcus.submit_order, arcus.fetch_order_snapshot = ack, hanging_query
    result = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert result.status == "RECOVERY"
    assert query_canceled.is_set()
    assert len(arcus.requests) == 1 and len(arcus.polls) == 1
    assert max(query_elapsed) < 0.5  # The new 5 s per-call cap cannot replace the 50 ms budget.


@pytest.mark.asyncio
async def test_ack_confirmation_polls_until_terminal_and_persists_final_order_id(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    arcus = executor.exchanges["arcus"]

    async def ack(request, _instrument):
        arcus.requests.append(request)
        return OrderSnapshot(None, "pending", None, None)

    async def confirmed(request, _instrument, order_id=None):
        arcus.polls.append((request, order_id))
        return OrderSnapshot("arcus-final", "closed", request.amount, request.price)

    arcus.submit_order, arcus.fetch_order_snapshot = ack, confirmed
    opened = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert opened.status == "OPEN"
    assert len(arcus.requests) == 1
    row = (await arb_store.get_arbitrage_cycle_legs(opened.cycle.cycle_id))[0]
    assert row["status"] == "CLOSED"
    assert row["venue_order_id"] == "arcus-final"


@pytest.mark.asyncio
async def test_terminal_order_waits_for_matching_private_fill_details(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    arcus = executor.exchanges["arcus"]

    async def terminal_without_fills(request, _instrument):
        arcus.requests.append(request)
        return OrderSnapshot("arcus-filled", "closed", request.amount, request.price)

    async def private_fill(_symbol):
        return {"id": "actual-trade", "order": "arcus-filled", "amount": 1, "price": 100, "timestamp": 1}

    arcus.submit_order = terminal_without_fills
    arcus.watch_user_fills = private_fill
    opened = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert opened.status == "OPEN"
    fills = await arb_store.get_arbitrage_fills(opened.cycle.cycle_id)
    assert {fill["trade_id"] for fill in fills} == {"actual-trade", "hyperliquid-trade-1"}


@pytest.mark.asyncio
async def test_partial_hedge_stays_in_recovery_with_remaining_exposure(arb_store) -> None:
    executor = _testnet_executor(arb_store, arcus=TestnetVenue("arcus", [(0.4, "canceled"), (0.2, "canceled")]))
    result = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    assert result.status == "RECOVERY"
    assert result.cycle.filled_qty_a == pytest.approx(0.6)
    assert result.cycle.filled_qty_b == 1
    assert len(executor.exchanges["arcus"].requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["mainnet", "instrument_mainnet", "ioc", "store"])
async def test_testnet_preflight_rejects_unsafe_execution(arb_store, fault) -> None:
    from dataclasses import replace

    executor = _testnet_executor(arb_store)
    cycle = _testnet_cycle()
    if fault == "mainnet":
        executor.execution_mode = "mainnet"
        executor.simulate = True
    elif fault == "instrument_mainnet":
        cycle.pair = replace(cycle.pair, instrument_a=replace(cycle.pair.instrument_a, network=NetworkType.MAINNET))
    elif fault == "ioc":
        executor.exchanges["arcus"].order_capabilities = lambda _instrument: OrderCapabilities(True, ("GTC",))
    else:
        executor.store = None
    result = await executor.open_cycle(cycle, buy_price=100, sell_price=101)
    assert result.status == "REJECTED"
    assert all(exchange.requests == [] for exchange in executor.exchanges.values())


@pytest.mark.asyncio
async def test_executor_context_allows_restart_recovery_without_resubmission(arb_store) -> None:
    executor = _testnet_executor(arb_store)
    opened = await executor.open_cycle(_testnet_cycle(), buy_price=100, sell_price=101)
    for exchange in executor.exchanges.values():

        async def confirmed(request, _instrument, order_id=None):
            return OrderSnapshot(order_id, "closed", request.amount, 100)

        exchange.fetch_order_snapshot = confirmed
    recovered = await ArbitrageRecovery(arb_store, execution_mode="testnet", testnet_confirmed=True).recover(
        executor.exchanges
    )
    assert recovered[0].status == "OPEN"
    assert recovered[0].cycle.pair == opened.cycle.pair
    assert recovered[0].cycle.filled_qty_a == recovered[0].cycle.filled_qty_b == 1
    assert all(len(exchange.requests) == 1 for exchange in executor.exchanges.values())
