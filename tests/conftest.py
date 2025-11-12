import pytest
import asyncio
import aiohttp
import json
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

# 添加源码路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.core.base_exchange import NetworkType

@pytest.fixture
def event_loop():
    """创建事件循环夹具"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def sample_config():
    """提供样本配置"""
    return {
        'type': 'native',
        'enabled': True,
        'default_network': 'testnet',
        'networks': {
            'mainnet': {
                'rest_base_url': 'https://api.mainnet.com',
                'websocket_url': 'wss://ws.mainnet.com',
                'api_paths': {
                    'orderbook': '/api/v1/orderbook',
                    'balance': '/api/v1/account/balance',
                    'order': '/api/v1/order'
                }
            },
            'testnet': {
                'rest_base_url': 'https://api.testnet.com',
                'websocket_url': 'wss://ws.testnet.com',
                'api_paths': {
                    'orderbook': '/api/v1/orderbook',
                    'balance': '/api/v1/account/balance',
                    'order': '/api/v1/order'
                }
            }
        },
        'rate_limit': 100,
        'symbols': ['ETH/USD', 'BTC/USD']
    }

@pytest.fixture
def sample_secrets():
    """提供样本密钥"""
    return {
        'api_key': 'test_api_key_123',
        'secret': 'test_secret_456'
    }

@pytest.fixture
def mock_aiohttp_session():
    """模拟aiohttp会话"""
    with patch('aiohttp.ClientSession') as mock_session:
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session.return_value)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
        yield mock_session

@pytest.fixture
def mock_websocket():
    """模拟WebSocket连接"""
    with patch('websockets.connect') as mock_ws:
        mock_ws_conn = AsyncMock()
        mock_ws.return_value.__aenter__ = AsyncMock(return_value=mock_ws_conn)
        mock_ws.return_value.__aexit__ = AsyncMock(return_value=None)
        yield mock_ws_conn