import asyncio
from typing import Dict, List
from dataclasses import dataclass
from src.core.base_exchange import BaseExchange

@dataclass
class ArbitrageOpportunity:
    """套利机会"""
    symbol: str
    exchange_a: str
    exchange_b: str  
    exchange_a_price: float
    exchange_b_price: float
    spread: float
    spread_percentage: float
    timestamp: float

class ArbitrageEngine:
    """套利引擎"""
    
    def __init__(self, exchanges: Dict[str, BaseExchange], min_spread: float = 0.5):
        self.exchanges = exchanges
        self.min_spread = min_spread  # 最小价差百分比
        self.opportunities = []
        
    async def monitor_spreads(self, symbols: List[str]) -> List[ArbitrageOpportunity]:
        """监控指定交易对的价差"""
        opportunities = []
        
        for symbol in symbols:
            # 获取所有交易所的订单簿
            orderbooks = {}
            for name, exchange in self.exchanges.items():
                try:
                    orderbook = await exchange.fetch_orderbook(symbol)
                    orderbooks[name] = orderbook
                except Exception as e:
                    print(f"获取 {name} {symbol} 订单簿失败: {e}")
            
            # 计算所有交易所组合的价差
            exchange_names = list(orderbooks.keys())
            for i in range(len(exchange_names)):
                for j in range(i + 1, len(exchange_names)):
                    opp = self._calculate_arbitrage(
                        symbol, exchange_names[i], exchange_names[j], 
                        orderbooks[exchange_names[i]], orderbooks[exchange_names[j]]
                    )
                    if opp and abs(opp.spread_percentage) >= self.min_spread:
                        opportunities.append(opp)
                        
        self.opportunities = opportunities
        return opportunities
    
    def _calculate_arbitrage(self, symbol: str, exchange_a: str, exchange_b: str, 
                           orderbook_a: Dict, orderbook_b: Dict) -> Optional[ArbitrageOpportunity]:
        """计算两个交易所之间的套利机会"""
        if not orderbook_a['bids'] or not orderbook_a['asks'] or not orderbook_b['bids'] or not orderbook_b['asks']:
            return None
            
        # 获取买卖价格
        a_bid = orderbook_a['bids'][0][0]  # A交易所买一价
        a_ask = orderbook_a['asks'][0][0]  # A交易所卖一价
        b_bid = orderbook_b['bids'][0][0]  # B交易所买一价  
        b_ask = orderbook_b['asks'][0][0]  # B交易所卖一价
        
        # 计算两个方向的价差
        # 方向1: 在B买入，在A卖出
        spread_1 = a_bid - b_ask
        spread_percentage_1 = (spread_1 / b_ask) * 100 if b_ask > 0 else 0
        
        # 方向2: 在A买入，在B卖出  
        spread_2 = b_bid - a_ask
        spread_percentage_2 = (spread_2 / a_ask) * 100 if a_ask > 0 else 0
        
        # 选择价差更大的方向
        if spread_percentage_1 > spread_percentage_2 and spread_percentage_1 > 0:
            return ArbitrageOpportunity(
                symbol=symbol,
                exchange_a=exchange_b,  # 买入交易所
                exchange_b=exchange_a,  # 卖出交易所
                exchange_a_price=b_ask,
                exchange_b_price=a_bid,
                spread=spread_1,
                spread_percentage=spread_percentage_1,
                timestamp=asyncio.get_event_loop().time()
            )
        elif spread_percentage_2 > 0:
            return ArbitrageOpportunity(
                symbol=symbol,
                exchange_a=exchange_a,  # 买入交易所
                exchange_b=exchange_b,  # 卖出交易所  
                exchange_a_price=a_ask,
                exchange_b_price=b_bid,
                spread=spread_2,
                spread_percentage=spread_percentage_2,
                timestamp=asyncio.get_event_loop().time()
            )
        
        return None