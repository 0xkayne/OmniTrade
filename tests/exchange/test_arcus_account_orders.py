"""Offline Arcus account and order contracts using documented wire schemas."""

from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.exchange.arcus import ArcusExchange, ArcusSigner
from src.exchange.order import OrderRequest


@pytest.fixture
def exchange():
    instance = ArcusExchange(
        "arcus",
        {"default_network": "testnet", "networks": {"testnet": {}}, "options": {"account_index": 2}},
        {"master_wallet_address": "0x" + "22" * 20, "api_key": "synthetic-key", "api_signing_key": "11" * 32},
    )
    row = {
        "marketId": 1,
        "marketDisplayName": "BTC-USD",
        "type": "PERPETUAL",
        "baseAsset": "BTC",
        "quoteAsset": "USD",
        "tickSize": "0.1",
        "stepSize": "0.00000001",
        "markPrice": "50000",
        "initialMarginFraction": "0.025",
        "tickTiers": [{"upToPrice": "10000", "tick": "0.1"}, {"tick": "1"}],
    }
    market = instance._parse_market(row)
    instance._markets_by_symbol[market.venue_symbol] = market
    instance._markets_by_id[1] = market
    instance._market_meta[market.venue_symbol] = row
    return instance


def account(exchange, **values):
    return {"address": exchange.master_wallet_address, "accountIndex": 2, **values}


def leverage(exchange):
    return account(
        exchange,
        leverages=[
            {"marketId": 1, "marketDisplayName": "BTC-USD", "leverage": 40, "isolated": False, "marginMode": "CROSS"}
        ],
    )


def position(exchange, **values):
    return account(
        exchange,
        marketId=1,
        marketDisplayName="BTC-USD",
        side="SHORT",
        size="-0.02",
        averageEntryPrice="49999",
        markPx="50000",
        leverage="3.2",
        marginMode="CROSS",
        **values,
    )


def fill(trade_id="trade-1", **values):
    return {
        "tradeId": trade_id,
        "orderId": "order-1",
        "marketId": 1,
        "marketDisplayName": "BTC-USD",
        "side": "BUY",
        "size": "0.01",
        "price": "50000",
        "fee": "0.15",
        "createdAt": 1700000000123456,
        "positionEffect": "OPEN_LONG",
        **values,
    }


@pytest.mark.asyncio
async def test_empty_positions_require_real_leverage_mode_and_mark(exchange):
    exchange._request = AsyncMock(
        side_effect=[{"positions": {}, "total": 0}, leverage(exchange), {"markets": [exchange._market_meta["BTC-USD"]]}]
    )
    snapshot = await exchange.fetch_order_position(exchange._market("BTC-USD"))
    assert snapshot.qty_native == 0
    assert snapshot.leverage == 40
    assert snapshot.margin_mode == "cross"
    assert snapshot.mark_price == 50000
    assert snapshot.entry_price is None
    assert exchange._request.call_args_list[0].kwargs["params"] == {
        "address": exchange.master_wallet_address,
        "accountIndex": 2,
        "market": "BTC-USD",
    }


@pytest.mark.asyncio
async def test_nonzero_signed_position_uses_documented_decimal_units(exchange):
    exchange._request = AsyncMock(return_value={"positions": {"1": position(exchange)}})
    snapshot = await exchange.fetch_order_position(exchange._market("BTC-USD"))
    assert snapshot.qty_native == -0.02
    assert snapshot.entry_price == 49999
    assert snapshot.mark_price == 50000
    assert snapshot.leverage == 3.2
    assert len(await exchange.fetch_order_positions()) == 1
    assert (await exchange.fetch_positions(["BTC-USD"]))[0]["contracts"] == 0.02


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"positions": []}, {"positions": None}])
async def test_incomplete_positions_never_become_flat(exchange, response):
    exchange._request = AsyncMock(return_value=response)
    with pytest.raises(ValueError, match="complete positions"):
        await exchange.fetch_order_positions()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accountIndex", 0),
        ("side", "LONG"),
        ("size", "NaN"),
        ("markPx", "0"),
        ("leverage", None),
        ("marketDisplayName", "ETH-USD"),
    ],
)
async def test_inconsistent_position_facts_fail_closed(exchange, field, value):
    row = position(exchange)
    row[field] = value
    exchange._request = AsyncMock(return_value={"positions": {"1": row}})
    with pytest.raises(ValueError):
        await exchange.fetch_order_positions()


@pytest.mark.asyncio
async def test_account_checks_identity_and_actual_collateral(exchange):
    exchange._request = AsyncMock(return_value=account(exchange, freeCollateral="2000", positions={}))
    result = await exchange.fetch_order_account(exchange._market("BTC-USD"))
    assert result.available == {"USD": 2000}
    assert result.margin_mode == "single_asset"
    exchange._request.return_value["freeCollateral"] = "NaN"
    with pytest.raises(ValueError, match="freeCollateral"):
        await exchange.fetch_order_account(exchange._market("BTC-USD"))


@pytest.mark.asyncio
async def test_positions_declared_total_cannot_hide_unknown_positions(exchange):
    exchange._request = AsyncMock(return_value={"positions": {}, "total": 1})
    with pytest.raises(ValueError, match="incomplete"):
        await exchange.fetch_order_positions()


@pytest.mark.asyncio
async def test_account_index_cannot_be_overridden(exchange):
    exchange._request = AsyncMock()
    with pytest.raises(ValueError, match="configured account"):
        await exchange.fetch_open_orders(params={"accountIndex": 0})
    exchange._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_requires_complete_results_and_preserves_unknown_client_id(exchange):
    exchange._request = AsyncMock(return_value={"orders": [], "total": 0})
    missing = await exchange.fetch_order_by_client_id("not-yet-visible", "BTC-USD")
    assert missing["status"] == "unknown"
    assert missing["filled"] is None
    exchange._request.return_value = {"orders": [], "total": 1}
    with pytest.raises(ValueError, match="truncated"):
        await exchange.fetch_open_orders("BTC-USD")
    exchange._request.return_value = {"orders": [{"clientId": "reused"}, {"clientId": "reused"}]}
    with pytest.raises(ValueError, match="multiple historical"):
        await exchange.fetch_order_by_client_id("reused", "BTC-USD")


@pytest.mark.parametrize(
    ("wire", "status", "quantity"),
    [
        ({"status": "ACK"}, "unknown", None),
        (
            {
                "state": "PARTIALLY_FILLED",
                "status": "CANCELED",
                "timeInForce": "IOC",
                "originalSize": "0.3",
                "remainingSize": "0.2",
            },
            "canceled",
            0.1,
        ),
        ({"state": "CANCELED", "cancelReason": "MODIFY_CANCELED"}, "unknown", None),
        ({"state": "OPEN", "originalSize": "0.3", "remainingSize": "0.3"}, "open", 0),
    ],
)
def test_order_status_does_not_invent_execution(wire, status, quantity):
    result = ArcusExchange._normalize_order(wire)
    assert result["status"] == status
    assert result["filled"] == quantity


@pytest.mark.asyncio
async def test_fill_query_deduplicates_and_normalizes_quote_fee_pnl_timestamp(exchange):
    exchange._request = AsyncMock(return_value={"fills": [fill(), fill()], "total": 2})
    trades = await exchange.fetch_my_trades("BTC-USD", since=1700000000000)
    assert len(trades) == 1
    trade = trades[0]
    assert trade["fee"] == {"currency": "USD", "cost": 0.15}
    assert trade["timestamp"] == 1700000000123.456
    assert trade["info"]["realizedPnl"] == 0
    assert exchange._request.call_args.kwargs["params"]["from"] == 1700000000000000
    assert (
        ArcusExchange._normalize_fill(fill(positionEffect="CLOSE_LONG", closedPnl="-1.25"))["info"]["realizedPnl"]
        == -1.25
    )
    unknown = ArcusExchange._normalize_fill(fill(positionEffect="CLOSE_LONG"))
    assert "realizedPnl" not in unknown["info"]


@pytest.mark.asyncio
async def test_order_snapshot_enriches_only_matching_fills(exchange):
    exchange._request = AsyncMock(
        side_effect=[
            {"orderId": "order-1", "state": "FILLED", "filledSize": "0.02", "avgFillPrice": "50050"},
            {"fills": [fill(), fill("trade-2", price="50100", fee="0.2"), fill("unrelated", orderId="other")]},
        ]
    )
    request = OrderRequest("BTC-USD", "buy", 0.02, "limit", 50100, "client-1", "perp", "IOC")
    result = await exchange.fetch_order_snapshot(request, exchange._market("BTC-USD"), "order-1")
    assert result.filled_qty_native == 0.02
    assert result.avg_price == 50050
    assert result.fee_usd == pytest.approx(0.35)
    assert len(result.fills) == 2
    assert all(row["realized_pnl"] == 0 for row in result.fills)


@pytest.mark.asyncio
async def test_synchronous_filled_submission_also_fetches_accounting(exchange):
    order = {"orderId": "order-1", "state": "FILLED", "filledSize": "0.01", "avgFillPrice": "50000"}
    exchange._request = AsyncMock(side_effect=[order, order, {"fills": [fill()]}])
    request = OrderRequest("BTC-USD", "buy", 0.01, "limit", 50000, "client-1", "perp", "IOC")
    result = await exchange.submit_order(request, exchange._market("BTC-USD"))
    assert result.fee_usd == 0.15
    assert len(result.fills) == 1
    assert [call.args[0] for call in exchange._request.call_args_list] == ["POST", "GET", "GET"]


def test_liquidation_placeholder_is_not_known_realized_pnl():
    result = ArcusExchange._normalize_fill(
        fill(positionEffect="CLOSE_LONG", closedPnl="0", liquidation={"method": "LIQUIDATION"})
    )
    assert "realizedPnl" not in result["info"]


@pytest.mark.asyncio
@pytest.mark.parametrize("client_id", [None, "stable-client"])
async def test_cancel_discriminator_matches_signed_target(exchange, monkeypatch, client_id):
    exchange._request = AsyncMock(return_value={"status": "ACK"})
    monkeypatch.setattr("src.exchange.arcus.time.time_ns", lambda: 123)
    await exchange.cancel_order("order-1", "BTC-USD", {"clientOrderId": client_id})
    call = exchange._request.call_args.kwargs
    target = {"c": client_id} if client_id else {"id": "order-1"}
    expected = {"ad": exchange.master_wallet_address, "ai": 2, "ct": 123, "m": 1, "op": 2, "v": 1, **target}
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("11" * 32))
    private.public_key().verify(bytes.fromhex(call["headers"]["X-Signature"]), ArcusSigner.canonical_json(expected))
    assert call["json"]["kind"] == ("clientId" if client_id else "orderId")
    assert ("orderId" in call["json"]) is (client_id is None)


@pytest.mark.asyncio
async def test_tick_tiers_check_price_but_signature_keeps_base_tick(exchange, monkeypatch):
    exchange._request = AsyncMock(return_value={"orderId": "new", "status": "ACK"})
    monkeypatch.setattr("src.exchange.arcus.time.time_ns", lambda: 123)
    assert exchange.price_tick("BTC-USD", 9999) == 0.1
    assert exchange.price_tick("BTC-USD", 10000) == 1
    with pytest.raises(ValueError, match="not aligned"):
        await exchange.create_order("BTC-USD", "limit", "buy", 1e-8, 50000.1)
    await exchange.create_order("BTC-USD", "limit", "buy", 1e-8, 50000, {"timeInForce": "IOC", "goodTilTime": 1000})
    body = exchange._request.call_args.kwargs["json"]
    assert body["quantity"] == "0.00000001"
    payload = exchange._build_place_order_payload(
        address=exchange.master_wallet_address,
        account_index=2,
        market_id=1,
        timestamp_ns=123,
        price_ticks=500000,
        quantity_quanta=1,
        side_code=0,
        order_type_code=2,
        expiry=1000000,
    )
    assert body["signature"] == exchange._signer.sign_payload(payload)


@pytest.mark.asyncio
async def test_set_leverage_uses_legacy_signature_and_verifies_readback(exchange, monkeypatch):
    monkeypatch.setattr("src.exchange.arcus.time.time_ns", lambda: 123)
    exchange._request = AsyncMock(side_effect=[{"status": "ACK"}, leverage(exchange)])
    await exchange.set_leverage(40, "BTC-USD")
    call = exchange._request.call_args_list[0].kwargs
    assert call["headers"]["X-Signature"] == exchange._signer.sign_legacy(123, "setLeverage", call["json"])
    assert "ct" not in call["json"]
    assert "isolated" not in call["json"]


def test_spot_market_is_not_mislabeled_as_perpetual(exchange):
    assert exchange._parse_market({**exchange._market_meta["BTC-USD"], "type": "SPOT"}) is None
