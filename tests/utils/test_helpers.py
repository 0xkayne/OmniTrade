import asyncio
import json
from typing import Dict, Any

async def wait_for_condition(condition_func, timeout=5, interval=0.1):
    """等待条件成立"""
    elapsed = 0
    while elapsed < timeout:
        if await condition_func():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False

def validate_orderbook_structure(orderbook: Dict[str, Any]) -> bool:
    """验证订单簿结构是否正确"""
    required_keys = ['bids', 'asks']
    if not all(key in orderbook for key in required_keys):
        return False
    
    # 验证bids和asks是列表
    if not isinstance(orderbook['bids'], list) or not isinstance(orderbook['asks'], list):
        return False
    
    # 验证每个价格水平格式正确
    for level in orderbook['bids'] + orderbook['asks']:
        if not isinstance(level, list) or len(level) != 2:
            return False
        if not all(isinstance(x, (int, float)) for x in level):
            return False
    
    return True

def validate_balance_structure(balance: Dict[str, Any]) -> bool:
    """验证余额结构是否正确"""
    # 检查是否有基本的货币字段
    if not balance:
        return False
    
    # 检查每个货币都有必要的字段
    for currency, data in balance.items():
        required_fields = ['free', 'used', 'total']
        if not all(field in data for field in required_fields):
            return False
    
    return True