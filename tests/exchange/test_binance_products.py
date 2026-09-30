"""Offline product routing, inverse fills and fail-closed Binance transports."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ccxt.base.errors import InvalidOrder

from src.exchange.binance import BinanceExchange
from src.exchange.binance_clients import (
    _attach_binance_budget,
    _BinanceRateBudget,
    _configure_binance_network,
    _create_binance_client,
)
from src.exchange.ccxt import CCXTExchange
from src.exchange.factory import ExchangeFactory
from src.exchange.order import OrderRequest, parse_order_snapshot
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType


def _config(families=None):
    return {
        "type": "ccxt",
        "default_network": "testnet",
        "networks": {"mainnet": {}, "testnet": {}},
        "market_families": families or ["spot", "usdm", "coinm"],
    }


def _market(family, **overrides):
    market = {
        "symbol": {"spot": "BTC/USDT", "usdm": "BTC/USDT:USDT", "coinm": "BTC/USD:BTC"}[family],
        "base": "BTC",
        "quote": "USD" if family == "coinm" else "USDT",
        "settle": "BTC" if family == "coinm" else "USDT",
        "type": "spot" if family == "spot" else "swap",
        "active": True,
        "inverse": family == "coinm",
        "linear": family == "usdm",
        "contractSize": 100 if family == "coinm" else 1,
        "precision": {"amount": 1 if family == "coinm" else 0.001, "price": 0.1},
        "limits": {"amount": {"min": 1 if family == "coinm" else 0.001}},
    }
    market.update(overrides)
    return market


def _client(family):
    market = _market(family)
    return SimpleNamespace(
        markets={market["symbol"]: market},
        precisionMode=4,
        load_markets=AsyncMock(),
        close=AsyncMock(),
        fetch_order_book=AsyncMock(return_value={"bids": [[20000, 10]], "asks": [[20100, 10]]}),
        fetch_balance=AsyncMock(return_value={"free": {"BTC": 1, "USDT": 1000}}),
        fetch_position_mode=AsyncMock(return_value={"hedged": False}),
        fapiPrivateGetMultiAssetsMargin=AsyncMock(return_value={"multiAssetsMargin": False}),
        fetch_positions=AsyncMock(
            return_value=[
                {
                    "symbol": market["symbol"],
                    "contracts": 2,
                    "entryPrice": 20000,
                    "markPrice": 25000,
                    "leverage": 5,
                    "marginMode": "cross",
                    "info": {"positionAmt": "-2", "positionSide": "BOTH", "maxQty": "1000"},
                }
            ]
        ),
        create_order=AsyncMock(return_value={"id": "1", "status": "open", "filled": 0}),
        fetch_order=AsyncMock(),
        fetch_my_trades=AsyncMock(return_value=[]),
        cancel_order=AsyncMock(return_value={"status": "canceled"}),
        amount_to_precision=lambda symbol, value: str(value),
        price_to_precision=lambda symbol, value: str(value),
    )


async def _exchange():
    adapter = BinanceExchange("binance", _config(), {})
    adapter.clients = {family: _client(family) for family in adapter.market_families}
    await adapter.list_markets()
    return adapter


def test_factory_uses_one_binance_adapter():
    assert isinstance(ExchangeFactory.create_exchange("binance", _config(), {}), BinanceExchange)


@pytest.mark.parametrize("family", ["spot", "usdm", "coinm"])
@pytest.mark.parametrize("network", list(NetworkType))
async def test_fixed_clients_pin_product_and_network(family, network):
    client = _create_binance_client(family, network)
    try:
        assert client.id == {"spot": "binance", "usdm": "binanceusdm", "coinm": "binancecoinm"}[family]
        assert client.options["defaultSubType"] == {"spot": None, "usdm": "linear", "coinm": "inverse"}[family]
        assert client.options["maxRetriesOnFailure"] == 0
        assert bool(client.options.get("enableDemoTrading")) == (network is NetworkType.TESTNET)
    finally:
        await client.close()


def test_demo_failure_never_accepts_mainnet():
    client = SimpleNamespace(
        urls={"api": {"dapiPrivate": "https://dapi.binance.com/dapi/v1"}}, enable_demo_trading=lambda enabled: None
    )
    with pytest.raises(ValueError, match="does not match testnet"):
        _configure_binance_network(client, NetworkType.TESTNET, "coinm")


async def test_market_mapping_does_not_silently_make_inverse_linear():
    adapter = await _exchange()
    markets = {market.venue_symbol: market for market in await adapter.list_markets()}
    inverse = markets["BTC/USD:BTC"]
    assert inverse.is_inverse and inverse.contract_size == 100
    assert inverse.settlement_asset == Asset("BTC")
    assert inverse.quantity_unit == "contracts"
    assert inverse.native_qty_from_notional(1000, 20000) == inverse.native_qty_from_notional(1000, 40000) == 10
    assert adapter.order_capabilities(inverse).has_position_validation
    assert not adapter.order_capabilities(replace(inverse, contract_size=1)).has_client_order_id
    delivery = _market("coinm", symbol="BTC/USD:BTC-261225", type="future", expiry=1)
    adapter.clients["coinm"].markets[delivery["symbol"]] = delivery
    unknown = _market("coinm", symbol="ETH/USD:ETH", contractSize=None)
    adapter.clients["coinm"].markets[unknown["symbol"]] = unknown
    assert len(await adapter.list_markets()) == 3


async def test_generic_ccxt_preserves_inverse_metadata_and_blocks_trading():
    adapter = CCXTExchange("binance", _config(), {})
    adapter.ccxt_exchange = _client("coinm")
    instrument = (await adapter.list_markets())[0]
    assert instrument.is_inverse and instrument.contract_size == 100
    assert not adapter.order_capabilities(instrument).has_client_order_id


async def test_product_routing_balance_and_order_never_cross_accounts():
    adapter = await _exchange()
    for instrument in await adapter.list_markets():
        family = adapter._families_by_symbol[instrument.venue_symbol]
        await adapter.fetch_orderbook(instrument.venue_symbol)
        await adapter.fetch_balance(adapter.account_params(instrument))
        request = OrderRequest(
            instrument.venue_symbol,
            "buy",
            2,
            "limit",
            20000,
            "product-test",
            instrument.market_type,
            "IOC",
            quantity_unit=instrument.quantity_unit,
            account_family=family,
        )
        await adapter.submit_order(request, instrument)
        client = adapter.clients[family]
        assert client.fetch_order_book.await_count == 1
        assert client.fetch_balance.await_count == 1
        assert client.create_order.await_count == 1
        params = client.create_order.call_args.args[-1]
        assert "account_family" not in params
        assert not ({"type", "subType", "maxRetriesOnFailure"} & params.keys())


async def test_inverse_requires_native_units_and_stable_account():
    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    request = OrderRequest(instrument.venue_symbol, "buy", 2, "limit", 20000, "units", "perp", "IOC")
    with pytest.raises(InvalidOrder, match="native quantity"):
        await adapter.submit_order(request, instrument)
    with pytest.raises(InvalidOrder, match="saved order account"):
        await adapter.submit_order(replace(request, quantity_unit="contracts", account_family="usdm"), instrument)
    assert adapter.clients["coinm"].create_order.await_count == 0


async def test_inverse_fill_average_is_harmonic_and_fee_conversion_is_per_fill():
    instrument = (await _exchange())._instruments["BTC/USD:BTC"]
    trades = [
        {
            "id": "1",
            "amount": 1,
            "price": 20000,
            "fee": {"currency": "BTC", "cost": 0.00001},
            "info": {"realizedPnl": "0.001"},
        },
        {"id": "2", "amount": 1, "price": 40000, "fee": {"currency": "BTC", "cost": 0.00001}},
    ]
    snapshot = parse_order_snapshot(
        {"id": "7", "filled": 2, "average": 30000, "trades": [*trades, trades[0]]}, instrument
    )
    assert snapshot.filled_qty_native == 2
    assert snapshot.filled_qty_base == pytest.approx(0.0075)
    assert snapshot.filled_notional_quote == 200
    assert snapshot.avg_price == pytest.approx(26666.6666667)
    assert snapshot.fee_usd == pytest.approx(0.6)
    assert snapshot.fills[0]["realized_pnl"] == 0.001
    assert snapshot.fills[0]["realized_pnl_currency"] == "BTC"


async def test_inverse_ack_never_invents_base_or_average():
    instrument = (await _exchange())._instruments["BTC/USD:BTC"]
    snapshot = parse_order_snapshot({"filled": 2, "average": 30000, "cost": 0.0075}, instrument)
    assert snapshot.filled_qty_native == 2 and snapshot.filled_notional_quote == 200
    assert snapshot.filled_qty_base is snapshot.avg_price is None
    assert parse_order_snapshot({"amount": 2}, instrument).filled_qty_native is None
    cumulative = parse_order_snapshot({"filled": 2, "info": {"cumBase": "0.0075"}}, instrument)
    assert cumulative.avg_price == pytest.approx(26666.6666667)


async def test_snapshot_paginates_deduplicates_and_uses_inverse_native_fills():
    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    client = adapter.clients["coinm"]
    client.fetch_order.return_value = {"id": "9", "status": "closed", "filled": 2, "timestamp": 1}
    first = [{"id": str(i), "order": "other", "amount": 1, "price": 20000} for i in range(999)]
    first.append({"id": "999", "order": "9", "amount": 1, "price": 20000})
    client.fetch_my_trades.side_effect = [first, [{"id": "1000", "order": "9", "amount": 1, "price": 40000}]]
    request = OrderRequest(
        instrument.venue_symbol, "buy", 2, "limit", 40000, "fill-pages", "perp", quantity_unit="contracts"
    )
    snapshot = await adapter.fetch_order_snapshot(request, instrument, "9")
    assert snapshot.avg_price == pytest.approx(26666.6666667)
    assert client.fetch_my_trades.call_args.args[-1]["fromId"] == 1000
    assert "orderId" not in client.fetch_my_trades.call_args.args[-1]


async def test_read_only_account_and_position_require_supported_modes():
    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    account = await adapter.fetch_order_account(instrument)
    position = await adapter.fetch_order_position(instrument)
    assert account.family == "coinm" and account.available["BTC"] == 1
    assert position.qty_native == -2 and position.max_notional_quote == 100000
    adapter.clients["coinm"].fetch_position_mode.return_value = {"hedged": True}
    with pytest.raises(ValueError, match="one-way"):
        await adapter.fetch_order_account(instrument)
    linear = adapter._instruments["BTC/USDT:USDT"]
    adapter.clients["usdm"].fapiPrivateGetMultiAssetsMargin.return_value = {"multiAssetsMargin": True}
    with pytest.raises(ValueError, match="single-asset"):
        await adapter.fetch_order_account(linear)


async def test_connect_product_failure_closes_client_and_is_reported(monkeypatch):
    spot, coinm = _client("spot"), _client("coinm")
    coinm.load_markets.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(
        "src.exchange.binance._create_binance_client",
        lambda family, *args, **kwargs: {"spot": spot, "coinm": coinm}[family],
    )
    adapter = BinanceExchange("binance", _config(["spot", "coinm"]), {})
    await adapter.connect()
    assert list(adapter.clients) == ["spot"] and "coinm" in adapter.family_errors
    assert coinm.close.await_count == 1
    with pytest.raises(ValueError):
        await adapter.fetch_balance({"account_family": "coinm"})
    await adapter.close()
    assert spot.close.await_count == 1


async def test_shared_contract_transport_budget_observes_both_clients():
    budget = _BinanceRateBudget()
    active = 0
    maximum_active = 0

    async def request(*args):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return {}

    clients = [
        SimpleNamespace(
            fetch2=request,
            calculate_rate_limiter_cost=lambda *args: 1,
            last_response_headers={"x-mbx-used-weight-1m": "20"},
        )
        for _ in range(2)
    ]
    for client in clients:
        _attach_binance_budget(client, budget)
    await asyncio.gather(*(client.fetch2("order", "fapiPrivate", "POST") for client in clients))
    assert maximum_active == 1
    assert budget.usage[("weight", 60)][1] >= 20
    assert budget.usage[("orders", 60)][1] == 2


async def test_full_position_query_keeps_unknown_exposure_visible():
    adapter = await _exchange()
    adapter.clients["usdm"].fetch_positions.return_value.append(
        {"symbol": "OLD/USD:OLD-DELIVERY", "contracts": 3, "markPrice": 7, "info": {"positionAmt": "3"}}
    )
    positions = await adapter.fetch_order_positions()
    assert {position.symbol for position in positions} == {"BTC/USDT:USDT", "BTC/USD:BTC", "OLD/USD:OLD-DELIVERY"}
    for family in ("usdm", "coinm"):
        assert adapter.clients[family].fetch_positions.call_args.args[0] is None


async def test_private_ws_drops_other_family_before_ccxt_parse():
    from unittest.mock import Mock

    from src.exchange.binance_clients import _filter_binance_order_stream

    client = SimpleNamespace(
        handle_order_update=Mock(), markets_by_id={"RAW_LINEAR": [_market("usdm")], "RAW_INVERSE": [_market("coinm")]}
    )
    original = client.handle_order_update
    _filter_binance_order_stream(client, "coinm")
    client.handle_order_update(None, {"o": {"s": "RAW_LINEAR"}})
    original.assert_not_called()
    event = {"o": {"s": "RAW_INVERSE"}}
    client.handle_order_update(None, event)
    original.assert_called_once_with(None, event)


async def test_post_ack_authentication_failure_preserves_known_fill():
    from ccxt.base.errors import AuthenticationError

    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    client = adapter.clients["coinm"]
    client.create_order.return_value = {"id": "accepted", "status": "closed", "filled": 2, "info": {"cumBase": "0.01"}}
    client.fetch_order.side_effect = AuthenticationError("query permission changed")
    request = OrderRequest(
        instrument.venue_symbol, "buy", 2, "limit", 20000, "post-ack", "perp", "IOC", quantity_unit="contracts"
    )
    snapshot = await adapter.submit_order(request, instrument)
    assert snapshot.status == "unknown"
    assert snapshot.filled_qty_native == 2
    assert snapshot.order_id == "accepted"
    assert client.create_order.await_count == 1


async def test_incomplete_fill_ledger_remains_unknown_even_with_cumulative_base():
    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    client = adapter.clients["coinm"]
    client.fetch_order.return_value = {"id": "accepted", "status": "closed", "filled": 2, "info": {"cumBase": "0.01"}}
    request = OrderRequest(
        instrument.venue_symbol, "buy", 2, "limit", 20000, "ledger", "perp", quantity_unit="contracts"
    )
    snapshot = await adapter.fetch_order_snapshot(request, instrument, "accepted")
    assert snapshot.status == "unknown" and snapshot.filled_qty_native == 2


async def test_fee_estimate_change_does_not_prevent_recovery_of_same_contract():
    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    previous = replace(instrument, taker_fee_rate=0.0017, maker_fee_rate=0.0007)
    assert adapter.order_capabilities(previous).has_client_order_id


async def test_market_order_protection_capabilities_follow_metadata():
    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    adapter.clients["coinm"].markets[instrument.venue_symbol]["info"] = {
        "timeInForce": ["GTC"],
        "orderTypes": ["LIMIT"],
    }
    assert adapter.order_capabilities(instrument).time_in_force == ("GTC",)


async def test_invalid_account_facts_fail_closed():
    adapter = await _exchange()
    instrument = adapter._instruments["BTC/USD:BTC"]
    adapter.clients["coinm"].fetch_balance.return_value = {"free": {"BTC": float("nan")}}
    with pytest.raises(ValueError, match="invalid available balance"):
        await adapter.fetch_order_account(instrument)
    adapter.clients["coinm"].fetch_positions.return_value[0]["markPrice"] = float("nan")
    with pytest.raises(ValueError, match="invalid position markPrice"):
        await adapter.fetch_order_position(instrument)


async def test_spot_public_http_requests_have_no_internal_routing_parameters():
    """Exercise real CCXT serialization; a mock high-level client misses leaks."""
    from urllib.parse import parse_qs, urlparse

    client = _create_binance_client("spot", NetworkType.TESTNET)
    market = _market(
        "spot", id="BTCUSDT", spot=True, swap=False, future=False, option=False, contract=False, settle=None, info={}
    )
    client.set_markets([market])
    requests = []

    async def fetch(url, method="GET", headers=None, body=None):
        parsed = urlparse(url)
        requests.append((parsed.path, parse_qs(parsed.query)))
        if parsed.path.endswith("depth"):
            return {"lastUpdateId": 1, "bids": [["20000", "1"]], "asks": [["20001", "1"]]}
        if parsed.path.endswith("klines"):
            return [[1000, "20000", "20001", "19999", "20000", "1", 2000, "20000", 1, "1", "20000", "0"]]
        return {"symbol": "BTCUSDT", "lastPrice": "20000"}

    client.fetch = fetch
    adapter = BinanceExchange("binance", _config(["spot"]), {})
    adapter.clients = {"spot": client}
    try:
        instrument = (await adapter.list_markets())[0]
        routing = adapter.account_params(instrument)
        await adapter.fetch_orderbook(instrument.venue_symbol, 5, routing)
        await adapter.fetch_ticker(instrument.venue_symbol, routing)
        await adapter.fetch_ohlcv(instrument.venue_symbol, "1m", limit=1, params=routing)
    finally:
        await adapter.close()
    assert requests == [
        ("/api/v3/depth", {"symbol": ["BTCUSDT"], "limit": ["5"]}),
        ("/api/v3/ticker/24hr", {"symbol": ["BTCUSDT"]}),
        ("/api/v3/klines", {"interval": ["1m"], "limit": ["1"], "symbol": ["BTCUSDT"]}),
    ]


@pytest.mark.parametrize("family,prefix", [("spot", "private"), ("usdm", "fapiPrivate"), ("coinm", "dapiPrivate")])
async def test_fixed_clients_route_balance_without_wire_type_overrides(family, prefix):
    client = _create_binance_client(family, NetworkType.TESTNET)
    market = _market(
        family,
        id="SYNTHETIC",
        spot=family == "spot",
        swap=family != "spot",
        future=False,
        option=False,
        contract=family != "spot",
        info={},
    )
    client.set_markets([market])
    requests = []

    async def request(path, api="public", method="GET", params=None, **kwargs):
        requests.append((path, api, params or {}))
        return {"balances": [], "assets": [], "positions": []}

    client.request = request
    adapter = BinanceExchange("binance", _config([family]), {})
    adapter.clients = {family: client}
    try:
        instrument = (await adapter.list_markets())[0]
        await adapter.fetch_balance(adapter.account_params(instrument))
    finally:
        await adapter.close()
    assert len(requests) == 1
    assert requests[0][1].startswith(prefix)
    assert not ({"type", "subType", "account_family", "maxRetriesOnFailure"} & requests[0][2].keys())
