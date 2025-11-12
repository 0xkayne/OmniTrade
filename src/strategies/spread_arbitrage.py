from typing import Dict, List, Optional
from dataclasses import dataclass
import asyncio
from decimal import Decimal
import logging

@dataclass
class ArbitrageOpportunity:
    """套利机会"""
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    spread: float
    spread_percentage: float
    timestamp: float
    volume: float = 0.0
    
    def __str__(self):
        return (f"Arbitrage[{self.symbol}]: {self.buy_exchange}@{self.buy_price:.2f} -> "
                f"{self.sell_exchange}@{self.sell_price:.2f} | "
                f"Spread: {self.spread_percentage:.4f}%")

class SpreadArbitrageStrategy:
    """价差套利策略"""
    
    def __init__(self, min_spread: float = 0.1, max_position_size: float = 0.1):
        self.min_spread = min_spread  # 最小价差百分比
        self.max_position_size = max_position_size  # 最大持仓量
        self.logger = logging.getLogger('strategy.spread_arbitrage')
        self.opportunities: List[ArbitrageOpportunity] = []
        
    def analyze_opportunities(self, orderbooks: Dict[str, Dict], symbols: List[str]) -> List[ArbitrageOpportunity]:
        """分析套利机会"""
        opportunities = []
        
        for symbol in symbols:
            symbol_opportunities = self._analyze_symbol_opportunities(symbol, orderbooks)
            opportunities.extend(symbol_opportunities)
        
        # 按价差排序
        opportunities.sort(key=lambda x: x.spread_percentage, reverse=True)
        self.opportunities = opportunities
        return opportunities
    
    def _analyze_symbol_opportunities(self, symbol: str, orderbooks: Dict[str, Dict]) -> List[ArbitrageOpportunity]:
        """分析单个交易对的套利机会"""
        opportunities = []
        exchanges = list(orderbooks.keys())
        
        for i, exchange_a in enumerate(exchanges):
            for exchange_b in exchanges[i+1:]:
                orderbook_a = orderbooks.get(exchange_a, {}).get(symbol)
                orderbook_b = orderbooks.get(exchange_b, {}).get(symbol)
                
                if not orderbook_a or not orderbook_b:
                    continue
                
                # 计算两个方向的套利机会
                opp_ab = self._calculate_directional_opportunity(
                    symbol, exchange_a, exchange_b, orderbook_a, orderbook_b
                )
                if opp_ab:
                    opportunities.append(opp_ab)
                
                opp_ba = self._calculate_directional_opportunity(
                    symbol, exchange_b, exchange_a, orderbook_b, orderbook_a
                )
                if opp_ba:
                    opportunities.append(opp_ba)
        
        return opportunities
    
    def _calculate_directional_opportunity(self, symbol: str, buy_exchange: str, 
                                         sell_exchange: str, buy_orderbook: Dict, 
                                         sell_orderbook: Dict) -> Optional[ArbitrageOpportunity]:
        """计算单向套利机会"""
        if not buy_orderbook.get('asks') or not sell_orderbook.get('bids'):
            return None
        
        # 获取最佳买卖价格
        buy_price = buy_orderbook['asks'][0][0]  # 买入交易所的卖一价
        sell_price = sell_orderbook['bids'][0][0]  # 卖出交易所的买一价
        
        # 计算价差
        spread = sell_price - buy_price
        spread_percentage = (spread / buy_price) * 100
        
        # 检查是否满足最小价差要求
        if spread_percentage >= self.min_spread:
            # 计算可用交易量（取两个交易所的最小值）
            buy_volume = buy_orderbook['asks'][0][1]
            sell_volume = sell_orderbook['bids'][0][1]
            volume = min(buy_volume, sell_volume, self.max_position_size)
            
            return ArbitrageOpportunity(
                symbol=symbol,
                buy_exchange=buy_exchange,
                sell_exchange=sell_exchange,
                buy_price=buy_price,
                sell_price=sell_price,
                spread=spread,
                spread_percentage=spread_percentage,
                timestamp=asyncio.get_event_loop().time(),
                volume=volume
            )
        
        return None
    
    def should_execute_trade(self, opportunity: ArbitrageOpportunity, 
                           balances: Dict[str, Dict]) -> bool:
        """判断是否应该执行交易"""
        # 检查价差是否足够
        if opportunity.spread_percentage < self.min_spread:
            return False
        
        # 检查资金是否足够
        required_buy_amount = opportunity.buy_price * opportunity.volume
        buy_balance = balances.get(opportunity.buy_exchange, {}).get('USD', {})
        
        if buy_balance.get('free', 0) < required_buy_amount:
            self.logger.warning(f"资金不足: {opportunity.buy_exchange} "
                              f"需要 {required_buy_amount:.2f} USD, "
                              f"可用 {buy_balance.get('free', 0):.2f} USD")
            return False
        
        # 检查卖出资产是否足够
        sell_balance = balances.get(opportunity.sell_exchange, {}).get(
            opportunity.symbol.split('/')[0], {}
        )
        
        if sell_balance.get('free', 0) < opportunity.volume:
            self.logger.warning(f"资产不足: {opportunity.sell_exchange} "
                              f"需要 {opportunity.volume:.4f} {opportunity.symbol.split('/')[0]}, "
                              f"可用 {sell_balance.get('free', 0):.4f}")
            return False
        
        return True
    
    def calculate_position_size(self, opportunity: ArbitrageOpportunity, 
                              total_capital: float, risk_per_trade: float = 0.02) -> float:
        """根据资金管理计算仓位大小"""
        max_trade_amount = total_capital * risk_per_trade
        max_volume = max_trade_amount / opportunity.buy_price
        
        return min(opportunity.volume, max_volume, self.max_position_size)