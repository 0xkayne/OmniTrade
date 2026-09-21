import asyncio

import pytest

from src.exchange.arcus import ArcusExchange


def _config():
    return {
        "type": "native",
        "default_network": "testnet",
        "networks": {
            "testnet": {"rest_base_url": "https://testnet.local", "websocket_url": "wss://testnet.local/v1/ws"},
            "mainnet": {"rest_base_url": "https://mainnet.local", "websocket_url": "wss://mainnet.local/v1/ws"},
        },
    }


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, value):
        self.sent.append(value)


def _exchange(address="0x" + "22" * 20):
    exchange = ArcusExchange("arcus", _config(), {"address": address, "api_key": "aa"})
    market = exchange._parse_market({
        "marketId": 1,
        "baseAsset": "BTC",
        "quoteAsset": "USD",
        "marketDisplayName": "BTC-USD",
        "tickSize": "0.1",
        "stepSize": "0.001",
    })
    exchange._markets_by_symbol["BTC-USD"] = market
    exchange._markets_by_id[1] = market
    exchange._market_meta["BTC-USD"] = {"marketId": 1}
    exchange._websocket = FakeWebSocket()
    exchange._ws_queue = asyncio.Queue()
    return exchange


@pytest.mark.asyncio
async def test_subscriptions_use_arcus_id_and_contents_envelope():
    exchange = _exchange()
    await exchange.subscribe_orderbook("BTC-USD", {"nLevels": 5})
    await exchange.subscribe_orders("BTC-USD")
    await exchange.subscribe_user_fills("BTC-USD", {"nFills": 20})
    assert '"id":"BTC-USD"' in exchange._websocket.sent[0]
    assert '"market"' not in exchange._websocket.sent[0]
    assert '"channel":"userFills"' in exchange._websocket.sent[2]
    assert '"nFills":20' in exchange._websocket.sent[2]


@pytest.mark.asyncio
async def test_watch_order_book_reconciles_delta_and_resubscribes_on_gap():
    exchange = _exchange()
    await exchange._ws_queue.put({
        "channel": "l2OrderbookUpdates", "type": "subscribed", "contents": {
            "bids": [["100", "1"]], "asks": [["101", "2"]], "lastSequenceId": 10,
        }
    })
    await exchange._ws_queue.put({
        "channel": "l2OrderbookUpdates", "type": "channel_data", "contents": {
            "bids": [["100", "0.5"]], "asks": [], "lastSequenceId": 12,
        }
    })
    await exchange._ws_queue.put({
        "channel": "l2OrderbookUpdates", "type": "subscribed", "contents": {
            "bids": [["99", "3"]], "asks": [["102", "1"]], "lastSequenceId": 20,
        }
    })
    first = await exchange.watch_order_book("BTC-USD")
    assert first["lastSequenceId"] == 10
    second = await exchange.watch_order_book("BTC-USD")
    assert second["lastSequenceId"] == 20
    result = second
    assert result["lastSequenceId"] == 20
    assert result["bids"] == [[99.0, 3.0]]
    # The second watch call reuses the live subscription; only a sequence gap
    # triggers a new subscription.
    assert len(exchange._websocket.sent) == 2
    assert '"channel":"l2OrderbookUpdates"' in exchange._websocket.sent[0]


@pytest.mark.asyncio
async def test_watch_user_fills_normalizes_trade_fields():
    exchange = _exchange()
    await exchange._ws_queue.put({
        "channel": "userFills", "type": "channel_data", "market": "BTC-USD",
        "contents": [{"tradeId": "t-1", "orderId": "o-1", "size": "0.25", "price": "100.5", "createdAt": 123}],
    })
    fill = await exchange.watch_user_fills("BTC-USD")
    assert fill["id"] == "t-1"
    assert fill["amount"] == 0.25
    assert fill["price"] == 100.5
    assert fill["timestamp"] == 123


@pytest.mark.asyncio
async def test_watch_user_fills_skips_empty_subscription_snapshot():
    exchange = _exchange()
    await exchange._ws_queue.put({
        "channel": "userFills", "type": "subscribed", "market": "BTC-USD",
        "contents": {"isSnapshot": True, "fills": []},
    })
    await exchange._ws_queue.put({
        "channel": "userFills", "type": "channel_data", "market": "BTC-USD",
        "contents": {"tradeId": "live-1", "size": "0.1", "price": "100.0"},
    })

    fill = await exchange.watch_user_fills("BTC-USD")

    assert fill["tradeId"] == "live-1"


@pytest.mark.asyncio
async def test_watch_user_fills_deduplicates_trade_ids():
    exchange = _exchange()
    event = {
        "channel": "userFills", "type": "channel_data", "market": "BTC-USD",
        "contents": [{"tradeId": "same", "size": "0.25", "price": "100.5"}],
    }
    await exchange._ws_queue.put(event)
    await exchange._ws_queue.put(event)
    first = await exchange.watch_user_fills("BTC-USD")
    assert first["tradeId"] == "same"
    # The duplicate remains queued but is skipped by the second watch call;
    # add a fresh event to make the expected next result explicit.
    await exchange._ws_queue.put({
        "channel": "userFills", "type": "channel_data", "contents": [
            {"tradeId": "next", "size": "0.1", "price": "100.0"}
        ],
    })
    second = await exchange.watch_user_fills("BTC-USD")
    assert second["tradeId"] == "next"


@pytest.mark.asyncio
async def test_fetch_order_by_client_id_uses_orders_history(monkeypatch):
    exchange = _exchange()
    calls = []

    async def fake_request(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        return {"orders": [{"orderId": "o-3", "clientId": "client-3", "state": "FILLED", "filledSize": "1"}]}

    monkeypatch.setattr(exchange, "_request", fake_request)
    order = await exchange.fetch_order_by_client_id("client-3", "BTC-USD")
    assert order["id"] == "o-3"
    assert order["status"] == "closed"
    assert order["filled"] == 1.0
    assert calls[0][1] == "/v1/orders"
    assert calls[0][2]["params"]["market"] == "BTC-USD"


def test_arcus_order_wire_fields_are_normalized():
    order = ArcusExchange._normalize_order({
        "orderId": "o-4", "clientId": "c-4", "state": "FILLED",
        "filledSize": "0.4", "avgFillPrice": "101.25", "marketDisplayName": "BTC-USD",
    })
    assert order == {
        "orderId": "o-4", "clientId": "c-4", "state": "FILLED", "filledSize": "0.4",
        "avgFillPrice": "101.25", "marketDisplayName": "BTC-USD", "id": "o-4",
        "clientOrderId": "c-4", "symbol": "BTC-USD", "status": "closed", "filled": 0.4,
        "average": 101.25,
    }
