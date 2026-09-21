from types import SimpleNamespace

import pytest

from src.arbitrage.canary import CONFIRMATION_TOKEN, CanaryRequest
from src.arbitrage.canary import TestnetCanary as CanaryRunner
from src.exchange.order import OrderCapabilities, OrderSnapshot
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.persistence.store import PersistenceStore


class CanaryExchange:
    network_type = NetworkType.TESTNET
    supports_user_fills = False

    def __init__(self, name: str, symbol: str, quote: str):
        self.name = name
        self.api_key = "arcus-key" if name == "arcus" else None
        self._signer = object() if name == "arcus" else None
        self.ccxt_exchange = SimpleNamespace(
            privateKey="0xkey" if name == "hyperliquid" else None,
            apiKey="api-key" if name == "binance" else None,
            secret="secret" if name == "binance" else None,
        )
        self.instrument = Instrument(
            name, NetworkType.TESTNET, "perp", Asset("BTC"), Asset(quote), symbol,
        )
        self.requests = []

    async def list_markets(self):
        return [self.instrument]

    async def fetch_balance(self):
        return {"free": {self.instrument.quote.symbol: 1000.0}}

    def order_capabilities(self, _instrument):
        return OrderCapabilities(True, ("IOC",))

    async def fetch_orderbook(self, _symbol, limit=5):
        return {"bids": [[99.0, 1.0]], "asks": [[101.0, 1.0]]}

    async def submit_order(self, request, _instrument):
        self.requests.append(request)
        return OrderSnapshot(f"{self.name}-{len(self.requests)}", "closed", request.amount, request.price, 0.0)


@pytest.fixture
async def store(tmp_path):
    value = PersistenceStore(tmp_path / "canary.db", tmp_path / "audit")
    await value.initialize()
    yield value
    await value.close()


@pytest.mark.asyncio
async def test_canary_requires_explicit_confirmation(store):
    exchanges = {
        "arcus": CanaryExchange("arcus", "BTC-USD", "USD"),
        "hyperliquid": CanaryExchange("hyperliquid", "BTC/USDC:USDC", "USDC"),
    }
    result = await CanaryRunner(exchanges, store).run(CanaryRequest(
        "BTC", "arcus", "hyperliquid", 0.001,
    ))
    assert result.status == "REJECTED"
    assert CONFIRMATION_TOKEN in (result.error or "")
    assert not exchanges["arcus"].requests


@pytest.mark.asyncio
async def test_canary_opens_and_closes_one_bounded_cycle(store):
    exchanges = {
        "arcus": CanaryExchange("arcus", "BTC-USD", "USD"),
        "hyperliquid": CanaryExchange("hyperliquid", "BTC/USDC:USDC", "USDC"),
    }
    result = await CanaryRunner(exchanges, store).run(CanaryRequest(
        "BTC", "arcus", "hyperliquid", 0.001, confirmation=CONFIRMATION_TOKEN,
    ))
    assert result.status == "CLOSED"
    assert result.opening is not None and result.opening.status == "OPEN"
    assert result.closing is not None and result.closing.status == "CLOSED"
    assert all(len(exchange.requests) == 2 for exchange in exchanges.values())


@pytest.mark.asyncio
async def test_canary_rejects_notional_limit_before_submission(store):
    exchanges = {
        "arcus": CanaryExchange("arcus", "BTC-USD", "USD"),
        "binance": CanaryExchange("binance", "BTC/USDT:USDT", "USDT"),
    }
    result = await CanaryRunner(exchanges, store).run(CanaryRequest(
        "BTC", "arcus", "binance", 1.0, max_notional_usd=10.0, confirmation=CONFIRMATION_TOKEN,
    ))
    assert result.status == "REJECTED"
    assert "max_notional_usd" in (result.error or "")
    assert not exchanges["arcus"].requests
