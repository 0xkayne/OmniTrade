import pytest

from src.exchange.ccxt import CCXTExchange
from src.exchange.factory import ExchangeFactory


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
    async def test_hyperliquid_websocket_requires_loaded_markets(self, sample_config, sample_secrets):
        """Hyperliquid subscriptions require an explicitly connected REST catalog."""
        exchange = CCXTExchange("hyperliquid", sample_config, sample_secrets)

        # Verify network config is correctly loaded
        assert exchange.name == "hyperliquid"
        assert exchange.rest_base_url == "https://api.testnet.com"
        assert exchange.websocket_url == "wss://ws.testnet.com"
        assert exchange.network_type.value == "testnet"

        assert exchange.supports_user_fills
        with pytest.raises(RuntimeError, match="connect REST markets"):
            await exchange.connect_websocket()
        assert exchange._ws_client is None
