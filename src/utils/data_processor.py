import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
import json
from datetime import datetime

class DataProcessor:
    """数据处理工具类"""
    
    @staticmethod
    def calculate_spread_stats(orderbooks: Dict[str, Dict]) -> Dict[str, float]:
        """计算价差统计信息"""
        spreads = []
        symbols = set()
        
        for exchange, symbol_data in orderbooks.items():
            for symbol, orderbook in symbol_data.items():
                symbols.add(symbol)
                if orderbook and orderbook.get('bids') and orderbook.get('asks'):
                    bid = orderbook['bids'][0][0]
                    ask = orderbook['asks'][0][0]
                    spread = (ask - bid) / bid * 100  # 买卖价差百分比
                    spreads.append(spread)
        
        if not spreads:
            return {}
        
        return {
            'mean_spread': float(np.mean(spreads)),
            'median_spread': float(np.median(spreads)),
            'min_spread': float(np.min(spreads)),
            'max_spread': float(np.max(spreads)),
            'std_spread': float(np.std(spreads)),
            'symbol_count': len(symbols)
        }
    
    @staticmethod
    def normalize_orderbook(orderbook: Dict, symbol: str) -> Dict:
        """标准化订单簿数据格式"""
        if not orderbook:
            return {}
        
        normalized = {
            'symbol': symbol,
            'timestamp': orderbook.get('timestamp'),
            'source': orderbook.get('source', 'unknown')
        }
        
        # 处理bids
        if 'bids' in orderbook:
            normalized['bids'] = [
                [float(price), float(quantity)] 
                for price, quantity in orderbook['bids']
            ]
        else:
            normalized['bids'] = []
        
        # 处理asks
        if 'asks' in orderbook:
            normalized['asks'] = [
                [float(price), float(quantity)] 
                for price, quantity in orderbook['asks']
            ]
        else:
            normalized['asks'] = []
        
        return normalized
    
    @staticmethod
    def calculate_mid_price(orderbook: Dict) -> Optional[float]:
        """计算中间价"""
        if not orderbook.get('bids') or not orderbook.get('asks'):
            return None
        
        best_bid = orderbook['bids'][0][0]
        best_ask = orderbook['asks'][0][0]
        
        return (best_bid + best_ask) / 2
    
    @staticmethod
    def calculate_weighted_average_price(orderbook: Dict, depth: int = 5) -> Optional[float]:
        """计算加权平均价格"""
        if not orderbook.get('bids') or not orderbook.get('asks'):
            return None
        
        # 计算买盘加权平均价
        bid_prices = [bid[0] for bid in orderbook['bids'][:depth]]
        bid_quantities = [bid[1] for bid in orderbook['bids'][:depth]]
        total_bid_quantity = sum(bid_quantities)
        
        if total_bid_quantity == 0:
            return None
        
        weighted_bid = sum(price * qty for price, qty in zip(bid_prices, bid_quantities)) / total_bid_quantity
        
        # 计算卖盘加权平均价
        ask_prices = [ask[0] for ask in orderbook['asks'][:depth]]
        ask_quantities = [ask[1] for ask in orderbook['asks'][:depth]]
        total_ask_quantity = sum(ask_quantities)
        
        if total_ask_quantity == 0:
            return None
        
        weighted_ask = sum(price * qty for price, qty in zip(ask_prices, ask_quantities)) / total_ask_quantity
        
        return (weighted_bid + weighted_ask) / 2
    
    @staticmethod
    def merge_orderbooks(orderbooks: List[Dict], method: str = 'volume_weighted') -> Dict:
        """合并多个订单簿"""
        if not orderbooks:
            return {}
        
        symbol = orderbooks[0].get('symbol')
        
        if method == 'volume_weighted':
            return DataProcessor._merge_volume_weighted(orderbooks, symbol)
        elif method == 'best_only':
            return DataProcessor._merge_best_only(orderbooks, symbol)
        else:
            raise ValueError(f"未知的合并方法: {method}")
    
    @staticmethod
    def _merge_volume_weighted(orderbooks: List[Dict], symbol: str) -> Dict:
        """按交易量加权合并订单簿"""
        all_bids = []
        all_asks = []
        
        for orderbook in orderbooks:
            all_bids.extend(orderbook.get('bids', []))
            all_asks.extend(orderbook.get('asks', []))
        
        # 按价格分组并累加数量
        bid_dict = {}
        for price, quantity in all_bids:
            bid_dict[price] = bid_dict.get(price, 0) + quantity
        
        ask_dict = {}
        for price, quantity in all_asks:
            ask_dict[price] = ask_dict.get(price, 0) + quantity
        
        # 转换回列表并排序
        merged_bids = sorted([[price, qty] for price, qty in bid_dict.items()], 
                           key=lambda x: x[0], reverse=True)
        merged_asks = sorted([[price, qty] for price, qty in ask_dict.items()], 
                           key=lambda x: x[0])
        
        return {
            'symbol': symbol,
            'bids': merged_bids,
            'asks': merged_asks,
            'timestamp': datetime.now().timestamp(),
            'source': 'merged'
        }
    
    @staticmethod
    def _merge_best_only(orderbooks: List[Dict], symbol: str) -> Dict:
        """只保留最佳买卖价合并订单簿"""
        best_bid = 0
        best_ask = float('inf')
        total_bid_volume = 0
        total_ask_volume = 0
        
        for orderbook in orderbooks:
            if orderbook.get('bids'):
                bid_price = orderbook['bids'][0][0]
                bid_volume = orderbook['bids'][0][1]
                if bid_price > best_bid:
                    best_bid = bid_price
                    total_bid_volume = bid_volume
                elif bid_price == best_bid:
                    total_bid_volume += bid_volume
            
            if orderbook.get('asks'):
                ask_price = orderbook['asks'][0][0]
                ask_volume = orderbook['asks'][0][1]
                if ask_price < best_ask:
                    best_ask = ask_price
                    total_ask_volume = ask_volume
                elif ask_price == best_ask:
                    total_ask_volume += ask_volume
        
        return {
            'symbol': symbol,
            'bids': [[best_bid, total_bid_volume]] if best_bid > 0 else [],
            'asks': [[best_ask, total_ask_volume]] if best_ask < float('inf') else [],
            'timestamp': datetime.now().timestamp(),
            'source': 'merged_best'
        }
    
    @staticmethod
    def serialize_for_storage(data: Dict) -> str:
        """序列化数据用于存储"""
        # 处理Decimal类型
        def decimal_default(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")
        
        return json.dumps(data, default=decimal_default, separators=(',', ':'))
    
    @staticmethod
    def calculate_slippage(orderbook: Dict, quantity: float, side: str) -> float:
        """计算预期滑点"""
        if side.lower() not in ['buy', 'sell']:
            raise ValueError("side must be 'buy' or 'sell'")
        
        levels = orderbook['asks'] if side.lower() == 'buy' else orderbook['bids']
        if not levels:
            return 0.0
        
        remaining_quantity = quantity
        total_cost = 0.0
        base_price = levels[0][0]  # 第一档价格
        
        for price, level_quantity in levels:
            if remaining_quantity <= 0:
                break
            
            fill_quantity = min(remaining_quantity, level_quantity)
            total_cost += fill_quantity * price
            remaining_quantity -= fill_quantity
        
        if quantity == 0:
            return 0.0
        
        avg_price = total_cost / quantity
        slippage = (avg_price - base_price) / base_price * 100
        
        return slippage if side.lower() == 'buy' else -slippage