import pytest

from src.core.base_exchange import BaseExchange, NetworkType


class _TestExchange(BaseExchange):
    """Minimal concrete BaseExchange for testing the base class itself."""

    async def list_markets(self) -> list:
        return []

    async def connect_websocket(self) -> bool:
        return True

    async def subscribe_orderbook(self, symbol: str):
        pass

    async def connect(self):
        pass

    async def _fetch_balance_impl(self, params: dict | None = None) -> dict:
        return {}

    async def fetch_orderbook(self, symbol: str, limit: int = 10, params: dict | None = None) -> dict:
        return {"bids": [], "asks": []}

    async def create_order(self, symbol: str, order_type: str, side: str, amount: float, price: float | None = None, params: dict | None = None) -> dict:
        return {"id": "test-1"}

    async def cancel_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> bool:
        return True

    async def fetch_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> dict:
        return {"id": id, "status": "closed"}

    async def watch_orders(self, symbol: str | None = None, params: dict | None = None) -> dict:
        return {"id": "test-1", "status": "closed"}


class TestNetworkSwitching:
    """网络切换功能测试"""

    @pytest.mark.asyncio
    async def test_network_initialization(self, sample_config, sample_secrets):
        """测试网络初始化"""
        exchange = _TestExchange("test", sample_config, sample_secrets)

        assert exchange.network_type == NetworkType.TESTNET
        assert exchange.rest_base_url == "https://api.testnet.com"
        assert exchange.websocket_url == "wss://ws.testnet.com"

    @pytest.mark.asyncio
    async def test_network_switching(self, sample_config, sample_secrets):
        """测试网络切换"""
        exchange = _TestExchange("test", sample_config, sample_secrets)

        success = exchange.switch_network(NetworkType.MAINNET)

        assert success is True
        assert exchange.network_type == NetworkType.MAINNET
        assert exchange.rest_base_url == "https://api.mainnet.com"
        assert exchange.websocket_url == "wss://ws.mainnet.com"

    @pytest.mark.asyncio
    async def test_network_switching_invalid(self, sample_config, sample_secrets):
        """测试无效网络切换"""
        exchange = _TestExchange("test", sample_config, sample_secrets)

        exchange.config["networks"] = {"testnet": exchange.config["networks"]["testnet"]}

        success = exchange.switch_network(NetworkType.MAINNET)

        assert success is False
        assert exchange.network_type == NetworkType.TESTNET

    def test_get_network_info(self, sample_config, sample_secrets):
        """测试获取网络信息"""
        exchange = _TestExchange("test", sample_config, sample_secrets)

        info = exchange.get_network_info()

        assert info["network"] == "testnet"
        assert info["is_testnet"] is True
        assert info["rest_base_url"] == "https://api.testnet.com"
        assert info["websocket_url"] == "wss://ws.testnet.com"
