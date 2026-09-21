"""Normalized order amounts and fee currencies must not invent USD values."""

import pytest

from src.exchange.order import parse_order_snapshot


def test_missing_filled_stays_unknown():
    snapshot = parse_order_snapshot({"id": "1", "status": "closed", "amount": 10}, "BTC", "USDT")
    assert snapshot.filled_qty_base is None


@pytest.mark.parametrize("currency,cost,expected", [("USDT", 1, 1), ("BTC", 0.001, 50), ("BNB", 1, None)])
def test_fee_currency_conversion(currency, cost, expected):
    snapshot = parse_order_snapshot(
        {"filled": 0.1, "cost": 5000, "fee": {"currency": currency, "cost": cost}}, "BTC", "USDT"
    )
    assert snapshot.avg_price == 50000
    assert snapshot.fee_usd == expected


def test_fees_array_is_not_double_counted():
    fee = {"currency": "USDT", "cost": 1}
    snapshot = parse_order_snapshot({"fee": fee, "fees": [fee, fee]}, "BTC", "USDT")
    assert snapshot.fee_usd == 2


@pytest.mark.parametrize("quantity", [-1, float("nan"), float("inf")])
def test_invalid_filled_quantity_raises(quantity):
    with pytest.raises(ValueError):
        parse_order_snapshot({"filled": quantity}, "BTC", "USDT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "venue,tif,is_supported",
    [
        ("binance", "IOC", True),
        ("binance", "FOK", True),
        ("hyperliquid", "IOC", True),
        ("hyperliquid", "FOK", False),
        ("unknown", "IOC", False),
    ],
)
async def test_adapter_enforces_verified_capabilities(venue, tif, is_supported):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.exchange.ccxt import CCXTExchange
    from src.exchange.order import OrderRequest
    from tests.coordinator.conftest import make_btc_usdt_spot

    adapter = CCXTExchange.__new__(CCXTExchange)
    adapter.name = venue
    adapter._balance_cache = {}
    adapter.ccxt_exchange = SimpleNamespace(
        create_order=AsyncMock(return_value={"id": "order-1", "status": "open", "filled": 0}),
        price_to_precision=lambda symbol, value: str(value),
        amount_to_precision=lambda symbol, value: str(value),
    )
    instrument = make_btc_usdt_spot(venue)
    request = OrderRequest(instrument.venue_symbol, "buy", 0.01, "limit", 50250, "0x" + "1" * 32, "spot", tif)
    if is_supported:
        await adapter.submit_order(request, instrument)
        args = adapter.ccxt_exchange.create_order.call_args.args
        assert args[1] == "limit"
        assert args[4] == 50250
        assert args[5]["clientOrderId"] == request.client_order_id
        assert args[5]["timeInForce"] == tif
    else:
        with pytest.raises(ValueError):
            await adapter.submit_order(request, instrument)
        adapter.ccxt_exchange.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_hyperliquid_query_does_not_overwrite_info_request_type():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.exchange.ccxt import CCXTExchange

    adapter = CCXTExchange.__new__(CCXTExchange)
    adapter.name = "hyperliquid"
    adapter.ccxt_exchange = SimpleNamespace(fetch_order=AsyncMock(return_value={"id": "1"}))
    await adapter.fetch_order("1", "BTC/USDC:USDC", {"type": "swap", "clientOrderId": "client-1"})
    assert adapter.ccxt_exchange.fetch_order.call_args.args[2] == {"clientOrderId": "client-1"}
