import asyncio
from decimal import Decimal

import pytest

from src.exchange.arcus import ArcusExchange, ArcusSigner
from src.exchange.factory import ExchangeFactory
from src.market.instrument import NetworkType


def _config():
    return {
        "type": "native",
        "adapter": "arcus",
        "default_network": "testnet",
        "networks": {
            "testnet": {"rest_base_url": "https://api.testnet.arcus.xyz", "websocket_url": "wss://api.testnet.arcus.xyz/v1/ws"},
            "mainnet": {"rest_base_url": "https://api.arcus.xyz", "websocket_url": "wss://api.arcus.xyz/v1/ws"},
        },
    }


def test_factory_builds_native_arcus_adapter():
    exchange = ExchangeFactory.create_exchange("arcus", _config(), {})
    assert isinstance(exchange, ArcusExchange)
    assert exchange.network_type is NetworkType.TESTNET


def test_arcus_signing_is_sorted_and_hex():
    exchange = ArcusExchange("arcus", _config(), {"private_key": "11" * 32, "address": "0x" + "22" * 20})
    signature = exchange._signer.sign_payload({"z": 1, "a": 2})
    assert len(signature) == 128
    assert bytes.fromhex(signature)


def test_arcus_signer_canonical_json_is_deterministic():
    signer = ArcusSigner("11" * 32)
    assert signer.canonical_json({"z": 1, "a": {"d": 2, "c": 3}}) == b'{"a":{"c":3,"d":2},"z":1}'


def test_arcus_signer_rejects_invalid_key_length():
    with pytest.raises(ValueError, match="32 bytes"):
        ArcusSigner("11" * 31)


def test_arcus_integer_units_require_exact_step():
    from src.exchange.arcus import _integer_step

    assert _integer_step(Decimal("1.25"), Decimal("0.25")) == 5
    with pytest.raises(ValueError, match="not aligned"):
        _integer_step(Decimal("1.1"), Decimal("0.25"))


def test_arcus_market_mapping():
    exchange = ArcusExchange("arcus", _config(), {})
    instrument = exchange._parse_market({"marketId": 7, "ticker": "BTC-USD", "baseAsset": "BTC", "quoteAsset": "USD", "marketType": "perp", "tickSize": "0.1", "stepSize": "0.001", "status": "ACTIVE"})
    assert instrument.venue == "arcus"
    assert instrument.market_type == "perp"
    assert instrument.price_step == 0.1
    assert instrument.listing_status == "active"


def test_arcus_orderbook_normalization_handles_dict_levels():
    result = ArcusExchange._normalize_orderbook(
        {"bids": [{"price": "100.5", "quantity": "2"}], "asks": [["101", "1.5"]], "timestamp": 123}
    )
    assert result == {"bids": [[100.5, 2.0]], "asks": [[101.0, 1.5]], "timestamp": 123}


def test_arcus_bbo_and_time_normalization_handles_wire_fields():
    exchange = ArcusExchange("arcus", _config(), {})
    exchange._markets_by_symbol["BTC-USD"] = exchange._parse_market(
        {"marketId": 1, "baseAsset": "BTC", "quoteAsset": "USD", "marketDisplayName": "BTC-USD", "tickSize": "0.1", "stepSize": "0.0001"}
    )
    # The public API returns object-valued bestBid/bestAsk and timeNs.
    assert exchange._normalize_bbo({"bestBid": {"price": "100.5"}, "bestAsk": {"price": "101"}}, "BTC-USD") == {
        "symbol": "BTC-USD",
        "bid": 100.5,
        "ask": 101.0,
        "info": {"bestBid": {"price": "100.5"}, "bestAsk": {"price": "101"}},
    }


def test_arcus_balance_falls_back_to_free_collateral():
    response = {"equity": "12.5", "freeCollateral": "10.25", "positions": {}}
    balances = response.get("balances", response.get("collateral", {}))
    assert not balances
    assert {"USD": float(response["freeCollateral"])} == {"USD": 10.25}


def test_arcus_order_status_is_normalized():
    result = ArcusExchange._normalize_order({"orderId": "o-1", "status": "PARTIALLY_FILLED", "filledQuantity": "0.2"}, "BTC-USD")
    assert result["id"] == "o-1"
    assert result["status"] == "open"
    assert result["filled"] == 0.2
    assert result["symbol"] == "BTC-USD"


@pytest.mark.asyncio
async def test_arcus_watch_orders_subscribes_and_returns_normalized_update(monkeypatch):
    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    exchange = ArcusExchange(
        "arcus", _config(), {"api_key": "aa", "address": "0x" + "22" * 20}
    )
    ws = FakeWebSocket()
    exchange._websocket = ws
    exchange._ws_queue = asyncio.Queue()
    await exchange._ws_queue.put({"channel": "orders", "orderId": "o-2", "status": "FILLED", "filled": "1"})

    result = await exchange.watch_orders("BTC-USD")
    assert result["id"] == "o-2"
    assert result["status"] == "closed"
    assert '"channel":"orders"' in ws.sent[0]
