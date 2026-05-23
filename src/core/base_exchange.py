import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from src.market.instrument import Instrument


class NetworkType(Enum):
    MAINNET = "mainnet"
    TESTNET = "testnet"


class BaseExchange(ABC):
    """所有交易所适配器的统一接口基类 - 增强网络支持"""

    def __init__(self, name: str, config: dict, secrets: dict):
        self.name = name
        self.config = config
        self.secrets = secrets
        self.logger = logging.getLogger(f"exchange.{name}")

        # 网络配置
        self.network_type = NetworkType(config.get("default_network", "testnet"))
        self.network_config = config["networks"][self.network_type.value]

        # HTTP 会话
        self._session: aiohttp.ClientSession | None = None
        self._websocket: Any | None = None

        # 端点
        self.rest_base_url = self.network_config["rest_base_url"]
        self.websocket_url = self.network_config["websocket_url"]
        self.api_paths = self.network_config.get("api_paths", {})

        # 费率配置
        self.fees = config.get("fees", {"taker": 0.0005, "maker": 0.0002})

    def get_fee_rate(self, symbol: str = None, order_type: str = "market", side: str = "buy") -> float:
        """
        获取费率

        Args:
            symbol: 交易对
            order_type: 订单类型 ('market' or 'limit')
            side: 方向 ('buy' or 'sell')

        Returns:
            float: 费率 (e.g. 0.0005 for 0.05%)
        """
        # 简单实现：市价单用 taker，限价单用 maker
        if order_type == "market":
            return self.fees.get("taker", 0.0005)
        else:
            return self.fees.get("maker", 0.0002)

    def get_network_info(self) -> dict:
        """获取当前网络信息"""
        return {
            "network": self.network_type.value,
            "rest_base_url": self.rest_base_url,
            "websocket_url": self.websocket_url,
            "is_testnet": self.network_type == NetworkType.TESTNET,
        }

    def switch_network(self, network: NetworkType) -> bool:
        """切换网络（主网/测试网）"""
        if network.value not in self.config["networks"]:
            self.logger.error(f"网络 {network.value} 不支持")
            return False

        old_network = self.network_type
        self.network_type = network
        self.network_config = self.config["networks"][network.value]
        self.rest_base_url = self.network_config["rest_base_url"]
        self.websocket_url = self.network_config["websocket_url"]
        self.api_paths = self.network_config.get("api_paths", {})

        self.logger.info(f"已切换网络: {old_network.value} -> {network.value}")
        return True

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保HTTP会话存在"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _http_request(self, method: str, endpoint: str, authenticated: bool = False, **kwargs) -> dict:
        """统一的HTTP请求方法"""
        session = await self._ensure_session()
        url = f"{self.rest_base_url}{endpoint}"

        headers = kwargs.pop("headers", {})
        if authenticated:
            headers.update(self._get_auth_headers(method, endpoint, kwargs.get("data")))

        try:
            async with session.request(method, url, headers=headers, **kwargs) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP请求失败: {e}")
            raise
        except Exception as e:
            self.logger.error(f"请求处理失败: {e}")
            raise

    def _get_auth_headers(self, method: str, endpoint: str, data: dict | None = None) -> dict:
        """生成认证头信息 - 子类可重写"""
        # 基础实现，具体交易所需要实现自己的签名逻辑
        return {"API-KEY": self.secrets.get("api_key", ""), "Content-Type": "application/json"}

    @abstractmethod
    async def list_markets(self) -> list["Instrument"]:
        """返回该交易所所有可交易品种的 Instrument 列表。"""

    @abstractmethod
    async def connect_websocket(self) -> bool:
        """连接WebSocket"""

    @abstractmethod
    async def subscribe_orderbook(self, symbol: str):
        """订阅订单簿更新"""

    @abstractmethod
    async def connect(self):
        """建立交易所连接"""

    @abstractmethod
    async def fetch_balance(self) -> dict:
        """获取账户余额"""

    @abstractmethod
    async def fetch_orderbook(self, symbol: str, limit: int = 10) -> dict:
        """获取订单簿"""

    @abstractmethod
    async def create_order(
        self, symbol: str, order_type: str, side: str, amount: float, price: float | None = None
    ) -> dict:
        """创建订单"""

    async def close(self):
        """清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._websocket:
            await self._websocket.close()
