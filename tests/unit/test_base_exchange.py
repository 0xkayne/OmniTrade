import pytest
from src.core.base_exchange import BaseExchange, NetworkType
from src.exchanges.lighter import LighterExchange

class TestNetworkSwitching:
    """网络切换功能测试"""
    
    @pytest.mark.asyncio
    async def test_network_initialization(self, sample_config, sample_secrets):
        """测试网络初始化"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 验证默认网络
        assert exchange.network_type == NetworkType.TESTNET
        assert exchange.rest_base_url == 'https://api.testnet.com'
        assert exchange.websocket_url == 'wss://ws.testnet.com'
    
    @pytest.mark.asyncio
    async def test_network_switching(self, sample_config, sample_secrets):
        """测试网络切换"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 切换到主网
        success = exchange.switch_network(NetworkType.MAINNET)
        
        assert success is True
        assert exchange.network_type == NetworkType.MAINNET
        assert exchange.rest_base_url == 'https://api.mainnet.com'
        assert exchange.websocket_url == 'wss://ws.mainnet.com'
    
    @pytest.mark.asyncio
    async def test_network_switching_invalid(self, sample_config, sample_secrets):
        """测试无效网络切换"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        # 修改配置，移除主网配置
        exchange.config['networks'] = {'testnet': exchange.config['networks']['testnet']}
        
        # 尝试切换到不存在的网络
        success = exchange.switch_network(NetworkType.MAINNET)
        
        assert success is False
        assert exchange.network_type == NetworkType.TESTNET  # 应该保持原网络
    
    def test_get_network_info(self, sample_config, sample_secrets):
        """测试获取网络信息"""
        exchange = LighterExchange('lighter', sample_config, sample_secrets)
        
        info = exchange.get_network_info()
        
        assert info['network'] == 'testnet'
        assert info['is_testnet'] is True
        assert info['rest_base_url'] == 'https://api.testnet.com'
        assert info['websocket_url'] == 'wss://ws.testnet.com'