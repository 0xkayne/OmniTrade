"""Offline guarantees for the explicit live-test boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.exchange.order import OrderPositionSnapshot, OrderSnapshot
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.persistence.store import PersistenceStore
from tests.e2e.dex_testnet_runner import _Blocked, _Budget, _common_step, _DexRun


def test_budget_reserves_before_send_and_never_reuses_identity(tmp_path):
    budget = _Budget(100, 400, tmp_path / "budget.json")
    for index in range(4):
        budget.reserve(str(index), 100)
    with pytest.raises(_Blocked, match="budget exhausted"):
        budget.reserve("extra", 1)
    with pytest.raises(_Blocked, match="already used"):
        budget.reserve("0", 1)
    assert budget.reserved == 400
    assert (tmp_path / "budget.json").exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1, 100.01])
def test_budget_rejects_invalid_order_notional(tmp_path, value):
    budget = _Budget(100, 5000, tmp_path / "budget.json")
    with pytest.raises(_Blocked):
        budget.reserve("bad", value)
    assert not budget.reservations


def test_common_quantity_step_is_exact_for_different_venues():
    assert _common_step(0.00000001, 0.00001) == 0.00001
    assert _common_step(0.003, 0.002) == 0.006


def _exchange():
    return SimpleNamespace(
        name="arcus",
        network_type=NetworkType.TESTNET,
        rest_base_url="https://api.testnet.arcus.xyz",
        websocket_url="wss://api.testnet.arcus.xyz/v1/ws",
        create_order=AsyncMock(return_value={"id": "synthetic-order"}),
        fetch_orderbook=AsyncMock(return_value={"bids": [[99.9, 10]], "asks": [[100.1, 10]]}),
        fetch_order_position=AsyncMock(return_value=SimpleNamespace(qty_native=0)),
    )


async def test_read_only_run_cannot_submit_even_with_credentials(tmp_path):
    runner = _DexRun(tmp_path)
    exchange = _exchange()
    original = exchange.create_order
    runner.install_gate(exchange)
    with pytest.raises(_Blocked, match="explicit testnet trading flag"):
        await exchange.create_order("BTC-USD", "limit", "buy", 0.001, 100)
    original.assert_not_awaited()


async def test_live_gate_rechecks_network_and_budget_before_transport(tmp_path):
    runner = _DexRun(tmp_path, trade=True)
    instrument = Instrument("arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD", qty_step=0.001)
    runner.markets["arcus"] = [instrument]
    exchange = _exchange()
    original = exchange.create_order
    runner.install_gate(exchange)
    exchange.rest_base_url = "https://api.arcus.xyz"
    with pytest.raises(_Blocked, match="allowlist"):
        await exchange.create_order("BTC-USD", "limit", "buy", 1, 100, {"clientOrderId": "one"})
    exchange.rest_base_url = "https://api.testnet.arcus.xyz"
    with pytest.raises(_Blocked, match="per-order"):
        await exchange.create_order("BTC-USD", "limit", "buy", 2, 100, {"clientOrderId": "two"})
    original.assert_not_awaited()
    await exchange.create_order("BTC-USD", "limit", "buy", 0.1, 100, {"clientOrderId": "three"})
    assert runner.budget.reserved == 10
    original.assert_awaited_once()


async def test_durable_budget_failure_prevents_transmission(tmp_path, monkeypatch):
    runner = _DexRun(tmp_path, trade=True)
    runner.markets["arcus"] = [
        Instrument("arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD", qty_step=0.001)
    ]
    exchange = _exchange()
    original = exchange.create_order
    runner.install_gate(exchange)

    def fail():
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(runner.budget, "flush", fail)
    with pytest.raises(OSError):
        await exchange.create_order("BTC-USD", "limit", "buy", 0.1, 100, {"clientOrderId": "identity"})
    original.assert_not_awaited()


def test_report_redacts_nested_credentials(tmp_path):
    runner = _DexRun(tmp_path)
    runner.sensitive = ["synthetic-secret-value", "synthetic-wallet-value"]
    runner.record("failure", "FAIL", {"nested": ["request synthetic-secret-value for synthetic-wallet-value"]})
    for name in ("report.json", "report.md"):
        content = (tmp_path / name).read_text()
        assert all(value not in content for value in runner.sensitive)
        assert "[redacted]" in content


async def test_durable_context_survives_discarding_all_in_memory_orders(tmp_path):
    runner = _DexRun(tmp_path, trade=True)
    runner.store = PersistenceStore(tmp_path / "execution.db", tmp_path / "audit")
    await runner.store.initialize()
    instrument = Instrument("arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD", qty_step=0.001)
    exchange = _exchange()
    exchange.fetch_order_position = AsyncMock(
        return_value=OrderPositionSnapshot("BTC-USD", 0, None, 100, 20, "cross", 0)
    )
    exchange.order_capabilities = lambda inst: SimpleNamespace(has_position_validation=True)

    async def submit(request, inst):
        row = await runner.store.get_order_row(request.client_order_id)
        leg = await runner.store.get_leg(row.leg_id)
        assert row.status == "UNKNOWN"
        assert leg.execution_context_json
        return OrderSnapshot("synthetic-order", "closed", request.amount, 100, 0)

    exchange.submit_order = AsyncMock(side_effect=submit)
    exchange.fetch_order_snapshot = AsyncMock(return_value=OrderSnapshot("synthetic-order", "closed", 0.1, 100, 0))
    runner.exchanges["arcus"] = exchange
    try:
        await runner.new_order(exchange, instrument, "buy", 0.1, 100)
        runner.orders.clear()
        assert await runner.readback() == {"orders": 1, "cycles": 0, "resubmissions": 0}
        exchange.submit_order.assert_awaited_once()
        exchange.fetch_order_snapshot.assert_awaited_once()
    finally:
        await runner.store.close()


async def test_gate_rejects_position_drift_before_transmission(tmp_path):
    runner = _DexRun(tmp_path, trade=True)
    runner.markets["arcus"] = [
        Instrument("arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD", qty_step=0.001)
    ]
    exchange = _exchange()
    exchange.fetch_order_position.return_value.qty_native = 0.5
    original = exchange.create_order
    runner.install_gate(exchange)
    with pytest.raises(_Blocked, match="baseline changed"):
        await exchange.create_order("BTC-USD", "limit", "buy", 0.1, 100, {"clientOrderId": "identity"})
    original.assert_not_awaited()
    assert not runner.budget.reservations


@pytest.mark.parametrize(
    "price,amount,tif,reason", [(101, 0.1, "GTC", "protection band"), (100.4, 0.2, "IOC", "insufficient depth")]
)
async def test_gate_rechecks_price_and_depth_before_transmission(tmp_path, price, amount, tif, reason):
    runner = _DexRun(tmp_path, trade=True)
    runner.markets["arcus"] = [
        Instrument("arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD", qty_step=0.001)
    ]
    exchange = _exchange()
    exchange.fetch_orderbook.return_value = {"bids": [[99.9, 0.1]], "asks": [[100.1, 0.1]]}
    original = exchange.create_order
    runner.install_gate(exchange)
    with pytest.raises(_Blocked, match=reason):
        await exchange.create_order(
            "BTC-USD", "limit", "buy", amount, price, {"clientOrderId": "identity", "timeInForce": tif}
        )
    original.assert_not_awaited()
    assert not runner.budget.reservations


async def test_pair_cancellation_halts_the_run(tmp_path, monkeypatch):
    runner = _DexRun(tmp_path, trade=True)
    for venue, symbol in (("arcus", "BTC-USD"), ("hyperliquid", "BTC/USDC:USDC")):
        instrument = Instrument(
            venue, NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), symbol, qty_step=0.001, price_step=0.01
        )
        runner.selected[(venue, "perp")] = instrument
        exchange = _exchange()
        exchange.name = venue
        runner.exchanges[venue] = exchange
    runner.clean = AsyncMock(return_value={})
    runner.book = AsyncMock(return_value={"bids": [[99.9, 10]], "asks": [[100.1, 10]]})
    canary = SimpleNamespace(run=AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr("tests.e2e.dex_testnet_runner.TestnetCanary", lambda *args, **kwargs: canary)
    with pytest.raises(asyncio.CancelledError):
        await runner.pair_cycle("buy_a_sell_b")
    assert runner.halted
    assert runner.checks[-1]["check"] == "pair.recovery_required"
    with pytest.raises(_Blocked, match="prior scenario"):
        await runner.pair_cycle("buy_b_sell_a")
    canary.run.assert_awaited_once()


async def test_cancellation_during_cleanup_halts_later_scenarios(tmp_path):
    runner = _DexRun(tmp_path, trade=True)
    exchange = _exchange()
    instrument = Instrument(
        "arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD", qty_step=0.001, price_step=0.01
    )
    runner.clean = AsyncMock(return_value={})
    runner.new_order = AsyncMock(side_effect=_Blocked("synthetic send failure"))
    runner.flatten = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await runner.lifecycle(exchange, instrument, "buy")
    assert runner.halted
    assert runner.checks[-1]["status"] == "BLOCKED"


async def test_account_websocket_survives_idle_timeout_on_python310(tmp_path):
    runner = _DexRun(tmp_path)
    exchange = _exchange()
    instrument = Instrument("arcus", NetworkType.TESTNET, "perp", Asset("BTC"), Asset("USD"), "BTC-USD")
    exchange.account_params = lambda inst: {}
    exchange.watch_orders = AsyncMock(
        side_effect=[asyncio.TimeoutError(), [{"id": "synthetic-order"}], asyncio.CancelledError()]
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.watch_account(exchange, instrument, "orders")
    assert runner.ws_events[("arcus", "BTC-USD", "orders")] == [{"id": "synthetic-order"}]
    assert not runner.checks
