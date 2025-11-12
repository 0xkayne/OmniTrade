from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from enum import Enum
import logging
import aiohttp

class NetworkType(Enum):
    MAINNET = "mainnet"
    TESTNET = "testnet"

class BaseExchange(ABC):
    """所有交易所适配器的统一接口基类 - 增强网络支持"""
    
    def __init__(self, name: str, config: Dict, secrets: Dict):
        self.name = name
        self.config = config
        self.secrets = secrets
        self.logger = logging.getLogger(f"exchange.{name}")
        
        # 网络配置
        self.network_type = NetworkType(config.get('default_network', 'testnet'))
        self.network_config = config['networks'][self.network_type.value]
        
        # HTTP 会话
        self._session: Optional[aiohttp.ClientSession] = None
        self._websocket: Optional[Any] = None
        
        # 端点
        self.rest_base_url = self.network_config['rest_base_url']
        self.websocket_url = self.network_config['websocket_url']
        self.api_paths = self.network_config.get('api_paths', {})
        
    def get_network_info(self) -> Dict:
        """获取当前网络信息"""
        return {
            'network': self.network_type.value,
            'rest_base_url': self.rest_base_url,
            'websocket_url': self.websocket_url,
            'is_testnet': self.network_type == NetworkType.TESTNET
        }
    
    def switch_network(self, network: NetworkType) -> bool:
        """切换网络（主网/测试网）"""
        if network.value not in self.config['networks']:
            self.logger.error(f"网络 {network.value} 不支持")
            return False
            
        old_network = self.network_type
        self.network_type = network
        self.network_config = self.config['networks'][network.value]
        self.rest_base_url = self.network_config['rest_base_url']
        self.websocket_url = self.network_config['websocket_url']
        self.api_paths = self.network_config.get('api_paths', {})
        
        self.logger.info(f"已切换网络: {old_network.value} -> {network.value}")
        return True
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保HTTP会话存在"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
        
    async def _http_request(self, method: str, endpoint: str, 
                          authenticated: bool = False, **kwargs) -> Dict:
        """统一的HTTP请求方法"""
        session = await self._ensure_session()
        url = f"{self.rest_base_url}{endpoint}"
        
        headers = kwargs.pop('headers', {})
        if authenticated:
            headers.update(self._get_auth_headers(method, endpoint, kwargs.get('data')))
        
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
    
    def _get_auth_headers(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """生成认证头信息 - 子类可重写"""
        # 基础实现，具体交易所需要实现自己的签名逻辑
        return {
            'API-KEY': self.secrets.get('api_key', ''),
            'Content-Type': 'application/json'
        }
    
    @abstractmethod
    async def connect_websocket(self) -> bool:
        """连接WebSocket"""
        pass
        
    @abstractmethod
    async def subscribe_orderbook(self, symbol: str):
        """订阅订单簿更新"""
        pass
        
    @abstractmethod
    async def connect(self):
        """建立交易所连接"""
        pass
        
    @abstractmethod
    async def fetch_balance(self) -> Dict:
        """获取账户余额"""
        pass
        
    @abstractmethod
    async def fetch_orderbook(self, symbol: str, limit: int = 10) -> Dict:
        """获取订单簿"""
        pass
        
    @abstractmethod
    async def create_order(self, symbol: str, order_type: str, side: str, 
                         amount: float, price: Optional[float] = None) -> Dict:
        """创建订单"""
        pass
        
    async def close(self):
        """清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._websocket:
            await self._websocket.close()