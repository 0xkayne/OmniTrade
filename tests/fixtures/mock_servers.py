import asyncio
import aiohttp
from aiohttp import web
import json

class MockExchangeServer:
    """模拟交易所服务器"""
    
    def __init__(self, host='localhost', port=8765):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.setup_routes()
        self.runner = None
        self.site = None
        
    def setup_routes(self):
        """设置路由"""
        self.app.router.add_get('/api/v1/orderbook', self.handle_orderbook)
        self.app.router.add_get('/api/v1/account/balance', self.handle_balance)
        self.app.router.add_post('/api/v1/order', self.handle_create_order)
        
    async def handle_orderbook(self, request):
        """处理订单簿请求"""
        symbol = request.query.get('symbol', 'ETHUSD')
        depth = request.query.get('depth', 10)
        
        response = {
            'bids': [{'price': '1999.0', 'quantity': '1.0'}],
            'asks': [{'price': '2001.0', 'quantity': '1.5'}],
            'timestamp': 1234567890,
            'symbol': symbol
        }
        return web.json_response(response)
    
    async def handle_balance(self, request):
        """处理余额请求"""
        # 验证认证头
        api_key = request.headers.get('X-API-KEY')
        if not api_key or api_key != 'test_api_key_123':
            return web.json_response({'error': 'Unauthorized'}, status=401)
            
        response = {
            'ETH': {'free': '1.5', 'used': '0.5', 'total': '2.0'},
            'USD': {'free': '5000.0', 'used': '0.0', 'total': '5000.0'}
        }
        return web.json_response(response)
    
    async def handle_create_order(self, request):
        """处理创建订单请求"""
        # 验证认证
        api_key = request.headers.get('X-API-KEY')
        if not api_key or api_key != 'test_api_key_123':
            return web.json_response({'error': 'Unauthorized'}, status=401)
            
        data = await request.json()
        
        response = {
            'order_id': 'test_order_123',
            'status': 'open',
            'symbol': data.get('symbol'),
            'side': data.get('side'),
            'quantity': data.get('quantity'),
            'price': data.get('price', '0.0')
        }
        return web.json_response(response)
    
    async def start(self):
        """启动服务器"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        print(f"Mock server running on http://{self.host}:{self.port}")
    
    async def stop(self):
        """停止服务器"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

@pytest.fixture
async def mock_server():
    """提供模拟服务器夹具"""
    server = MockExchangeServer()
    await server.start()
    yield server
    await server.stop()