from typing import Dict
from src.core.base_exchange import BaseExchange, NetworkType
from src.exchanges.ccxt_exchange import CCXTExchange
from src.exchanges.lighter import LighterExchange
from src.exchanges.paradex import ParadexExchange


class ExchangeFactory:
    """交易所工厂，统一创建和管理交易所实例 - 增强网络支持"""
    
    @staticmethod
    def create_exchange(name: str, config: Dict, secrets: Dict) -> BaseExchange:
        """根据配置创建交易所实例"""
        exchange_type = config.get('type', 'ccxt')
        
        if exchange_type == 'native':
            # 原生SDK交易所
            if name == 'lighter':
                return LighterExchange(name, config, secrets)
            else:
                raise ValueError(f"不支持的native交易所: {name}")
                
        elif exchange_type == 'ccxt':
            return CCXTExchange(name, config, secrets)
            
        else:
            raise ValueError(f"不支持的交易所类型: {exchange_type}")
    
    @staticmethod
    async def initialize_exchanges(exchange_configs: Dict, secrets: Dict) -> Dict[str, BaseExchange]:
        """批量初始化所有启用的交易所"""
        exchanges = {}
        
        for name, config in exchange_configs.items():
            if config.get('enabled', False):
                try:
                    exchange = ExchangeFactory.create_exchange(name, config, secrets.get(name, {}))
                    await exchange.connect()
                    
                    # 输出网络信息
                    network_info = exchange.get_network_info()
                    print(f"✅ 交易所 {name} 初始化成功 - 网络: {network_info['network']}")
                    print(f"   REST端点: {network_info['rest_base_url']}")
                    print(f"   WebSocket端点: {network_info['websocket_url']}")
                    
                    exchanges[name] = exchange
                except Exception as e:
                    print(f"❌ 交易所 {name} 初始化失败: {e}")
                    
        return exchanges