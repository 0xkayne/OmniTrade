import asyncio
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.exchange.arcus import ArcusExchange, ArcusSigner


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


def _exchange(master_wallet_address="0x" + "22" * 20, api_signing_key=None):
    exchange = ArcusExchange(
        "arcus",
        _config(),
        {"master_wallet_address": master_wallet_address, "api_key": "aa", "api_signing_key": api_signing_key},
    )
    market = exchange._parse_market(
        {
            "marketId": 1,
            "baseAsset": "BTC",
            "quoteAsset": "USD",
            "marketDisplayName": "BTC-USD",
            "tickSize": "0.1",
            "stepSize": "0.001",
        }
    )
    exchange._markets_by_symbol["BTC-USD"] = market
    exchange._markets_by_id[1] = market
    exchange._market_meta["BTC-USD"] = {"marketId": 1}
    exchange._websocket = FakeWebSocket()
    exchange._ws_queue = asyncio.Queue()
    return exchange


@pytest.mark.asyncio
async def test_subscriptions_use_arcus_id_and_contents_envelope():
    master_wallet_address = "0x" + "33" * 20
    exchange = _exchange(master_wallet_address)
    await exchange.subscribe_orderbook("BTC-USD", {"nLevels": 5})
    await exchange.subscribe_orders("BTC-USD")
    await exchange.subscribe_user_fills("BTC-USD", {"nFills": 20})
    assert '"id":"BTC-USD"' in exchange._websocket.sent[0]
    assert '"market"' not in exchange._websocket.sent[0]
    assert '"channel":"userFills"' in exchange._websocket.sent[2]
    assert '"nFills":20' in exchange._websocket.sent[2]
    for message in exchange._websocket.sent[1:]:
        subscription = json.loads(message)
        assert subscription["id"] == master_wallet_address
        assert "master_wallet_address" not in subscription


@pytest.mark.asyncio
async def test_master_wallet_address_drives_rest_account_and_signed_order(monkeypatch):
    signing_key = Ed25519PrivateKey.generate()
    seed = signing_key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    ).hex()
    master_wallet_address = "0x" + "Ab" * 20
    exchange = _exchange(master_wallet_address, seed)
    calls = []

    async def fake_request(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        if endpoint == "/v1/account":
            return {"address": master_wallet_address, "accountIndex": 0, "freeCollateral": "10.25", "equity": "12"}
        return {"orderId": "o-1", "status": "OPEN"}

    monkeypatch.setattr(exchange, "_request", fake_request)
    monkeypatch.setattr("src.exchange.arcus.time.time_ns", lambda: 123)
    balance = await exchange.fetch_balance()
    order = await exchange.create_order(
        "BTC-USD", "limit", "buy", 0.002, 100.1, {"timeInForce": "IOC", "goodTilTime": 1000}
    )

    assert balance["free"]["USD"] == 10.25
    assert balance["total"]["USD"] == 12
    assert order["id"] == "o-1"
    assert calls[0][:2] == ("GET", "/v1/account")
    assert calls[0][2]["params"] == {"address": master_wallet_address, "accountIndex": 0}
    assert calls[1][:2] == ("POST", "/v1/placeOrder")
    request = calls[1][2]
    assert request["params"] == {"address": master_wallet_address}
    assert request["json"]["address"] == master_wallet_address
    assert "master_wallet_address" not in request["json"]
    signed_payload = {
        "ad": master_wallet_address.lower(),
        "ai": 0,
        "ct": 123,
        "g": 1000000,
        "m": 1,
        "op": 1,
        "p": 1001,
        "q": 2,
        "r": 0,
        "s": 0,
        "t": 2,
        "v": 1,
    }
    signature = bytes.fromhex(request["json"]["signature"])
    signing_key.public_key().verify(signature, ArcusSigner.canonical_json(signed_payload))
    assert request["headers"]["X-Signature"] == signature.hex()


@pytest.mark.asyncio
async def test_watch_order_book_reconciles_delta_and_resubscribes_on_gap():
    exchange = _exchange()
    await exchange._ws_queue.put(
        {
            "channel": "l2OrderbookUpdates",
            "type": "subscribed",
            "contents": {
                "bids": [["100", "1"]],
                "asks": [["101", "2"]],
                "lastSequenceId": 10,
            },
        }
    )
    await exchange._ws_queue.put(
        {
            "channel": "l2OrderbookUpdates",
            "type": "channel_data",
            "contents": {
                "bids": [["100", "0.5"]],
                "asks": [],
                "lastSequenceId": 12,
            },
        }
    )
    await exchange._ws_queue.put(
        {
            "channel": "l2OrderbookUpdates",
            "type": "subscribed",
            "contents": {
                "bids": [["99", "3"]],
                "asks": [["102", "1"]],
                "lastSequenceId": 20,
            },
        }
    )
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
async def test_reconnect_preserves_waiter_and_discards_stale_book_until_snapshot(monkeypatch):
    from unittest.mock import AsyncMock

    class Socket(FakeWebSocket):
        def __init__(self):
            super().__init__()
            self.incoming = asyncio.Queue()

        async def recv(self):
            return await self.incoming.get()

        async def close(self):
            pass

    exchange = _exchange()
    old_socket = Socket()
    new_socket = Socket()
    exchange._websocket = old_socket
    await exchange.subscribe_orderbook_updates("BTC-USD")
    exchange._orderbook_sequences["BTC-USD"] = 10
    exchange._orderbook_books["BTC-USD"] = {"bids": [[100, 1]], "asks": [[101, 1]]}
    exchange._orderbook_needs_snapshot.clear()
    waiting = asyncio.create_task(exchange.watch_order_book("BTC-USD"))
    await asyncio.sleep(0)
    original_queue = exchange._ws_queues["l2OrderbookUpdates"]
    monkeypatch.setattr("websockets.connect", AsyncMock(return_value=new_socket))
    try:
        await exchange._reconnect_ws()
        assert exchange._ws_queues["l2OrderbookUpdates"] is original_queue
        assert exchange._orderbook_books == {}
        # A delta arriving before the replacement snapshot must not stand in for a full book.
        for event_type, sequence, bids, asks in [
            ("channel_data", 11, [["100", "0.5"]], []),
            ("subscribed", 20, [["99", "2"]], [["102", "3"]]),
        ]:
            await new_socket.incoming.put(
                json.dumps(
                    {
                        "channel": "l2OrderbookUpdates",
                        "type": event_type,
                        "id": "BTC-USD",
                        "contents": {"lastSequenceId": sequence, "bids": bids, "asks": asks},
                    }
                )
            )
        result = await asyncio.wait_for(waiting, 1)
        assert result["lastSequenceId"] == 20
        assert result["bids"] == [[99, 2]]
        assert result["asks"] == [[102, 3]]
        assert len(new_socket.sent) == 1
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await exchange.close()


@pytest.mark.asyncio
async def test_watch_user_fills_normalizes_trade_fields():
    exchange = _exchange()
    await exchange._ws_queue.put(
        {
            "channel": "userFills",
            "type": "channel_data",
            "market": "BTC-USD",
            "contents": [{"tradeId": "t-1", "orderId": "o-1", "size": "0.25", "price": "100.5", "createdAt": 123}],
        }
    )
    fill = await exchange.watch_user_fills("BTC-USD")
    assert fill["id"] == "t-1"
    assert fill["amount"] == 0.25
    assert fill["price"] == 100.5
    assert fill["timestamp"] == 0.123  # Arcus microseconds become CCXT milliseconds.


@pytest.mark.asyncio
async def test_watch_user_fills_skips_empty_subscription_snapshot():
    exchange = _exchange()
    await exchange._ws_queue.put(
        {
            "channel": "userFills",
            "type": "subscribed",
            "market": "BTC-USD",
            "contents": {"isSnapshot": True, "fills": []},
        }
    )
    await exchange._ws_queue.put(
        {
            "channel": "userFills",
            "type": "channel_data",
            "market": "BTC-USD",
            "contents": {"tradeId": "live-1", "size": "0.1", "price": "100.0"},
        }
    )

    fill = await exchange.watch_user_fills("BTC-USD")

    assert fill["tradeId"] == "live-1"


@pytest.mark.asyncio
async def test_watch_user_fills_deduplicates_trade_ids():
    exchange = _exchange()
    event = {
        "channel": "userFills",
        "type": "channel_data",
        "market": "BTC-USD",
        "contents": [{"tradeId": "same", "size": "0.25", "price": "100.5"}],
    }
    await exchange._ws_queue.put(event)
    await exchange._ws_queue.put(event)
    first = await exchange.watch_user_fills("BTC-USD")
    assert first["tradeId"] == "same"
    # The duplicate remains queued but is skipped by the second watch call;
    # add a fresh event to make the expected next result explicit.
    await exchange._ws_queue.put(
        {
            "channel": "userFills",
            "type": "channel_data",
            "contents": [{"tradeId": "next", "size": "0.1", "price": "100.0"}],
        }
    )
    second = await exchange.watch_user_fills("BTC-USD")
    assert second["tradeId"] == "next"


@pytest.mark.asyncio
async def test_account_stream_batches_deliver_every_order_and_fill():
    exchange = _exchange()
    await exchange._ws_queue.put(
        {
            "channel": "orders",
            "contents": {
                "orders": [
                    {"orderId": "o-1", "status": "OPEN"},
                    {"orderId": "o-2", "status": "CANCELED"},
                ]
            },
        }
    )
    assert (await exchange.watch_orders("BTC-USD"))["id"] == "o-1"
    assert (await exchange.watch_orders("BTC-USD"))["id"] == "o-2"
    await exchange._ws_queue.put(
        {
            "channel": "userFills",
            "contents": {
                "isSnapshot": True,
                "fills": [
                    {"tradeId": "t-1", "size": "0.1", "price": "100"},
                    {"tradeId": "t-2", "size": "0.2", "price": "101"},
                ],
            },
        }
    )
    assert (await exchange.watch_user_fills("BTC-USD"))["id"] == "t-1"
    assert (await exchange.watch_user_fills("BTC-USD"))["id"] == "t-2"


@pytest.mark.asyncio
async def test_account_stream_rejects_other_subaccount():
    exchange = _exchange()
    await exchange._ws_queue.put({"channel": "orders", "accountIndex": 1, "contents": {"orders": []}})
    with pytest.raises(ValueError, match="another account"):
        await exchange.watch_orders("BTC-USD")


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
    order = ArcusExchange._normalize_order(
        {
            "orderId": "o-4",
            "clientId": "c-4",
            "state": "FILLED",
            "filledSize": "0.4",
            "avgFillPrice": "101.25",
            "marketDisplayName": "BTC-USD",
        }
    )
    assert order == {
        "orderId": "o-4",
        "clientId": "c-4",
        "state": "FILLED",
        "filledSize": "0.4",
        "avgFillPrice": "101.25",
        "marketDisplayName": "BTC-USD",
        "id": "o-4",
        "clientOrderId": "c-4",
        "symbol": "BTC-USD",
        "status": "closed",
        "filled": 0.4,
        "average": 101.25,
    }
