"""Tests for MockExchange."""

import pytest

from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.mock_backend import MockExchange


@pytest.fixture
def mock():
    return MockExchange("mock_v1")


class TestMockExchangeLifecycle:
    """Connect and close."""

    async def test_connect_sets_connected_flag(self, mock):
        assert mock._connected is False
        await mock.connect()
        assert mock._connected is True

    async def test_close_clears_connected_flag(self, mock):
        await mock.connect()
        await mock.close()
        assert mock._connected is False


class TestMockExchangeOrderbook:
    """Orderbook injection and fetching."""

    async def test_fetch_orderbook_returns_canned_data(self, mock):
        mock.set_orderbook("BTCUSDT", bids=[(50000.0, 1.0)], asks=[(50010.0, 0.5)])
        ob = await mock.fetch_orderbook("BTCUSDT")
        assert "bids" in ob
        assert "asks" in ob
        assert ob["bids"] == [[50000.0, 1.0]]
        assert ob["asks"] == [[50010.0, 0.5]]

    async def test_fetch_orderbook_unknown_symbol_raises(self, mock):
        with pytest.raises(KeyError, match="No canned orderbook"):
            await mock.fetch_orderbook("UNKNOWN")

    async def test_set_orderbook_overwrites_previous(self, mock):
        mock.set_orderbook("BTCUSDT", bids=[(50000.0, 1.0)], asks=[(50010.0, 0.5)])
        mock.set_orderbook("BTCUSDT", bids=[(49000.0, 2.0)], asks=[(49100.0, 3.0)])
        ob = await mock.fetch_orderbook("BTCUSDT")
        assert ob["bids"] == [[49000.0, 2.0]]
        assert ob["asks"] == [[49100.0, 3.0]]


class TestMockExchangeBalance:
    """Balance injection."""

    async def test_default_balance_is_zero(self, mock):
        bal = await mock.fetch_balance()
        assert bal.get("USDT", 0) == 0
        assert bal.get("BTC", 0) == 0

    async def test_set_balance_stores_value(self, mock):
        mock.set_balance("USDT", 100000.0)
        mock.set_balance("BTC", 2.0)
        bal = await mock.fetch_balance()
        assert bal["USDT"] == 100000.0
        assert bal["BTC"] == 2.0


class TestMockExchangeCreateOrder:
    """create_order returns canned result and supports injection."""

    async def test_create_order_returns_canned_dict(self, mock):
        result = await mock.create_order(
            symbol="BTCUSDT", order_type="market", side="buy", amount=0.1,
        )
        assert result["id"].startswith("mock-")
        assert result["symbol"] == "BTCUSDT"
        assert result["side"] == "buy"
        assert result["amount"] == 0.1
        assert result["status"] == "closed"

    async def test_create_order_with_injected_result(self, mock):
        canned = {
            "id": "injected-123",
            "symbol": "BTCUSDT",
            "side": "sell",
            "amount": 1.0,
            "filled": 0.8,
            "average": 49950.0,
            "status": "open",
        }
        mock.inject_next_order_result("BTCUSDT", canned)
        result = await mock.create_order(
            symbol="BTCUSDT", order_type="limit", side="sell", amount=1.0, price=50000.0,
        )
        assert result == canned

    async def test_create_order_with_injected_error(self, mock):
        mock.inject_order_error("BTCUSDT", RuntimeError("rate limit exceeded"))
        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await mock.create_order(
                symbol="BTCUSDT", order_type="market", side="buy", amount=1.0,
            )

    async def test_injected_error_clears_after_use(self, mock):
        mock.inject_order_error("BTCUSDT", RuntimeError("first"))
        with pytest.raises(RuntimeError):
            await mock.create_order(symbol="BTCUSDT", order_type="market", side="buy", amount=1.0)
        # Second call without new injection should succeed
        result = await mock.create_order(symbol="BTCUSDT", order_type="market", side="buy", amount=1.0)
        assert result["status"] == "closed"

    async def test_injected_result_clears_after_use(self, mock):
        mock.inject_next_order_result("BTCUSDT", {"id": "one-shot", "status": "closed"})
        r1 = await mock.create_order(symbol="BTCUSDT", order_type="market", side="buy", amount=1.0)
        assert r1["id"] == "one-shot"
        r2 = await mock.create_order(symbol="BTCUSDT", order_type="market", side="buy", amount=1.0)
        assert r2["id"].startswith("mock-")  # back to default


class TestMockExchangeListMarkets:
    """list_markets returns set_markets data."""

    async def test_default_list_markets_returns_empty(self, mock):
        markets = await mock.list_markets()
        assert markets == []

    async def test_set_markets_returns_instruments(self, mock):
        instr1 = Instrument(
            venue="mock_v1", market_type="spot",
            base=Asset("BTC"), quote=Asset("USDT"),
            venue_symbol="BTCUSDT",
        )
        instr2 = Instrument(
            venue="mock_v1", market_type="perp",
            base=Asset("ETH"), quote=Asset("USDC"),
            venue_symbol="ETH-USD",
        )
        mock.set_markets([instr1, instr2])
        markets = await mock.list_markets()
        assert len(markets) == 2
        assert markets == [instr1, instr2]


class TestMockExchangeCancelFetchOrder:
    """cancel_order and fetch_order."""

    async def test_cancel_order_returns_true(self, mock):
        result = await mock.cancel_order("any-id", "BTCUSDT")
        assert result is True

    async def test_fetch_order_returns_default_dict(self, mock):
        result = await mock.fetch_order("ord-1", "BTCUSDT")
        assert result["id"] == "ord-1"
        assert result["symbol"] == "BTCUSDT"
        assert result["status"] == "closed"


class TestMockExchangeWebSocket:
    """WebSocket stubs."""

    async def test_connect_websocket_returns_true(self, mock):
        assert await mock.connect_websocket() is True

    async def test_subscribe_orderbook_returns_none(self, mock):
        result = await mock.subscribe_orderbook("BTCUSDT")
        assert result is None
