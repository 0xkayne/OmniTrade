import pytest

from src.core.exchange_factory import ExchangeFactory
from src.exchanges.ccxt_exchange import CCXTExchange


class TestWebSocketIntegration:
    """WebSocket连接集成测试"""

    def test_exchange_factory_creates_ccxt_exchanges(self, sample_config, sample_secrets):
        """测试 ExchangeFactory 创建多个 CCXT 交易所实例(不调用 connect)"""
        config1 = {**sample_config, "type": "ccxt"}
        config2 = {**sample_config, "type": "ccxt"}

        exchange1 = ExchangeFactory.create_exchange("hyperliquid", config1, sample_secrets)
        exchange2 = ExchangeFactory.create_exchange("binance", config2, sample_secrets)

        assert exchange1.name == "hyperliquid"
        assert exchange2.name == "binance"
        assert hasattr(exchange1, "connect_websocket")
        assert hasattr(exchange2, "connect_websocket")

    @pytest.mark.asyncio
    async def test_websocket_subscription_integration(self, sample_config, sample_secrets):
        """测试WebSocket订阅集成 — verifies exchange instantiation and attributes"""
        exchange = CCXTExchange("hyperliquid", sample_config, sample_secrets)

        # Verify network config is correctly loaded
        assert exchange.name == "hyperliquid"
        assert exchange.rest_base_url == "https://api.testnet.com"
        assert exchange.websocket_url == "wss://ws.testnet.com"
        assert exchange.network_type.value == "testnet"

        # CCXT adapters report WebSocket as not directly supported
        result = await exchange.connect_websocket()
        assert result is False
        # subscribe_orderbook is a no-op for ccxt adapters
        await exchange.subscribe_orderbook("ETH/USD")  # should not raise
