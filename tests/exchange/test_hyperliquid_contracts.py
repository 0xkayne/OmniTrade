"""Offline Hyperliquid account and Pro subscription protocol regressions."""

import asyncio
import time
from unittest.mock import AsyncMock

import ccxt.async_support as ccxt
import pytest
from ccxt.async_support.base.ws.cache import ArrayCacheBySymbolById
from ccxt.async_support.base.ws.client import Client

from src.exchange.ccxt import CCXTExchange
from src.exchange.order import OrderRequest


def _adapter(network="testnet"):
    config = {
        "default_network": network,
        "networks": {
            "testnet": {"rest_base_url": "https://api.hyperliquid-testnet.xyz"},
            "mainnet": {"rest_base_url": "https://api.hyperliquid.xyz"},
        },
    }
    adapter = CCXTExchange("hyperliquid", config, {"master_wallet_address": "0x" + "12" * 20})
    client = ccxt.hyperliquid(adapter._build_ccxt_config())
    markets = []
    for is_swap in (False, True):
        markets.append(
            {
                "id": "BTC/USDC:USDC" if is_swap else "@1",
                "symbol": "BTC/USDC:USDC" if is_swap else "BTC/USDC",
                "base": "BTC",
                "baseId": "0",
                "baseName": "BTC",
                "quote": "USDC",
                "settle": "USDC" if is_swap else None,
                "active": True,
                "type": "swap" if is_swap else "spot",
                "spot": not is_swap,
                "swap": is_swap,
                "linear": is_swap,
                "inverse": False,
                "contract": is_swap,
                "contractSize": 1 if is_swap else None,
                "precision": {"amount": 0.001, "price": 1},
                "info": {"szDecimals": 3},
            }
        )
    client.set_markets(markets)
    client.fetch = AsyncMock(side_effect=AssertionError("network transport is forbidden"))
    adapter.ccxt_exchange = client
    return adapter


async def _instrument(adapter, product="perp"):
    return next(item for item in await adapter.list_markets() if item.market_type == product)


def _state(*positions):
    return {"assetPositions": list(positions), "time": int(time.time() * 1000)}


def _position(qty="-0.25", mode="isolated"):
    return {
        "type": "oneWay",
        "position": {
            "coin": "BTC",
            "szi": qty,
            "entryPx": "60000",
            "positionValue": "15500",
            "leverage": {"type": mode, "value": 3},
            "marginUsed": "5000",
            "unrealizedPnl": "-500",
        },
    }


@pytest.mark.parametrize(
    ("product", "abstraction", "endpoint", "mode", "portfolio"),
    [
        ("spot", "disabled", "spotClearinghouseState", "single_asset", False),
        ("perp", "disabled", "clearinghouseState", "single_asset", False),
        ("perp", '"unifiedAccount"', "spotClearinghouseState", "unified", False),
        ("perp", "portfolioMargin", "spotClearinghouseState", "portfolio", True),
    ],
)
async def test_account_queries_pin_master_and_preserve_account_mode(product, abstraction, endpoint, mode, portfolio):
    adapter = _adapter()
    response = (
        {"balances": [{"coin": "USDC", "total": "1000", "hold": "12"}]}
        if endpoint == "spotClearinghouseState"
        else {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "12"}, "withdrawable": "0"}
    )
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(side_effect=[abstraction, response])
    try:
        account = await adapter.fetch_order_account(await _instrument(adapter, product))
        assert account.family == product
        assert account.available["USDC"] == 988
        assert account.margin_mode == mode
        assert account.is_portfolio_margin is portfolio
        assert time.time() - account.timestamp < 1
        calls = adapter.ccxt_exchange.publicPostInfo.call_args_list
        assert calls[0].args[0] == {"type": "userAbstraction", "user": adapter.ccxt_exchange.walletAddress}
        assert calls[1].args[0] == {"type": endpoint, "user": adapter.ccxt_exchange.walletAddress}
    finally:
        await adapter.close()


async def test_account_query_does_not_default_to_standard_mode_on_failure():
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(side_effect=ccxt.NetworkError("offline failure"))
    try:
        with pytest.raises(ccxt.NetworkError):
            await adapter.fetch_order_account(await _instrument(adapter))
    finally:
        await adapter.close()


async def test_empty_spot_account_is_distinct_from_a_failed_account_query():
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(side_effect=["disabled", {"balances": []}])
    try:
        account = await adapter.fetch_order_account(await _instrument(adapter, "spot"))
        assert account.available == {}
        assert account.margin_mode == "single_asset"
    finally:
        await adapter.close()


@pytest.mark.parametrize(("qty", "mode"), [("-0.25", "isolated"), ("0.25", "cross")])
async def test_position_retains_signed_native_size_and_derives_mark_from_position_value(qty, mode):
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(return_value=_state(_position(qty, mode)))
    try:
        instrument = await _instrument(adapter)
        position = await adapter.fetch_order_position(instrument)
        assert adapter.order_capabilities(instrument).has_position_validation
        assert position.qty_native == float(qty)
        assert position.entry_price == 60000
        assert position.mark_price == 62000
        assert position.leverage == 3
        assert position.margin_mode == mode
        assert time.time() - position.timestamp < 1
        adapter.ccxt_exchange.publicPostInfo.assert_awaited_once_with(
            {"type": "clearinghouseState", "user": adapter.ccxt_exchange.walletAddress}
        )
    finally:
        await adapter.close()


async def test_flat_position_reads_live_leverage_and_mark_without_mutation():
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(
        side_effect=[_state(), {"coin": "BTC", "leverage": {"type": "cross", "value": 5}, "markPx": "62000"}]
    )
    adapter.ccxt_exchange.privatePostExchange = AsyncMock(side_effect=AssertionError("private actions forbidden"))
    try:
        position = await adapter.fetch_order_position(await _instrument(adapter))
        assert (position.qty_native, position.entry_price, position.leverage, position.mark_price) == (
            0,
            None,
            5,
            62000,
        )
        assert adapter.ccxt_exchange.publicPostInfo.call_args.args[0] == {
            "type": "activeAssetData",
            "user": adapter.ccxt_exchange.walletAddress,
            "coin": "BTC",
        }
        adapter.ccxt_exchange.privatePostExchange.assert_not_called()
    finally:
        await adapter.close()


async def test_legacy_position_and_open_order_wrappers_do_not_leak_product_type_to_info():
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(side_effect=[_state(_position()), []])
    try:
        positions = await adapter.fetch_positions(["BTC/USDC:USDC"], {"type": "swap"})
        assert positions[0]["side"] == "short"
        assert await adapter.fetch_open_orders("BTC/USDC:USDC", params={"type": "swap"}) == []
        assert [call.args[0]["type"] for call in adapter.ccxt_exchange.publicPostInfo.call_args_list] == [
            "clearinghouseState",
            "frontendOpenOrders",
        ]
    finally:
        await adapter.close()


async def test_cancel_success_ack_requires_separate_terminal_query():
    adapter = _adapter()
    adapter.ccxt_exchange.cancel_order = AsyncMock(return_value={"info": "success", "status": "success"})
    adapter.ccxt_exchange.fetch_order = AsyncMock(return_value={"id": "123", "status": "open", "filled": 0})
    try:
        assert await adapter.cancel_order("123", "BTC/USDC:USDC") is True
        assert (await adapter.fetch_order("123", "BTC/USDC:USDC"))["status"] == "open"
    finally:
        await adapter.close()


async def test_complete_positions_scan_includes_all_perpetual_dexes_and_rejects_unknown_holdings():
    adapter = _adapter()
    unknown = _position()
    unknown["position"]["coin"] = "other:XYZ"
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(
        side_effect=[[None, {"name": "other"}], _state(_position()), _state(unknown)]
    )
    try:
        with pytest.raises(ValueError, match="no loaded instrument"):
            await adapter.fetch_order_positions()
        assert adapter.ccxt_exchange.publicPostInfo.call_args.args[0]["dex"] == "other"
    finally:
        await adapter.close()


@pytest.mark.parametrize("state", [{}, {"assetPositions": [], "time": 1}])
async def test_missing_or_stale_position_state_is_not_reported_as_flat(state):
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(return_value=state)
    try:
        with pytest.raises(ValueError, match=r"position response|timestamp"):
            await adapter.fetch_order_position(await _instrument(adapter))
    finally:
        await adapter.close()


def _fill(*, fee="0.1", builder_fee="0.02"):
    return {
        "coin": "BTC",
        "px": "62000",
        "sz": "0.01",
        "side": "B",
        "time": 1000,
        "oid": 123,
        "tid": 321,
        "fee": fee,
        "feeToken": "USDC",
        "builderFee": builder_fee,
        "closedPnl": "-0.4",
        "crossed": True,
    }


@pytest.mark.parametrize("fee", ["0.1", "0.0", "-0.1"])
async def test_private_fills_use_venue_fee_total_without_double_counting_builder(fee):
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(return_value=[_fill(fee=fee)])
    try:
        trades = await adapter.fetch_my_trades("BTC/USDC:USDC", params={"type": "swap"})
        assert len(trades) == 1
        assert trades[0]["fee"] == {"cost": float(fee), "currency": "USDC"}
        assert trades[0]["fees"] == [trades[0]["fee"]]
        assert trades[0]["info"]["realizedPnl"] == "-0.4"
        assert adapter.ccxt_exchange.publicPostInfo.call_args.args[0]["type"] == "userFills"
    finally:
        await adapter.close()


async def test_spot_realized_pnl_keeps_quote_currency_when_fee_is_paid_in_base():
    adapter = _adapter()
    raw = dict(_fill(), coin="@1", feeToken="BTC")
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(return_value=[raw])
    try:
        trade = (await adapter.fetch_my_trades("BTC/USDC"))[0]
        assert trade["fee"]["currency"] == "BTC"
        assert trade["info"]["marginAsset"] == "USDC"
    finally:
        await adapter.close()


async def test_optional_params_support_ticker_funding_and_public_trade_queries():
    adapter = _adapter()
    market = adapter.ccxt_exchange.markets["BTC/USDC:USDC"]
    context = {
        "name": "BTC",
        "markPx": "62000",
        "midPx": "62001",
        "prevDayPx": "61000",
        "funding": "0.0001",
        "oraclePx": "61999",
        "dayNtlVlm": "1000000",
    }
    adapter.ccxt_exchange.fetch_markets = AsyncMock(return_value=[dict(market, info=context)])
    public_trade = {"coin": "BTC", "side": "B", "px": "62000", "sz": "0.01", "time": 1000, "tid": 321}
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(
        side_effect=[[{"universe": [{"name": "BTC"}]}, [context]], [public_trade]]
    )
    try:
        ticker = await adapter.fetch_ticker("BTC/USDC:USDC")
        funding = await adapter.fetch_funding_rate("BTC/USDC:USDC")
        trades = await adapter.fetch_trades("BTC/USDC:USDC", limit=5)
        assert ticker["symbol"] == "BTC/USDC:USDC"
        assert ticker["last"] == 62001
        assert funding["fundingRate"] == 0.0001
        assert trades[0]["id"] == "321"
        assert trades[0]["order"] is None
        assert adapter.ccxt_exchange.publicPostInfo.call_args_list[0].args[0] == {"type": "metaAndAssetCtxs"}
        assert adapter.ccxt_exchange.publicPostInfo.call_args_list[1].args[0] == {"type": "recentTrades", "coin": "BTC"}
    finally:
        await adapter.close()


@pytest.mark.parametrize(("symbol", "coin"), [("BTC/USDC:USDC", "BTC"), ("BTC/USDC", "@1")])
async def test_public_trades_need_no_wallet_and_filter_recent_snapshot_locally(symbol, coin):
    adapter = _adapter()
    adapter.secrets = {}
    adapter.ccxt_exchange.walletAddress = None
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(
        return_value=[
            {"coin": coin, "side": "B", "px": "62000", "sz": "0.01", "time": timestamp, "tid": timestamp}
            for timestamp in (3000, 2000, 1000)
        ]
    )
    try:
        trades = await adapter.fetch_trades(symbol, since=1500, limit=1)
        assert [trade["id"] for trade in trades] == ["3000"]
        assert trades[0]["symbol"] == symbol
        adapter.ccxt_exchange.publicPostInfo.assert_awaited_once_with({"type": "recentTrades", "coin": coin})
    finally:
        await adapter.close()


@pytest.mark.parametrize("network", ["testnet", "mainnet"])
async def test_pro_client_subscriptions_are_read_only_master_scoped_and_network_pinned(network):
    adapter = _adapter(network)
    try:
        clients = await asyncio.gather(adapter._get_ws_client(), adapter._get_ws_client())
        client = clients[0]
        assert client is clients[1]
        assert client is not adapter.ccxt_exchange
        assert client.markets is adapter.ccxt_exchange.markets
        assert client.walletAddress == adapter.ccxt_exchange.walletAddress
        assert client.options["sandboxMode"] is (network == "testnet")
        host = "api.hyperliquid-testnet.xyz" if network == "testnet" else "api.hyperliquid.xyz"
        assert client.urls["api"]["ws"]["public"] == f"wss://{host}/ws"
        assert client.urls["api"]["public"] == f"https://{host}"
        client.fetch = AsyncMock(side_effect=AssertionError("network transport is forbidden"))
        subscriptions = []

        async def watch(url, message_hash, message, *args):
            subscriptions.append(message["subscription"])
            if message["subscription"]["type"] == "l2Book":
                return client.order_book({"bids": [[62000, 1]], "asks": [[62001, 1]]})
            cache = ArrayCacheBySymbolById(100)
            cache.append(
                client.parse_trade(_fill())
                if message["subscription"]["type"] == "userFills"
                else {"id": "123", "symbol": "BTC/USDC:USDC", "status": "open"}
            )
            return cache

        client.watch = AsyncMock(side_effect=watch)
        assert (await adapter.watch_order_book("BTC/USDC:USDC", params={"type": "swap"}))["bids"]
        assert (await adapter.watch_orders("BTC/USDC:USDC", {"type": "swap"}))[0]["id"] == "123"
        fills = await adapter.watch_user_fills("BTC/USDC:USDC", {"type": "swap"})
        assert fills[0]["fees"] == [{"cost": 0.1, "currency": "USDC"}]
        assert adapter.supports_user_fills
        assert subscriptions == [
            {"type": "l2Book", "coin": "BTC"},
            {"type": "orderUpdates", "user": client.walletAddress},
            {"type": "userFills", "user": client.walletAddress},
        ]
        with pytest.raises(ccxt.NotSupported, match="read subscriptions only"):
            await adapter.create_order_ws("BTC/USDC:USDC", "limit", "buy", 1, 62000)
        close_ws, close_rest = AsyncMock(wraps=client.close), AsyncMock(wraps=adapter.ccxt_exchange.close)
        client.close = close_ws
        adapter.ccxt_exchange.close = close_rest
        await adapter.close()
        close_ws.assert_awaited_once()
        close_rest.assert_awaited_once()
        assert adapter._ws_client is None
    finally:
        await adapter.close()


def _offline_socket(client):
    """Keep CCXT watch/future/dispatch real; replace only network transport."""
    socket = Client("wss://offline.invalid", None, None, None, None)
    socket.connected.resolve(True)
    subscriptions = []

    async def send(message):
        subscription = message["subscription"]
        duplicate = subscription in subscriptions
        subscriptions.append(subscription)
        if duplicate:
            client.handle_message(socket, {"channel": "error", "data": "Subscription already exists"})

    socket.send = send
    socket.throttle = AsyncMock()
    client.open = lambda: None
    client.client = lambda url: socket
    client.check_ws_proxy_settings = lambda: (None, None, None)
    return socket, subscriptions


async def _registered_futures(socket, keys):
    async def wait():
        while not set(keys) <= socket.futures.keys():
            await asyncio.sleep(0)
        # Base.watch schedules sending via connected.add_done_callback.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 1)


def _dispatch_private_updates(client, socket, fills):
    client.handle_message(socket, {"channel": "userFills", "data": {"isSnapshot": False, "fills": fills}})
    client.handle_message(
        socket,
        {
            "channel": "orderUpdates",
            "data": [
                {
                    "order": {
                        "coin": fill["coin"],
                        "oid": fill["oid"],
                        "side": fill["side"],
                        "limitPx": fill["px"],
                        "sz": "0",
                        "origSz": fill["sz"],
                        "timestamp": fill["time"],
                    },
                    "status": "filled",
                    "statusTimestamp": fill["time"],
                }
                for fill in fills
            ],
        },
    )


async def test_private_streams_subscribe_once_when_spot_joins_after_perpetual():
    adapter = _adapter()
    client = await adapter._get_ws_client()
    socket, subscriptions = _offline_socket(client)
    tasks = []
    symbols = ["BTC/USDC:USDC", "BTC/USDC"]
    try:
        tasks = [asyncio.create_task(method(symbols[0])) for method in (adapter.watch_orders, adapter.watch_user_fills)]
        await _registered_futures(socket, ["order:" + symbols[0], "myTrades:" + symbols[0]])
        _dispatch_private_updates(client, socket, [_fill()])
        first = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert all(rows[0]["symbol"] == symbols[0] for rows in first)
        assert len(subscriptions) == 2

        # Reproduce the runner sequence: spot watchers join an account whose
        # perpetual streams already received events, while perps keep watching.
        tasks = [
            asyncio.create_task(method(symbol))
            for method in (adapter.watch_orders, adapter.watch_user_fills)
            for symbol in symbols
        ]
        await _registered_futures(socket, [prefix + symbol for prefix in ("order:", "myTrades:") for symbol in symbols])
        assert len(subscriptions) == 2
        _dispatch_private_updates(
            client,
            socket,
            [
                dict(_fill(), oid=124, tid=322),
                dict(_fill(), coin="@1", oid=125, tid=323),
            ],
        )
        updates = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert [[row["symbol"] for row in rows] for rows in updates] == [[symbol] for symbol in symbols * 2]

        # A spot consumer timeout cannot cancel the distinct perpetual future.
        tasks = [asyncio.create_task(adapter.watch_user_fills(symbol)) for symbol in symbols]
        await _registered_futures(socket, ["myTrades:" + symbol for symbol in symbols])
        tasks[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        tasks[1] = asyncio.create_task(adapter.watch_user_fills(symbols[1]))
        await _registered_futures(socket, ["myTrades:" + symbol for symbol in symbols])
        _dispatch_private_updates(
            client,
            socket,
            [
                dict(_fill(), oid=126, tid=324),
                dict(_fill(), coin="@1", oid=127, tid=325),
            ],
        )
        updates = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert [[row["symbol"] for row in rows] for rows in updates] == [[symbol] for symbol in symbols]
        assert len(subscriptions) == 2
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await adapter.close()


async def test_private_stream_server_error_rejects_with_exception_and_dispatch_can_continue():
    adapter = _adapter()
    client = await adapter._get_ws_client()
    socket, _ = _offline_socket(client)
    try:
        pending = socket.future("myTrades:BTC/USDC:USDC")
        client.handle_message(socket, {"channel": "error", "data": "rejected " + client.walletAddress})
        with pytest.raises(ccxt.ExchangeError, match="subscription rejected") as error:
            await pending
        assert client.walletAddress not in str(error.value)
        next_update = socket.future("myTrades:BTC/USDC:USDC")
        _dispatch_private_updates(client, socket, [_fill()])
        assert (await asyncio.wait_for(next_update, 1))[0]["symbol"] == "BTC/USDC:USDC"
    finally:
        await adapter.close()


async def test_protected_account_queries_reject_vault_routing_and_subscription_overrides():
    adapter = _adapter()
    try:
        with pytest.raises(ValueError, match="payload overrides"):
            await adapter.watch_orders(params={"subscription": {"user": "other"}})
        with pytest.raises(ValueError, match="payload overrides"):
            await adapter.watch_order_book("BTC/USDC", params={"method": "post"})
        adapter.secrets["vault_address"] = "0x" + "34" * 20
        with pytest.raises(ValueError, match="vault or subaccount"):
            await adapter.fetch_order_position(await _instrument(adapter))
    finally:
        await adapter.close()


@pytest.mark.parametrize("has_fills", [True, False])
async def test_order_recovery_by_client_id_requires_complete_fills_and_keeps_realized_pnl(has_fills):
    adapter = _adapter()
    client_id = "0x" + "ab" * 16
    order_response = {
        "status": "order",
        "order": {
            "status": "filled",
            "statusTimestamp": 1100,
            "order": {
                "coin": "BTC",
                "oid": 123,
                "cloid": client_id,
                "side": "B",
                "origSz": "0.01",
                "sz": "0",
                "limitPx": "63000",
                "timestamp": 1000,
                "orderType": "Limit",
                "tif": "Ioc",
            },
        },
    }
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(side_effect=[order_response, [_fill()] if has_fills else []])
    try:
        instrument = await _instrument(adapter)
        request = OrderRequest(instrument.venue_symbol, "buy", 0.01, "limit", 63000, client_id, "perp")
        snapshot = await adapter.fetch_order_snapshot(request, instrument)
        assert adapter.ccxt_exchange.publicPostInfo.call_args_list[0].args[0] == {
            "type": "orderStatus",
            "user": adapter.ccxt_exchange.walletAddress,
            "oid": client_id,
        }
        assert snapshot.status == ("closed" if has_fills else "unknown")
        if has_fills:
            assert snapshot.avg_price == 62000
            assert snapshot.fee_usd == 0.1
            assert snapshot.fills[0]["realized_pnl"] == -0.4
    finally:
        await adapter.close()


async def test_client_initialization_does_not_approve_builder_fees_or_set_referrer():
    adapter = _adapter()
    adapter.ccxt_exchange.publicPostInfo = AsyncMock(return_value='"unifiedAccount"')
    adapter.ccxt_exchange.privatePostExchange = AsyncMock(side_effect=AssertionError("private mutation forbidden"))
    try:
        assert await adapter.ccxt_exchange.initialize_client()
        assert adapter.ccxt_exchange.options["builderFee"] is False
        adapter.ccxt_exchange.privatePostExchange.assert_not_called()
    finally:
        await adapter.close()
