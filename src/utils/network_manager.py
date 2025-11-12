from typing import Dict, List
from src.core.base_exchange import BaseExchange, NetworkType

class NetworkManager:
    """网络管理器，用于批量切换交易所网络"""
    
    def __init__(self, exchanges: Dict[str, BaseExchange]):
        self.exchanges = exchanges
    
    async def switch_all_networks(self, network: NetworkType) -> Dict[str, bool]:
        """切换所有交易所的网络"""
        results = {}
        
        for name, exchange in self.exchanges.items():
            try:
                success = exchange.switch_network(network)
                if success:
                    # 重新连接
                    await exchange.connect()
                results[name] = success
            except Exception as e:
                print(f"切换 {name} 到 {network.value} 失败: {e}")
                results[name] = False
        
        return results
    
    def get_network_status(self) -> Dict[str, Dict]:
        """获取所有交易所的网络状态"""
        status = {}
        for name, exchange in self.exchanges.items():
            status[name] = exchange.get_network_info()
        return status
    
    def check_network_consistency(self) -> bool:
        """检查所有交易所是否在同一个网络"""
        networks = set()
        for exchange in self.exchanges.values():
            networks.add(exchange.network_type)
        
        return len(networks) == 1