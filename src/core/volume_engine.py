"""
刷量引擎 - 管理跨交易所对冲刷量
"""
import asyncio
import random
import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from src.core.base_exchange import BaseExchange


@dataclass
class HedgePosition:
    """对冲仓位记录"""
    position_id: str
    symbol: str
    long_exchange: str      # 做多的交易所
    short_exchange: str     # 做空的交易所
    size: float             # 仓位大小
    long_price: float       # 开多价格
    short_price: float      # 开空价格
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None
    status: str = 'open'    # 'open', 'closed', 'partial', 'failed'
    pnl: float = 0.0        # 盈亏
    long_order_id: Optional[str] = None
    short_order_id: Optional[str] = None
    
    def get_spread(self) -> float:
        """计算当前价差（开仓成本）"""
        return abs(self.long_price - self.short_price)
    
    def calculate_cost(self) -> float:
        """计算总开仓成本（考虑价差磨损）"""
        return self.get_spread() * self.size
    
    def get_lifetime_seconds(self) -> float:
        """获取持仓时长（秒）"""
        end_time = self.closed_at if self.closed_at else datetime.now()
        return (end_time - self.opened_at).total_seconds()


class VolumeEngine:
    """刷量引擎 - 管理跨交易所对冲刷量"""
    
    def __init__(
        self,
        exchanges: Dict[str, BaseExchange],
        config: Dict
    ):
        self.exchanges = exchanges
        self.config = config
        self.active_positions: List[HedgePosition] = []
        self.position_history: List[HedgePosition] = []
        self.logger = logging.getLogger('engine.volume')
        self.is_running = False
        
        # 交易对符号映射: {标准符号: {exchange_name: 实际符号}}
        self.symbol_mapping: Dict[str, Dict[str, str]] = {}
        
        # 从配置中提取参数
        timing_config = config.get('timing', {})
        position_config = config.get('position', {})
        risk_config = config.get('risk', {})
        
        # 交易所配置
        configured_exchanges = config.get('exchanges', [])
        if configured_exchanges:
            # 使用配置中指定的交易所（仅保留已连接的）
            self.volume_exchanges = [ex for ex in configured_exchanges if ex in exchanges]
            if not self.volume_exchanges:
                self.logger.warning(f"配置的交易所 {configured_exchanges} 均未连接，将使用所有已连接的交易所")
                self.volume_exchanges = list(exchanges.keys())
        else:
            # 使用所有已连接的交易所
            self.volume_exchanges = list(exchanges.keys())
        
        # 时间配置
        self.min_interval = timing_config.get('min_interval', 30)
        self.max_interval = timing_config.get('max_interval', 600)
        self.min_position_lifetime = timing_config.get('min_position_lifetime', 300)
        self.max_position_lifetime = timing_config.get('max_position_lifetime', 7200)
        
        # 仓位配置
        self.min_size = position_config.get('min_size', 0.001)
        self.max_size = position_config.get('max_size', 0.1)
        self.size_distribution = position_config.get('size_distribution', 'lognormal')
        self.leverage = position_config.get('leverage', 2)  # 默认2倍杠杆
        
        # 风险配置
        self.max_spread_tolerance = risk_config.get('max_spread_tolerance', 0.5)
        self.max_spread_cost = risk_config.get('max_spread_cost', 100)
        self.max_concurrent_positions = risk_config.get('max_concurrent_positions', 10)
        self.daily_max_volume = risk_config.get('daily_max_volume', 1000)
        
        # 统计数据
        self.daily_volume = 0.0
        self.last_reset_date = datetime.now().date()
        
        self.logger.info(
            f"刷量引擎初始化完成 - "
            f"已连接交易所: {len(exchanges)}, "
            f"刷量交易所: {self.volume_exchanges}, "
            f"最大并发仓位: {self.max_concurrent_positions}"
        )
    
    async def _validate_symbols_for_exchanges(self, symbols: List[str]) -> List[str]:
        """
        验证并构建交易对符号映射
        返回: 所有交易所都支持的标准符号列表
        """
        # 清空旧的映射
        self.symbol_mapping = {}
        
        print(f"🔍 开始验证 {len(symbols)} 个交易对...")
        
        for symbol in symbols:
            print(f"\n  检查交易对: {symbol}")
            symbol_map = {}
            
            for ex_name in self.volume_exchanges:
                exchange = self.exchanges[ex_name]
                
                # 检查是否是 CCXT 交易所
                if hasattr(exchange, 'ccxt_exchange') and exchange.ccxt_exchange:
                    ccxt_client = exchange.ccxt_exchange
                    available_markets = ccxt_client.symbols if hasattr(ccxt_client, 'symbols') else []
                    
                    print(f"    {ex_name}: 有 {len(available_markets)} 个市场")
                    
                    if symbol in available_markets:
                        symbol_map[ex_name] = symbol
                        print(f"    {ex_name}: ✅ 直接支持 {symbol}")
                    else:
                        # 尝试常见的符号变体
                        variants = self._generate_symbol_variants(symbol)
                        print(f"    {ex_name}: 尝试变体 {variants}")
                        
                        for variant in variants:
                            if variant in available_markets:
                                msg = f"{ex_name}: 将 {symbol} 映射为 {variant}"
                                print(f"    {ex_name}: ✅ {msg}")
                                self.logger.info(msg)
                                symbol_map[ex_name] = variant
                                break
                        
                        if ex_name not in symbol_map:
                            msg = f"{ex_name} 不支持交易对 {symbol} 及其变体"
                            print(f"    {ex_name}: ❌ {msg}")
                            self.logger.warning(msg)
                else:
                    # 非 CCXT 交易所，假设支持原始符号
                    symbol_map[ex_name] = symbol
                    print(f"    {ex_name}: ✅ (非CCXT交易所，假设支持)")
            
            # 只有当所有刷量交易所都支持该符号时才添加到映射
            if len(symbol_map) == len(self.volume_exchanges):
                self.symbol_mapping[symbol] = symbol_map
                msg = f"✅ {symbol} 映射成功: {symbol_map}"
                print(f"  {msg}")
                self.logger.info(msg)
            else:
                msg = f"⚠️  {symbol} 未被所有交易所支持 (支持: {len(symbol_map)}/{len(self.volume_exchanges)})，跳过"
                print(f"  {msg}")
                self.logger.warning(msg)
        
        result = list(self.symbol_mapping.keys())
        print(f"\n✅ 验证完成，有效交易对: {result}\n")
        return result
    
    def _generate_symbol_variants(self, symbol: str) -> List[str]:
        """生成交易对符号的常见变体"""
        variants = [symbol]
        
        # 常见变体转换规则
        # 1. 基础货币替换：USD <-> USDC
        if '/USD:' in symbol:
            # BTC/USD:USDC -> BTC/USDC:USDC
            variants.append(symbol.replace('/USD:', '/USDC:'))
        elif '/USD' in symbol and ':' not in symbol:
            # BTC/USD -> BTC/USDC
            variants.append(symbol.replace('/USD', '/USDC'))
            # BTC/USD -> BTC/USD:USDC
            variants.append(symbol + ':USDC')
            # BTC/USD -> BTC/USDC:USDC
            variants.append(symbol.replace('/USD', '/USDC') + ':USDC')
        
        if '/USDC:' in symbol:
            # BTC/USDC:USDC -> BTC/USD:USDC
            variants.append(symbol.replace('/USDC:', '/USD:'))
        elif '/USDC' in symbol and ':' not in symbol:
            # BTC/USDC -> BTC/USD
            variants.append(symbol.replace('/USDC', '/USD'))
            # BTC/USDC -> BTC/USD:USDC
            variants.append(symbol.replace('/USDC', '/USD') + ':USDC')
            # BTC/USDC -> BTC/USDC:USDC
            variants.append(symbol + ':USDC')
        
        # 2. 移除结算货币
        if ':' in symbol:
            # BTC/USD:USDC -> BTC/USD
            # BTC/USDC:USDC -> BTC/USDC
            variants.append(symbol.split(':')[0])
        
        # 去重并保持顺序
        return list(dict.fromkeys(variants))
    
    def _get_exchange_symbol(self, standard_symbol: str, exchange_name: str) -> Optional[str]:
        """获取交易所的实际交易对符号"""
        if standard_symbol in self.symbol_mapping:
            return self.symbol_mapping[standard_symbol].get(exchange_name)
        return None
    
    async def _validate_and_adjust_size(
        self,
        size: float,
        long_exchange: str,
        long_symbol: str,
        long_price: float,
        short_exchange: str,
        short_symbol: str,
        short_price: float
    ) -> float:
        """
        验证并调整交易数量以满足所有市场的最小要求
        
        Returns:
            调整后的交易数量
        """
        adjusted_size = size
        
        # 检查多头交易所的要求
        if hasattr(self.exchanges[long_exchange], 'ccxt_exchange'):
            try:
                ccxt_client = self.exchanges[long_exchange].ccxt_exchange
                market = ccxt_client.market(long_symbol)
                limits = market.get('limits', {})
                
                # 检查最小成本要求
                min_cost = limits.get('cost', {}).get('min')
                if min_cost and long_price:
                    required_amount = float(min_cost) / long_price * 1.1  # 加10% buffer
                    if adjusted_size < required_amount:
                        self.logger.info(
                            f"{long_exchange} 最小成本要求: ${min_cost}, "
                            f"需要数量: {required_amount:.6f}"
                        )
                        adjusted_size = max(adjusted_size, required_amount)
                
                # 检查最小数量要求
                min_amount = limits.get('amount', {}).get('min')
                if min_amount:
                    adjusted_size = max(adjusted_size, float(min_amount) * 1.1)
                
                # 应用精度
                adjusted_size = float(ccxt_client.amount_to_precision(long_symbol, adjusted_size))
                
            except Exception as e:
                self.logger.warning(f"获取 {long_exchange} 市场限制失败: {e}")
        
        # 检查空头交易所的要求
        if hasattr(self.exchanges[short_exchange], 'ccxt_exchange'):
            try:
                ccxt_client = self.exchanges[short_exchange].ccxt_exchange
                market = ccxt_client.market(short_symbol)
                limits = market.get('limits', {})
                
                # 检查最小成本要求
                min_cost = limits.get('cost', {}).get('min')
                if min_cost and short_price:
                    required_amount = float(min_cost) / short_price * 1.1  # 加10% buffer
                    if adjusted_size < required_amount:
                        self.logger.info(
                            f"{short_exchange} 最小成本要求: ${min_cost}, "
                            f"需要数量: {required_amount:.6f}"
                        )
                        adjusted_size = max(adjusted_size, required_amount)
                
                # 检查最小数量要求
                min_amount = limits.get('amount', {}).get('min')
                if min_amount:
                    adjusted_size = max(adjusted_size, float(min_amount) * 1.1)
                
                # 应用精度
                adjusted_size = float(ccxt_client.amount_to_precision(short_symbol, adjusted_size))
                
            except Exception as e:
                self.logger.warning(f"获取 {short_exchange} 市场限制失败: {e}")
        
        return adjusted_size
    
    async def _set_leverage(self, exchange_name: str, symbol: str, leverage: int) -> bool:
        """
        为指定交易所的交易对设置杠杆倍数
        
        Args:
            exchange_name: 交易所名称
            symbol: 交易对符号
            leverage: 杠杆倍数
            
        Returns:
            bool: 是否设置成功
        """
        try:
            exchange = self.exchanges[exchange_name]
            
            # 检查是否是 CCXT 交易所
            if hasattr(exchange, 'ccxt_exchange') and exchange.ccxt_exchange:
                ccxt_client = exchange.ccxt_exchange
                
                # 检查交易所是否支持设置杠杆
                if hasattr(ccxt_client, 'set_leverage'):
                    await ccxt_client.set_leverage(leverage, symbol)
                    self.logger.info(f"✅ {exchange_name} 设置杠杆成功: {symbol} -> {leverage}x")
                    return True
                else:
                    self.logger.debug(f"{exchange_name} 不支持 set_leverage 方法")
                    return False
            else:
                self.logger.debug(f"{exchange_name} 不是 CCXT 交易所，跳过杠杆设置")
                return False
                
        except Exception as e:
            # 某些交易所可能不支持或已经有默认杠杆，不作为错误处理
            self.logger.debug(f"{exchange_name} 设置杠杆时出现异常 ({symbol}, {leverage}x): {e}")
            return False
    
    async def start_volume_farming(self, symbols: List[str]):
        """启动刷量任务"""
        print(f"🔄 验证交易对符号映射 - 配置交易对: {symbols}")
        self.logger.info(f"🔄 开始刷量任务 - 配置交易对: {symbols}")
        
        # 验证并构建交易对符号映射
        valid_symbols = await self._validate_symbols_for_exchanges(symbols)
        
        if not valid_symbols:
            error_msg = "❌ 没有可用的交易对进行刷量（所有交易对都不被支持）"
            print(error_msg)
            self.logger.error(error_msg)
            return
        
        print(f"✅ 符号映射完成 - 有效交易对: {valid_symbols}")
        print(f"📋 符号映射表: {self.symbol_mapping}")
        self.logger.info(f"✅ 开始刷量 - 有效交易对: {valid_symbols}")
        self.is_running = True
        
        # 启动两个并发任务
        print("🚀 启动刷量循环和仓位管理循环...")
        await asyncio.gather(
            self._farming_loop(valid_symbols),
            self._position_manager_loop(),
            return_exceptions=True
        )
        print("🛑 刷量循环已停止")
    
    async def _farming_loop(self, symbols: List[str]):
        """刷量主循环"""
        print(f"💫 刷量循环已启动 - 交易对: {symbols}")
        iteration = 0
        while self.is_running:
            try:
                # 循环开始时立即检查停止标志
                if not self.is_running:
                    print("⚠️  收到停止信号，退出刷量循环")
                    break
                
                iteration += 1
                print(f"\n{'='*60}")
                print(f"🔄 刷量循环 #{iteration}")
                print(f"{'='*60}")
                
                # 检查每日限额
                self._check_daily_reset()
                if self.daily_volume >= self.daily_max_volume:
                    msg = f"已达到每日交易量限额 {self.daily_max_volume}, 等待明日..."
                    print(f"⚠️  {msg}")
                    self.logger.warning(msg)
                    # 等待1小时，但每分钟检查一次是否停止
                    for _ in range(60):
                        if not self.is_running:
                            break
                        await asyncio.sleep(60)
                    continue
                
                # 检查并发仓位限制
                if len(self.active_positions) >= self.max_concurrent_positions:
                    msg = f"已达到最大并发仓位数 {self.max_concurrent_positions}, 等待..."
                    print(f"⚠️  {msg}")
                    self.logger.info(msg)
                    # 等待30秒，但每秒检查一次是否停止
                    for _ in range(30):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    continue
                
                # 随机选择交易对
                symbol = random.choice(symbols)
                print(f"📊 选择交易对: {symbol}")
                
                # 随机选择两个交易所组合
                exchange_pair = self._select_exchange_pair()
                if not exchange_pair:
                    msg = "⚠️  没有足够的交易所进行对冲，等待..."
                    print(msg)
                    self.logger.warning(msg)
                    # 等待10秒，但每秒检查一次是否停止
                    for _ in range(10):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    continue
                
                # 智能检查价差并决定最优开仓方向
                print(f"🔍 检查价差并选择最优方向...")
                spread_check = await self._check_spread_and_determine_direction(symbol, exchange_pair)
                
                if not spread_check['acceptable']:
                    reason = spread_check.get('reason', '未知')
                    msg = (f"⚠️  {symbol} 价差检查失败: {reason}")
                    print(msg)
                    self.logger.info(msg)
                    # 随机等待5-15秒，但每秒检查一次是否停止
                    wait_time = random.uniform(5, 15)
                    elapsed = 0
                    while elapsed < wait_time and self.is_running:
                        await asyncio.sleep(1)
                        elapsed += 1
                    continue
                
                # 使用智能选择的方向
                long_ex = spread_check['long_exchange']
                short_ex = spread_check['short_exchange']
                
                # 显示优化后的方向选择
                cost_adv = spread_check.get('cost_advantage', 0)
                if cost_adv < 0:
                    print(f"🏦 最优方向: {long_ex} (多头) <-> {short_ex} (空头) | 💰 预期收益: ${abs(cost_adv):.4f}")
                else:
                    print(f"🏦 最优方向: {long_ex} (多头) <-> {short_ex} (空头) | 💸 成本: ${cost_adv:.4f}")
                
                print(f"✅ 价差: {spread_check['spread_pct']:.3f}%")
                
                # 生成随机仓位大小
                size = self._generate_random_size()
                print(f"📏 生成仓位大小: {size}")
                
                # 执行对冲开仓
                print(f"💰 执行对冲开仓...")
                position = await self._execute_hedge_open(
                    symbol, long_ex, short_ex, size
                )
                
                if position:
                    self.active_positions.append(position)
                    self.daily_volume += size
                    msg = (
                        f"✅ 开启对冲仓位: {position.position_id}\n"
                        f"   成本: ${position.calculate_cost():.4f}\n"
                        f"   今日累计: {self.daily_volume:.2f}/{self.daily_max_volume}"
                    )
                    print(msg)
                    self.logger.info(msg)
                else:
                    print("❌ 开仓失败")
                
                # 随机等待下一次开仓 - 支持快速中断
                wait_time = random.uniform(self.min_interval, self.max_interval)
                msg = f"⏳ 等待 {wait_time:.1f} 秒后继续..."
                print(msg)
                self.logger.info(msg)
                
                # 每秒检查一次停止标志
                for i in range(int(wait_time)):
                    if not self.is_running:
                        print("⚠️  收到停止信号，退出刷量循环")
                        self.logger.info("收到停止信号，退出刷量循环")
                        return  # 直接返回，退出整个方法
                    await asyncio.sleep(1)
                    # 每30秒显示一次剩余等待时间
                    if i > 0 and i % 30 == 0:
                        remaining = wait_time - i
                        print(f"⏳ 还剩 {remaining:.0f} 秒...")
                
                # 处理不足1秒的剩余时间
                remaining = wait_time - int(wait_time)
                if remaining > 0 and self.is_running:
                    await asyncio.sleep(remaining)
                
                # 最后再检查一次是否收到停止信号
                if not self.is_running:
                    print("⚠️  收到停止信号，退出刷量循环")
                    return
                
            except Exception as e:
                import traceback
                error_msg = f"❌ 刷量循环错误: {e}\n{traceback.format_exc()}"
                print(error_msg)
                self.logger.error(f"刷量循环错误: {e}", exc_info=True)
                # 等待10秒后重试，但每秒检查一次是否停止
                for _ in range(10):
                    if not self.is_running:
                        break
                    await asyncio.sleep(1)
        
        print("✅ 刷量循环已正常退出")
    
    async def _position_manager_loop(self):
        """仓位管理循环 - 负责检查和关闭仓位"""
        print("🔧 仓位管理循环已启动")
        while self.is_running:
            try:
                # 等待30秒，但每秒检查一次是否停止
                for _ in range(30):
                    if not self.is_running:
                        break
                    await asyncio.sleep(1)
                
                if self.is_running:  # 只有在仍在运行时才检查仓位
                    await self._check_and_close_positions()
            except Exception as e:
                import traceback
                error_msg = f"❌ 仓位管理循环错误: {e}\n{traceback.format_exc()}"
                print(error_msg)
                self.logger.error(f"仓位管理循环错误: {e}", exc_info=True)
        
        print("✅ 仓位管理循环已正常退出")
    
    def _check_daily_reset(self):
        """检查是否需要重置每日统计"""
        today = datetime.now().date()
        if today > self.last_reset_date:
            self.logger.info(
                f"每日统计重置 - 昨日交易量: {self.daily_volume:.2f}"
            )
            self.daily_volume = 0.0
            self.last_reset_date = today
    
    def _select_exchange_pair(self) -> Optional[Tuple[str, str]]:
        """
        根据配置选择交易所对（不决定方向）
        - 如果配置的交易所 <= 2个，则使用这些交易所进行对冲
        - 如果配置的交易所 >= 3个，则随机选择其中2个
        - 方向将由价差检查方法根据价格优势决定
        """
        available_exchanges = self.volume_exchanges
        
        if len(available_exchanges) < 2:
            self.logger.warning(f"可用交易所不足2个: {available_exchanges}")
            return None
        
        if len(available_exchanges) == 2:
            # 正好2个交易所，直接使用
            selected = list(available_exchanges)
        else:
            # 3个或更多交易所，随机选择2个
            selected = random.sample(available_exchanges, 2)
        
        # 不再随机决定方向，保持原始顺序返回
        return tuple(selected)
    
    def _generate_random_size(self) -> float:
        """
        生成随机仓位大小
        使用对数均匀分布或对数正态分布，避免女巫检测
        """
        if self.size_distribution == 'lognormal':
            # 对数正态分布
            log_mean = (math.log(self.min_size) + math.log(self.max_size)) / 2
            log_std = (math.log(self.max_size) - math.log(self.min_size)) / 6
            size = random.lognormvariate(log_mean, log_std)
            # 限制在范围内
            size = max(self.min_size, min(self.max_size, size))
        else:
            # 对数均匀分布（默认）
            log_min = math.log(self.min_size)
            log_max = math.log(self.max_size)
            random_log = random.uniform(log_min, log_max)
            size = math.exp(random_log)
        
        # 添加一些噪音，让大小看起来更"自然"
        noise = random.uniform(0.95, 1.05)
        size = size * noise
        
        # 四舍五入到合理的精度
        return round(size, 6)
    
    async def _check_spread_and_determine_direction(
        self,
        symbol: str,
        exchange_pair: Tuple[str, str]
    ) -> Dict:
        """
        检查价差并智能决定开仓方向
        
        策略：价格低的交易所做多（买入），价格高的交易所做空（卖出）
        这样可以利用价差，减少刷量成本，甚至可能获利
        
        Args:
            symbol: 标准交易对符号
            exchange_pair: 两个交易所（顺序无关）
            
        Returns:
            Dict包含: acceptable, long_exchange, short_exchange, spread_pct, long_price, short_price, reason
        """
        ex1, ex2 = exchange_pair
        
        try:
            # 获取实际符号
            symbol1 = self._get_exchange_symbol(symbol, ex1)
            symbol2 = self._get_exchange_symbol(symbol, ex2)
            
            if not symbol1 or not symbol2:
                return {
                    'acceptable': False,
                    'spread_pct': 999.0,
                    'reason': f'符号映射失败: {symbol}'
                }
            
            # 并发获取订单簿
            ob1, ob2 = await asyncio.gather(
                self.exchanges[ex1].fetch_orderbook(symbol1),
                self.exchanges[ex2].fetch_orderbook(symbol2)
            )
            
            # 检查订单簿有效性
            if (not ob1.get('asks') or not ob1.get('bids') or 
                not ob2.get('asks') or not ob2.get('bids') or
                len(ob1['asks']) == 0 or len(ob1['bids']) == 0 or
                len(ob2['asks']) == 0 or len(ob2['bids']) == 0):
                return {
                    'acceptable': False,
                    'spread_pct': 999.0,
                    'reason': '订单簿为空或无效'
                }
            
            # 获取价格
            ex1_buy_price = ob1['asks'][0][0]   # 在ex1买入的价格
            ex1_sell_price = ob1['bids'][0][0]  # 在ex1卖出的价格
            ex2_buy_price = ob2['asks'][0][0]   # 在ex2买入的价格
            ex2_sell_price = ob2['bids'][0][0]  # 在ex2卖出的价格
            
            # 计算两种方案的成本/收益
            # 方案1: ex1做多(买入), ex2做空(卖出)
            # 成本 = 买入价 - 卖出价（负值表示有收益）
            cost1 = ex1_buy_price - ex2_sell_price
            spread1 = abs(cost1)
            spread1_pct = (spread1 / ((ex1_buy_price + ex2_sell_price) / 2)) * 100
            
            # 方案2: ex2做多(买入), ex1做空(卖出)
            cost2 = ex2_buy_price - ex1_sell_price
            spread2 = abs(cost2)
            spread2_pct = (spread2 / ((ex2_buy_price + ex1_sell_price) / 2)) * 100
            
            # 选择成本更低（或收益更高）的方案
            if cost1 <= cost2:
                # 方案1更优
                long_exchange = ex1
                short_exchange = ex2
                long_price = ex1_buy_price
                short_price = ex2_sell_price
                spread = cost1
                spread_pct = spread1_pct
                direction_note = f"{ex1}价格更低，做多"
            else:
                # 方案2更优
                long_exchange = ex2
                short_exchange = ex1
                long_price = ex2_buy_price
                short_price = ex1_sell_price
                spread = cost2
                spread_pct = spread2_pct
                direction_note = f"{ex2}价格更低，做多"
            
            acceptable = spread_pct <= self.max_spread_tolerance
            
            if not acceptable:
                reason = f'价差 {spread_pct:.3f}% 超过最大容忍度 {self.max_spread_tolerance:.3f}%'
            else:
                reason = f'{direction_note}, 价差 {spread_pct:.3f}% 可接受'
            
            return {
                'acceptable': acceptable,
                'long_exchange': long_exchange,
                'short_exchange': short_exchange,
                'spread_pct': spread_pct,
                'long_price': long_price,
                'short_price': short_price,
                'spread': spread,
                'reason': reason,
                'cost_advantage': min(cost1, cost2)  # 负值表示有利润
            }
            
        except Exception as e:
            import traceback
            error_detail = f"{type(e).__name__}: {str(e)}"
            error_trace = traceback.format_exc()
            
            # 同时输出到控制台和日志
            print(f"❌ 价差检查异常 ({ex1}-{ex2}): {error_detail}")
            self.logger.error(f"检查价差失败: {error_detail}\n{error_trace}")
            
            return {'acceptable': False, 'spread_pct': 999.0, 'reason': error_detail}
    
    async def _check_spread_acceptable(
        self, 
        symbol: str, 
        long_exchange: str, 
        short_exchange: str
    ) -> Dict:
        """检查价差是否可接受（已弃用，保留用于兼容性）"""
        try:
            # 获取每个交易所的实际符号
            long_symbol = self._get_exchange_symbol(symbol, long_exchange)
            short_symbol = self._get_exchange_symbol(symbol, short_exchange)
            
            if not long_symbol or not short_symbol:
                return {
                    'acceptable': False, 
                    'spread_pct': 999.0, 
                    'reason': f'符号映射失败: {symbol}'
                }
            
            # 并发获取两个交易所的订单簿
            long_orderbook_task = self.exchanges[long_exchange].fetch_orderbook(long_symbol)
            short_orderbook_task = self.exchanges[short_exchange].fetch_orderbook(short_symbol)
            
            long_orderbook, short_orderbook = await asyncio.gather(
                long_orderbook_task, short_orderbook_task
            )
            
            # 检查订单簿是否有效
            if (not long_orderbook.get('asks') or 
                not short_orderbook.get('bids') or
                len(long_orderbook['asks']) == 0 or 
                len(short_orderbook['bids']) == 0):
                reason = f'订单簿为空或无效: long_asks={len(long_orderbook.get("asks", []))}, short_bids={len(short_orderbook.get("bids", []))}'
                return {'acceptable': False, 'spread_pct': 999.0, 'reason': reason}
            
            # 获取价格
            long_price = long_orderbook['asks'][0][0]  # 做多需要买入
            short_price = short_orderbook['bids'][0][0]  # 做空需要卖出
            
            # 计算价差百分比
            mid_price = (long_price + short_price) / 2
            spread = abs(long_price - short_price)
            spread_pct = (spread / mid_price) * 100
            
            acceptable = spread_pct <= self.max_spread_tolerance
            
            # 生成原因说明
            if not acceptable:
                reason = f'价差 {spread_pct:.3f}% 超过最大容忍度 {self.max_spread_tolerance:.3f}%'
            else:
                reason = f'价差 {spread_pct:.3f}% 在可接受范围内'
            
            return {
                'acceptable': acceptable,
                'spread_pct': spread_pct,
                'long_price': long_price,
                'short_price': short_price,
                'spread': spread,
                'reason': reason
            }
            
        except Exception as e:
            import traceback
            error_detail = f"{type(e).__name__}: {str(e)}"
            error_trace = traceback.format_exc()
            
            # 同时输出到控制台和日志
            print(f"❌ 价差检查异常 ({long_exchange}-{short_exchange}): {error_detail}")
            self.logger.error(f"检查价差失败: {error_detail}\n{error_trace}")
            
            return {'acceptable': False, 'spread_pct': 999.0, 'reason': error_detail}
    
    async def _fetch_position_info(self, exchange_name: str, symbol: str) -> Optional[Dict]:
        """
        查询交易所的仓位信息
        返回格式化的仓位信息，如果查询失败则返回 None
        """
        try:
            exchange = self.exchanges[exchange_name]
            
            # 检查是否是 CCXT 交易所
            if hasattr(exchange, 'ccxt_exchange') and exchange.ccxt_exchange:
                ccxt_client = exchange.ccxt_exchange
                
                # 尝试获取仓位信息
                try:
                    positions = await ccxt_client.fetch_positions([symbol])
                    
                    if positions:
                        # 找到对应交易对的仓位
                        for pos in positions:
                            if pos.get('symbol') == symbol:
                                side = pos.get('side', 'unknown')
                                size = pos.get('contracts', 0) or pos.get('contractSize', 0)
                                notional = pos.get('notional', 0)
                                entry_price = pos.get('entryPrice', 0)
                                unrealized_pnl = pos.get('unrealizedPnl', 0)
                                
                                return {
                                    'exchange': exchange_name,
                                    'symbol': symbol,
                                    'side': side,
                                    'size': size,
                                    'notional': notional,
                                    'entry_price': entry_price,
                                    'unrealized_pnl': unrealized_pnl
                                }
                        
                        # 如果没有找到仓位，返回空仓
                        return {
                            'exchange': exchange_name,
                            'symbol': symbol,
                            'side': 'none',
                            'size': 0,
                            'notional': 0,
                            'entry_price': 0,
                            'unrealized_pnl': 0
                        }
                    else:
                        # 没有仓位
                        return {
                            'exchange': exchange_name,
                            'symbol': symbol,
                            'side': 'none',
                            'size': 0,
                            'notional': 0,
                            'entry_price': 0,
                            'unrealized_pnl': 0
                        }
                except Exception as e:
                    self.logger.warning(f"查询 {exchange_name} 仓位失败: {e}")
                    return None
            else:
                self.logger.warning(f"{exchange_name} 不支持仓位查询（非CCXT交易所）")
                return None
                
        except Exception as e:
            self.logger.error(f"查询仓位信息出错 {exchange_name}: {e}")
            return None
    
    def _format_position_info(self, pos_info: Optional[Dict]) -> str:
        """格式化仓位信息为可读字符串"""
        if not pos_info:
            return "查询失败"
        
        if pos_info['side'] == 'none':
            return f"无仓位"
        
        # 确保所有数值字段都是数字类型
        try:
            size = float(pos_info['size']) if pos_info['size'] else 0
            entry_price = float(pos_info['entry_price']) if pos_info['entry_price'] else 0
            notional = float(pos_info['notional']) if pos_info['notional'] else 0
            unrealized_pnl = float(pos_info['unrealized_pnl']) if pos_info['unrealized_pnl'] else 0
            
            return (
                f"方向: {pos_info['side']}, "
                f"数量: {size:.6f}, "
                f"入场价: ${entry_price:.2f}, "
                f"名义价值: ${notional:.2f}, "
                f"未实现盈亏: ${unrealized_pnl:.4f}"
            )
        except (ValueError, TypeError) as e:
            return f"格式化失败: {e}"
    
    async def _execute_hedge_open(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        size: float
    ) -> Optional[HedgePosition]:
        """执行对冲开仓"""
        position_id = f"{symbol.replace('/', '').replace(':', '')}_{long_exchange}_{short_exchange}_{int(datetime.now().timestamp())}"
        
        try:
            # 获取每个交易所的实际符号
            long_symbol = self._get_exchange_symbol(symbol, long_exchange)
            short_symbol = self._get_exchange_symbol(symbol, short_exchange)
            
            if not long_symbol or not short_symbol:
                self.logger.error(f"符号映射失败: {symbol}")
                return None
            
            self.logger.info(
                f"准备开仓: {position_id} | "
                f"Long@{long_exchange}({long_symbol}) | Short@{short_exchange}({short_symbol}) | Size: {size}"
            )
            
            # 获取当前价格（用于某些交易所的市价单和验证最小成本）
            try:
                long_orderbook = await self.exchanges[long_exchange].fetch_orderbook(long_symbol, limit=1)
                short_orderbook = await self.exchanges[short_exchange].fetch_orderbook(short_symbol, limit=1)
                long_price = long_orderbook['asks'][0][0] if long_orderbook.get('asks') else None
                short_price = short_orderbook['bids'][0][0] if short_orderbook.get('bids') else None
            except Exception as e:
                self.logger.error(f"获取价格失败: {e}")
                return None
            
            # 验证并调整交易数量以满足市场限制
            original_size = size
            size = await self._validate_and_adjust_size(
                size, 
                long_exchange, long_symbol, long_price,
                short_exchange, short_symbol, short_price
            )
            
            if size != original_size:
                print(f"📐 数量已调整: {original_size:.6f} -> {size:.6f} (满足市场要求)")
                self.logger.info(f"数量已调整: {original_size:.6f} -> {size:.6f}")
            
            # 设置杠杆倍数
            print(f"⚙️  设置杠杆倍数: {self.leverage}x")
            await asyncio.gather(
                self._set_leverage(long_exchange, long_symbol, self.leverage),
                self._set_leverage(short_exchange, short_symbol, self.leverage),
                return_exceptions=True
            )
            
            # 并发执行两边开仓
            # 注意：Paradex 的市价单不能传 price，Hyperliquid 必须传 price
            if long_exchange == 'hyperliquid':
                long_task = self.exchanges[long_exchange].create_order(
                    long_symbol, 'market', 'buy', size, price=long_price
                )
            else:
                long_task = self.exchanges[long_exchange].create_order(
                    long_symbol, 'market', 'buy', size
                )
            
            if short_exchange == 'hyperliquid':
                short_task = self.exchanges[short_exchange].create_order(
                    short_symbol, 'market', 'sell', size, price=short_price
                )
            else:
                short_task = self.exchanges[short_exchange].create_order(
                    short_symbol, 'market', 'sell', size
                )
            
            results = await asyncio.gather(
                long_task, short_task, return_exceptions=True
            )
            long_order, short_order = results
            
            # 检查订单是否都成功
            if isinstance(long_order, Exception):
                self.logger.error(f"开多失败 {long_exchange}: {long_order}")
                # 如果空头已经成功，需要立即平掉
                if not isinstance(short_order, Exception):
                    self.logger.warning("空头成功但多头失败，立即平掉空头")
                    await self._emergency_close_order(short_exchange, short_symbol, 'buy', size)
                return None
            
            if isinstance(short_order, Exception):
                self.logger.error(f"开空失败 {short_exchange}: {short_order}")
                # 如果多头已经成功，需要立即平掉
                if not isinstance(long_order, Exception):
                    self.logger.warning("多头成功但空头失败，立即平掉多头")
                    await self._emergency_close_order(long_exchange, long_symbol, 'sell', size)
                return None
            
            # 提取成交价格 - 使用订单簿价格作为后备
            long_price = long_order.get('average') or long_order.get('price') or long_price
            short_price = short_order.get('average') or short_order.get('price') or short_price
            
            # 确保价格不为 None
            if long_price is None or short_price is None:
                self.logger.error(
                    f"无法获取成交价格: long_price={long_price}, short_price={short_price}"
                )
                # 尝试回滚 - 平掉已开的仓位
                if not isinstance(long_order, Exception):
                    await self._emergency_close_order(long_exchange, long_symbol, 'sell', size)
                if not isinstance(short_order, Exception):
                    await self._emergency_close_order(short_exchange, short_symbol, 'buy', size)
                return None
            
            # 创建仓位记录
            position = HedgePosition(
                position_id=position_id,
                symbol=symbol,
                long_exchange=long_exchange,
                short_exchange=short_exchange,
                size=size,
                long_price=float(long_price),
                short_price=float(short_price),
                opened_at=datetime.now(),
                status='open',
                long_order_id=long_order.get('id'),
                short_order_id=short_order.get('id')
            )
            
            self.logger.info(
                f"✅ 对冲开仓成功: {position_id}\n"
                f"   Long@{long_exchange}: {position.long_price:.4f}\n"
                f"   Short@{short_exchange}: {position.short_price:.4f}\n"
                f"   Size: {size}\n"
                f"   Spread Cost: ${position.calculate_cost():.4f}"
            )
            
            # Paradex 的市价单是异步成交的，需要延迟查询
            # 如果订单 filled=0 但状态是 open，等待并重新查询
            paradex_used = False
            
            if long_exchange == 'paradex' and long_order.get('filled') == 0 and long_order.get('status') == 'open':
                paradex_used = True
                if long_order.get('id'):
                    await asyncio.sleep(3)  # 等待订单成交
                    try:
                        long_order = await self.exchanges[long_exchange].fetch_order(long_order['id'], long_symbol)
                        self.logger.info(f"Paradex 多头订单查询后: filled={long_order.get('filled')}, status={long_order.get('status')}")
                    except Exception as e:
                        self.logger.warning(f"查询 Paradex 多头订单失败: {e}")
            
            if short_exchange == 'paradex' and short_order.get('filled') == 0 and short_order.get('status') == 'open':
                paradex_used = True
                if short_order.get('id'):
                    await asyncio.sleep(3)  # 等待订单成交  
                    try:
                        short_order = await self.exchanges[short_exchange].fetch_order(short_order['id'], short_symbol)
                        self.logger.info(f"Paradex 空头订单查询后: filled={long_order.get('filled')}, status={short_order.get('status')}")
                    except Exception as e:
                        self.logger.warning(f"查询 Paradex 空头订单失败: {e}")
            
            # 获取订单实际成交量（如果没有 filled 字段或为 None，使用预期的 size）
            long_filled = long_order.get('filled') if long_order.get('filled') is not None else size
            short_filled = short_order.get('filled') if short_order.get('filled') is not None else size
            
            # 检查本次成交量是否匹配
            if abs(long_filled - short_filled) > 0.001:  # 容忍 0.001 的差异
                warning_msg = f"⚠️  警告：本次开仓数量不匹配！Long={long_filled:.6f}, Short={short_filled:.6f}"
                print(warning_msg)
                self.logger.warning(warning_msg)
            
            # 查询并输出实际仓位信息
            print(f"📊 查询开仓后的实际仓位...")
            self.logger.info("查询开仓后的实际仓位...")
            
            long_pos_info = await self._fetch_position_info(long_exchange, long_symbol)
            short_pos_info = await self._fetch_position_info(short_exchange, short_symbol)
            
            long_pos_str = self._format_position_info(long_pos_info)
            short_pos_str = self._format_position_info(short_pos_info)
            
            position_summary = (
                f"📊 开仓后仓位情况:\n"
                f"   本次开仓数量: Long={long_filled:.6f}, Short={short_filled:.6f}\n"
                f"   {long_exchange}@{long_symbol} 总仓位: {long_pos_str}\n"
                f"   {short_exchange}@{short_symbol} 总仓位: {short_pos_str}"
            )
            print(position_summary)
            self.logger.info(position_summary)
            
            # 如果使用了 Paradex，额外等待确保订单完全结算
            # 避免快速连续下单导致后续订单被取消
            if paradex_used:
                cooldown_time = 5  # 5秒冷却时间
                print(f"⏸️  Paradex 订单结算中，等待 {cooldown_time} 秒...")
                self.logger.info(f"Paradex 订单结算冷却: {cooldown_time}秒")
                await asyncio.sleep(cooldown_time)
            
            return position
            
        except Exception as e:
            self.logger.error(f"执行对冲开仓失败: {e}", exc_info=True)
            return None
    
    async def _emergency_close_order(
        self, 
        exchange: str, 
        symbol: str, 
        side: str, 
        size: float
    ):
        """紧急平仓（当对冲的一边失败时）"""
        try:
            self.logger.warning(f"执行紧急平仓: {exchange} {symbol} {side} {size}")
            
            # Paradex 的市价单不能传 price，Hyperliquid 必须传 price
            if exchange == 'hyperliquid':
                # 获取当前价格
                orderbook = await self.exchanges[exchange].fetch_orderbook(symbol, limit=1)
                if side == 'buy':
                    price = orderbook['asks'][0][0] if orderbook.get('asks') else None
                else:  # sell
                    price = orderbook['bids'][0][0] if orderbook.get('bids') else None
                
                await self.exchanges[exchange].create_order(
                    symbol, 'market', side, size, price=price
                )
            else:
                # Paradex 等其他交易所，市价单不传 price
                await self.exchanges[exchange].create_order(
                    symbol, 'market', side, size
                )
        except Exception as e:
            self.logger.error(f"紧急平仓失败: {e}", exc_info=True)
    
    async def _check_and_close_positions(self):
        """检查并关闭过期仓位"""
        if not self.active_positions:
            return
        
        now = datetime.now()
        positions_to_close = []
        
        for position in self.active_positions:
            lifetime = position.get_lifetime_seconds()
            
            # 检查是否达到最大持仓时间
            if lifetime >= self.max_position_lifetime:
                positions_to_close.append(position)
                self.logger.info(
                    f"仓位 {position.position_id} 达到最大持仓时间 {lifetime:.0f}s, 准备平仓"
                )
            # 检查是否超过最小持仓时间，并使用概率决定是否平仓
            elif lifetime >= self.min_position_lifetime:
                # 随机概率平仓（持仓时间越长概率越大）
                time_factor = (lifetime - self.min_position_lifetime) / (
                    self.max_position_lifetime - self.min_position_lifetime
                )
                close_probability = time_factor * 0.3  # 最高30%概率
                
                if random.random() < close_probability:
                    positions_to_close.append(position)
                    self.logger.info(
                        f"仓位 {position.position_id} 随机触发平仓 (lifetime: {lifetime:.0f}s)"
                    )
        
        # 批量平仓
        for position in positions_to_close:
            await self._execute_hedge_close(position)
    
    async def _execute_hedge_close(self, position: HedgePosition):
        """执行对冲平仓"""
        try:
            self.logger.info(f"准备平仓: {position.position_id}")
            
            # 获取每个交易所的实际符号
            long_symbol = self._get_exchange_symbol(position.symbol, position.long_exchange)
            short_symbol = self._get_exchange_symbol(position.symbol, position.short_exchange)
            
            if not long_symbol or not short_symbol:
                self.logger.error(f"平仓失败: 符号映射失败 {position.symbol}")
                position.status = 'failed'
                return
            
            # 获取当前价格
            try:
                long_orderbook = await self.exchanges[position.long_exchange].fetch_orderbook(long_symbol, limit=1)
                short_orderbook = await self.exchanges[position.short_exchange].fetch_orderbook(short_symbol, limit=1)
                long_close_price = long_orderbook['bids'][0][0] if long_orderbook.get('bids') else None
                short_close_price = short_orderbook['asks'][0][0] if short_orderbook.get('asks') else None
            except Exception as e:
                self.logger.error(f"获取平仓价格失败: {e}")
                position.status = 'failed'
                return
            
            # 反向操作：平多头和平空头
            # 注意：Paradex 的市价单不能传 price，Hyperliquid 必须传 price
            if position.long_exchange == 'hyperliquid':
                close_long_task = self.exchanges[position.long_exchange].create_order(
                    long_symbol, 'market', 'sell', position.size, price=long_close_price
                )
            else:
                close_long_task = self.exchanges[position.long_exchange].create_order(
                    long_symbol, 'market', 'sell', position.size
                )
            
            if position.short_exchange == 'hyperliquid':
                close_short_task = self.exchanges[position.short_exchange].create_order(
                    short_symbol, 'market', 'buy', position.size, price=short_close_price
                )
            else:
                close_short_task = self.exchanges[position.short_exchange].create_order(
                    short_symbol, 'market', 'buy', position.size
                )
            
            results = await asyncio.gather(
                close_long_task, close_short_task, return_exceptions=True
            )
            
            close_long_order, close_short_order = results
            
            # Paradex 平仓订单也需要延迟查询（异步成交）
            if position.long_exchange == 'paradex' and not isinstance(close_long_order, Exception):
                if close_long_order.get('filled') == 0 and close_long_order.get('status') == 'open':
                    if close_long_order.get('id'):
                        await asyncio.sleep(3)
                        try:
                            close_long_order = await self.exchanges[position.long_exchange].fetch_order(
                                close_long_order['id'], long_symbol
                            )
                            self.logger.info(f"Paradex 平多头查询后: filled={close_long_order.get('filled')}, status={close_long_order.get('status')}")
                        except Exception as e:
                            self.logger.warning(f"查询 Paradex 平多头订单失败: {e}")
            
            if position.short_exchange == 'paradex' and not isinstance(close_short_order, Exception):
                if close_short_order.get('filled') == 0 and close_short_order.get('status') == 'open':
                    if close_short_order.get('id'):
                        await asyncio.sleep(3)
                        try:
                            close_short_order = await self.exchanges[position.short_exchange].fetch_order(
                                close_short_order['id'], short_symbol
                            )
                            self.logger.info(f"Paradex 平空头查询后: filled={close_short_order.get('filled')}, status={close_short_order.get('status')}")
                        except Exception as e:
                            self.logger.warning(f"查询 Paradex 平空头订单失败: {e}")
            
            # 计算盈亏（不考虑手续费的理论盈亏）
            if not isinstance(close_long_order, Exception) and not isinstance(close_short_order, Exception):
                # 从订单中获取成交价格，如果没有则使用订单簿价格
                order_long_price = close_long_order.get('average') or close_long_order.get('price')
                order_short_price = close_short_order.get('average') or close_short_order.get('price')
                
                final_long_close_price = order_long_price if order_long_price is not None else long_close_price
                final_short_close_price = order_short_price if order_short_price is not None else short_close_price
                
                # 如果价格仍为 None，使用 0 避免错误
                if final_long_close_price is None or final_short_close_price is None:
                    self.logger.warning(f"无法获取平仓价格: long={final_long_close_price}, short={final_short_close_price}，跳过 PnL 计算")
                    position.pnl = 0.0
                else:
                    # 多头盈亏 = (平仓价 - 开仓价) * 仓位
                    # 空头盈亏 = (开仓价 - 平仓价) * 仓位
                    long_pnl = (float(final_long_close_price) - position.long_price) * position.size
                    short_pnl = (position.short_price - float(final_short_close_price)) * position.size
                    position.pnl = long_pnl + short_pnl
            else:
                # 有订单失败，PnL 设为 0
                self.logger.warning(f"平仓订单有异常，跳过 PnL 计算")
                position.pnl = 0.0
            
            # 更新仓位状态并移动到历史记录
            position.status = 'closed'
            position.closed_at = datetime.now()
            self.active_positions.remove(position)
            self.position_history.append(position)
            
            self.logger.info(
                f"✅ 平仓完成: {position.position_id} | "
                f"持仓时长: {position.get_lifetime_seconds():.0f}s | "
                f"PnL: ${position.pnl:.4f}"
            )
            
            # 查询并输出平仓后的实际仓位信息
            print(f"📊 查询平仓后的实际仓位...")
            self.logger.info("查询平仓后的实际仓位...")
            
            long_pos_info = await self._fetch_position_info(position.long_exchange, long_symbol)
            short_pos_info = await self._fetch_position_info(position.short_exchange, short_symbol)
            
            long_pos_str = self._format_position_info(long_pos_info)
            short_pos_str = self._format_position_info(short_pos_info)
            
            position_summary = (
                f"📊 平仓后仓位情况:\n"
                f"   {position.long_exchange}@{long_symbol}: {long_pos_str}\n"
                f"   {position.short_exchange}@{short_symbol}: {short_pos_str}"
            )
            print(position_summary)
            self.logger.info(position_summary)
            
        except Exception as e:
            self.logger.error(f"平仓失败 {position.position_id}: {e}", exc_info=True)
            position.status = 'failed'
    
    async def close_all_positions(self):
        """关闭所有活跃仓位"""
        if not self.active_positions:
            self.logger.info("没有活跃仓位需要关闭")
            return
        
        positions_to_close = list(self.active_positions)  # 复制列表避免迭代时修改
        total = len(positions_to_close)
        
        print(f"📋 准备关闭 {total} 个仓位...")
        self.logger.info(f"准备关闭 {total} 个活跃仓位")
        
        for i, position in enumerate(positions_to_close, 1):
            try:
                print(f"  [{i}/{total}] 关闭仓位: {position.position_id}")
                await self._execute_hedge_close(position)
                print(f"  ✅ 已关闭: {position.position_id}")
            except Exception as e:
                print(f"  ❌ 关闭失败: {position.position_id} - {e}")
                self.logger.error(f"关闭仓位失败 {position.position_id}: {e}")
        
        remaining = len(self.active_positions)
        if remaining > 0:
            print(f"⚠️  仍有 {remaining} 个仓位未能关闭")
        else:
            print(f"✅ 所有 {total} 个仓位已成功关闭")
    
    def stop(self):
        """停止刷量引擎"""
        self.logger.info("停止刷量引擎...")
        self.is_running = False
    
    def get_statistics(self) -> Dict:
        """获取刷量统计"""
        # 合并活跃仓位和历史仓位
        all_positions = self.active_positions + self.position_history
        closed_positions = self.position_history
        
        total_positions = len(all_positions)
        total_volume = sum(p.size for p in all_positions)
        total_cost = sum(p.calculate_cost() for p in all_positions)
        # 只有已平仓的仓位才有 PnL
        total_pnl = sum(p.pnl for p in closed_positions)
        
        # 计算平均持仓时间（只统计已平仓的）
        avg_lifetime = 0
        if len(closed_positions) > 0:
            avg_lifetime = sum(
                p.get_lifetime_seconds() for p in closed_positions
            ) / len(closed_positions)
        
        return {
            'active_positions': len(self.active_positions),
            'total_positions_opened': total_positions,
            'total_volume': round(total_volume, 4),
            'total_spread_cost': round(total_cost, 4),
            'total_pnl': round(total_pnl, 4),
            'avg_spread_cost': round(total_cost / total_positions, 4) if total_positions > 0 else 0,
            'avg_lifetime_seconds': round(avg_lifetime, 1),
            'daily_volume': round(self.daily_volume, 4),
            'daily_volume_remaining': round(self.daily_max_volume - self.daily_volume, 4)
        }

