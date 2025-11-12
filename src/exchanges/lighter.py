import asyncio
import json
import time
import hmac
import hashlib
from typing import Dict, Optional, Callable
from src.core.base_exchange import BaseExchange, NetworkType

class LighterExchange(BaseExchange):
    """Lighter交易所原生SDK适配器 - 完整网络支持"""
    
    def __init__(self, name: str, config: Dict, secrets: Dict):
        super().__init__(name, config, secrets)
        self._orderbook_callbacks = []
        
    def _generate_signature(self, method: str, endpoint: str, data: Optional[Dict] = None) -> str:
        """生成Lighter特定的签名"""
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{endpoint}"
        if data:
            message += json.dumps(data, separators=(',', ':'))
            
        signature = hmac.new(
            self.secrets.get('secret', '').encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    def _get_auth_headers(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """生成Lighter认证头信息"""
        signature = self._generate_signature(method, endpoint, data)
        timestamp = str(int(time.time() * 1000))
        
        return {
            'X-API-KEY': self.secrets.get('api_key', ''),
            'X-SIGNATURE': signature,
            'X-TIMESTAMP': timestamp,
            'Content-Type': 'application/json'
        }
    
    async def connect(self):
        """连接Lighter交易所"""
        # 测试连接
        try:
            # 尝试获取订单簿来测试连接
            test_symbol = self.config['symbols'][0] if self.config['symbols'] else 'ETH/USD'
            orderbook = await self.fetch_orderbook(test_symbol)
            self.logger.info(f"Lighter {self.network_type.value} 连接测试成功")
        except Exception as e:
            self.logger.error(f"Lighter {self.network_type.value} 连接测试失败: {e}")
            raise
    
    async def connect_websocket(self) -> bool:
        """连接Lighter WebSocket"""
        try:
            import websockets
            self._websocket = await websockets.connect(self.websocket_url)
            
            # 发送认证消息（如果需要）
            if self.secrets.get('api_key'):
                auth_msg = {
                    'action': 'auth',
                    'api_key': self.secrets.get('api_key'),
                    'signature': self._generate_signature('GET', '/ws')
                }
                await self._websocket.send(json.dumps(auth_msg))
                
            self.logger.info(f"Lighter WebSocket连接已建立: {self.websocket_url}")
            return True
        except Exception as e:
            self.logger.error(f"Lighter WebSocket连接失败: {e}")
            return False
    
    async def subscribe_orderbook(self, symbol: str):
        """订阅订单簿更新"""
        if not self._websocket:
            await self.connect_websocket()
            
        subscribe_msg = {
            'action': 'subscribe',
            'channel': 'orderbook',
            'symbol': symbol.replace('/', '')  # 格式化符号
        }
        await self._websocket.send(json.dumps(subscribe_msg))
        
        # 启动消息处理循环
        asyncio.create_task(self._websocket_message_handler())
    
    async def _websocket_message_handler(self):
        """处理WebSocket消息"""
        try:
            async for message in self._websocket:
                data = json.loads(message)
                await self._handle_websocket_message(data)
        except Exception as e:
            self.logger.error(f"WebSocket消息处理错误: {e}")
    
    async def _handle_websocket_message(self, data: Dict):
        """处理具体的WebSocket消息"""
        if data.get('channel') == 'orderbook':
            # 处理订单簿更新
            formatted_orderbook = self._format_websocket_orderbook(data)
            for callback in self._orderbook_callbacks:
                await callback(formatted_orderbook)
    
    def _format_websocket_orderbook(self, data: Dict) -> Dict:
        """格式化WebSocket订单簿数据"""
        return {
            'symbol': data.get('symbol'),
            'bids': [[float(bid['price']), float(bid['quantity'])] for bid in data.get('bids', [])],
            'asks': [[float(ask['price']), float(ask['quantity'])] for ask in data.get('asks', [])],
            'timestamp': data.get('timestamp'),
            'source': 'websocket'
        }
    
    def add_orderbook_callback(self, callback: Callable):
        """添加订单簿更新回调"""
        self._orderbook_callbacks.append(callback)
    
    async def fetch_balance(self) -> Dict:
        """获取余额 - 使用REST API"""
        endpoint = self.api_paths.get('balance', '/api/v1/account/balance')
        return await self._http_request('GET', endpoint, authenticated=True)
    
    async def fetch_orderbook(self, symbol: str, limit: int = 10) -> Dict:
        """获取订单簿 - 使用REST API"""
        endpoint = self.api_paths.get('orderbook', '/api/v1/orderbook')
        params = {'symbol': symbol.replace('/', ''), 'depth': limit}
        
        response = await self._http_request('GET', endpoint, params=params)
        return self._format_rest_orderbook(response, symbol)
    
    def _format_rest_orderbook(self, data: Dict, symbol: str) -> Dict:
        """格式化REST订单簿响应"""
        return {
            'symbol': symbol,
            'bids': [[float(bid['price']), float(bid['quantity'])] for bid in data.get('bids', [])],
            'asks': [[float(ask['price']), float(ask['quantity'])] for ask in data.get('asks', [])],
            'timestamp': data.get('timestamp'),
            'source': 'rest'
        }
    
    async def create_order(self, symbol: str, order_type: str, side: str, 
                         amount: float, price: Optional[float] = None) -> Dict:
        """创建订单"""
        endpoint = self.api_paths.get('order', '/api/v1/order')
        
        order_data = {
            'symbol': symbol.replace('/', ''),
            'side': side.lower(),
            'quantity': str(amount),
            'orderType': order_type.lower()
        }
        
        if price is not None:
            order_data['price'] = str(price)
        
        return await self._http_request('POST', endpoint, data=order_data, authenticated=True)