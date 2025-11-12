import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch
from src.core.exchange_factory import ExchangeFactory

class TestWebSocketIntegration:
    """WebSocket连接集成测试"""
    
    @pytest.mark.asyncio
    async def test_multiple_exchange_websocket_connections(self, sample_config, sample_secrets):
        """测试多个交易所的WebSocket连接"""
        # 创建多个交易所配置
        exchanges_config = {
            'lighter': {
                **sample_config,
                'type': 'native'
            },
            'paradex': {
                **sample_config,
                'type': 'ccxt',
                'networks': {
                    'testnet': {
                        'rest_base_url': 'https://api-testnet.paradex.com',
                        'websocket_url': 'wss://ws-testnet.paradex.com'
                    }
                }
            }
        }
        
        secrets = {
            'lighter': sample_secrets,
            'paradex': sample_secrets
        }
        
        # 模拟WebSocket连接
        with patch('websockets.connect') as mock_ws:
            mock_ws_conn = AsyncMock()
            mock_ws.return_value.__aenter__ = AsyncMock(return_value=mock_ws_conn)
            mock_ws.return_value.__aexit__ = AsyncMock(return_value=None)
            
            # 初始化交易所
            exchanges = await ExchangeFactory.initialize_exchanges(exchanges_config, secrets)
            
            # 验证所有交易所都成功初始化
            assert 'lighter' in exchanges
            assert 'paradex' in exchanges
            
            # 测试WebSocket连接
            for name, exchange in exchanges.items():
                if hasattr(exchange, 'connect_websocket'):
                    result = await exchange.connect_websocket()
                    assert result is True
    
    @pytest.mark.asyncio
    async def test_websocket_subscription_integration(self, sample_config, sample_secrets):
        """测试WebSocket订阅集成"""
        from src.exchanges.lighter import LighterExchange
        
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 模拟WebSocket连接和消息流
        with patch('websockets.connect') as mock_ws:
            mock_ws_conn = AsyncMock()
            mock_ws.return_value.__aenter__ = AsyncMock(return_value=mock_ws_conn)
            mock_ws.return_value.__aexit__ = AsyncMock(return_value=None)
            
            # 模拟接收到的消息序列
            messages = [
                json.dumps({'status': 'connected'}),
                json.dumps({
                    'channel': 'orderbook',
                    'symbol': 'ETHUSD',
                    'bids': [{'price': '1999.0', 'quantity': '1.0'}],
                    'asks': [{'price': '2001.0', 'quantity': '1.5'}],
                    'timestamp': 1234567890
                })
            ]
            mock_ws_conn.recv = AsyncMock(side_effect=messages)
            
            # 连接并订阅
            await exchange.connect_websocket()
            await exchange.subscribe_orderbook('ETH/USD')
            
            # 验证订阅消息发送
            expected_subscribe = {
                'action': 'subscribe',
                'channel': 'orderbook',
                'symbol': 'ETHUSD'
            }
            mock_ws_conn.send.assert_any_call(json.dumps(expected_subscribe))