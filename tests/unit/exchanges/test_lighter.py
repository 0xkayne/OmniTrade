import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch
from src.exchanges.lighter import LighterExchange

class TestLighterWebSocket:
    """Lighter交易所WebSocket连接测试"""
    
    @pytest.mark.asyncio
    async def test_websocket_connection_success(self, sample_config, sample_secrets, mock_websocket):
        """测试WebSocket连接成功"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 模拟成功的连接
        mock_websocket.recv = AsyncMock(return_value=json.dumps({'status': 'connected'}))
        
        result = await exchange.connect_websocket()
        
        assert result is True
        assert exchange._websocket is not None
        mock_websocket.send.assert_called_once()  # 应该发送了认证消息
    
    @pytest.mark.asyncio
    async def test_websocket_connection_failure(self, sample_config, sample_secrets):
        """测试WebSocket连接失败"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 模拟连接异常
        with patch('websockets.connect', side_effect=Exception("Connection failed")):
            result = await exchange.connect_websocket()
            
            assert result is False
            assert exchange._websocket is None
    
    @pytest.mark.asyncio
    async def test_subscribe_orderbook(self, sample_config, sample_secrets, mock_websocket):
        """测试订单簿订阅"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        exchange._websocket = mock_websocket
        
        symbol = 'ETH/USD'
        await exchange.subscribe_orderbook(symbol)
        
        # 验证发送了正确的订阅消息
        expected_message = {
            'action': 'subscribe',
            'channel': 'orderbook',
            'symbol': 'ETHUSD'  # 格式化后的符号
        }
        mock_websocket.send.assert_called_with(json.dumps(expected_message))
    
    @pytest.mark.asyncio
    async def test_websocket_message_handling(self, sample_config, sample_secrets):
        """测试WebSocket消息处理"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 模拟订单簿消息
        orderbook_message = {
            'channel': 'orderbook',
            'symbol': 'ETHUSD',
            'bids': [{'price': '2000.0', 'quantity': '1.5'}],
            'asks': [{'price': '2001.0', 'quantity': '2.0'}],
            'timestamp': 1234567890
        }
        
        # 添加回调来验证消息处理
        received_messages = []
        async def test_callback(message):
            received_messages.append(message)
        
        exchange.add_orderbook_callback(test_callback)
        
        # 处理消息
        await exchange._handle_websocket_message(orderbook_message)
        
        # 验证回调被调用且数据格式正确
        assert len(received_messages) == 1
        message = received_messages[0]
        assert message['symbol'] == 'ETHUSD'
        assert message['bids'] == [[2000.0, 1.5]]
        assert message['asks'] == [[2001.0, 2.0]]
        assert message['source'] == 'websocket'
    
    @pytest.mark.asyncio
    async def test_websocket_reconnection_logic(self, sample_config, sample_secrets, mock_websocket):
        """测试WebSocket重连逻辑"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 第一次连接成功
        with patch('websockets.connect', return_value=mock_websocket):
            await exchange.connect_websocket()
        
        # 模拟连接断开后重新连接
        mock_websocket.recv.side_effect = [Exception("Connection lost")]
        
        # 应该能够处理重连
        with patch.object(exchange, 'connect_websocket', AsyncMock(return_value=True)) as mock_reconnect:
            # 触发消息处理循环中的异常
            await exchange._websocket_message_handler()
            
            # 验证重连逻辑被调用
            mock_reconnect.assert_called_once()