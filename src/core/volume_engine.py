"""
刷量引擎 - 管理跨交易所对冲刷量
"""

import asyncio
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime

from src.core.base_exchange import BaseExchange
from src.utils.log_utils import print_substage


@dataclass
class HedgePosition:
    """对冲仓位记录"""

    position_id: str
    symbol: str
    long_exchange: str  # 做多的交易所
    short_exchange: str  # 做空的交易所
    size: float  # 仓位大小
    long_price: float  # 开多价格
    short_price: float  # 开空价格
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: datetime | None = None
    status: str = "open"  # 'open', 'closed', 'partial', 'failed'
    pnl: float = 0.0  # 盈亏
    long_order_id: str | None = None
    short_order_id: str | None = None

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

    def __init__(self, exchanges: dict[str, BaseExchange], config: dict, volume_strategy=None):
        self.exchanges = exchanges
        self.config = config
        self.volume_strategy = volume_strategy  # 可选的刷量策略，用于更新进度
        self.active_positions: list[HedgePosition] = []
        self.position_history: list[HedgePosition] = []
        self.logger = logging.getLogger("engine.volume")
        self.is_running = False

        # 交易对符号映射: {标准符号: {exchange_name: 实际符号}}
        self.symbol_mapping: dict[str, dict[str, str]] = {}

        # 从配置中提取参数
        timing_config = config.get("timing", {})
        position_config = config.get("position", {})
        risk_config = config.get("risk", {})

        # 交易所配置
        configured_exchanges = config.get("exchanges", [])
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
        self.min_interval = timing_config.get("min_interval", 30)
        self.max_interval = timing_config.get("max_interval", 600)
        self.min_position_lifetime = timing_config.get("min_position_lifetime", 300)
        self.max_position_lifetime = timing_config.get("max_position_lifetime", 7200)

        # 仓位配置 (USD 价值)
        self.min_order_value = position_config.get("min_size", 50.0)  # 最小下单价值 (USD)
        self.max_order_value = position_config.get("max_size", 100.0)  # 最大下单价值 (USD)
        self.size_distribution = position_config.get("size_distribution", "lognormal")
        self.leverage = position_config.get("leverage", 2)  # 默认2倍杠杆

        # 风险配置
        self.max_spread_tolerance = risk_config.get("max_spread_tolerance", 0.5)
        self.max_spread_cost = risk_config.get("max_spread_cost", 100)
        self.max_concurrent_positions = risk_config.get("max_concurrent_positions", 10)
        self.max_spread_tolerance = risk_config.get("max_spread_tolerance", 0.5)
        self.max_spread_cost = risk_config.get("max_spread_cost", 100)
        self.min_profit_threshold = risk_config.get("min_profit_threshold", 0.0)  # 默认无损
        self.min_fund_balance = risk_config.get("min_fund_balance", 50.0)  # 最小资金要求
        self.max_concurrent_positions = risk_config.get("max_concurrent_positions", 10)
        self.daily_max_volume = risk_config.get("daily_max_volume", 1000)

        # 统计数据
        self.daily_volume = 0.0
        self.last_reset_date = datetime.now().date()

        self.logger.info(
            f"刷量引擎初始化完成 - "
            f"已连接交易所: {len(exchanges)}, "
            f"刷量交易所: {self.volume_exchanges}, "
            f"最大并发仓位: {self.max_concurrent_positions}"
        )

    async def _validate_symbols_for_exchanges(self, symbols: list[str]) -> list[str]:
        """
        验证并构建交易对符号映射
        返回: 所有交易所都支持的标准符号列表

        注意：优先匹配永续合约 (swap) 市场，避免使用现货 (spot) 市场
        """
        self.symbol_mapping = {}

        for symbol in symbols:
            symbol_map = {}

            for ex_name in self.volume_exchanges:
                exchange = self.exchanges[ex_name]

                if hasattr(exchange, "ccxt_exchange") and exchange.ccxt_exchange:
                    ccxt_client = exchange.ccxt_exchange
                    markets = ccxt_client.markets if hasattr(ccxt_client, "markets") else {}

                    # 优先查找永续合约 (swap) 市场
                    # 永续合约符号通常带有结算货币后缀，如 ETH/USDC:USDC
                    swap_variants = self._generate_swap_symbol_variants(symbol)

                    matched = False
                    for variant in swap_variants:
                        if variant in markets:
                            market_info = markets[variant]
                            # 确认是 swap 类型
                            if market_info.get("type") == "swap" or market_info.get("swap", False):
                                self.logger.debug(f"{ex_name}: {symbol} -> {variant} (swap)")
                                symbol_map[ex_name] = variant
                                matched = True
                                break

                    # 如果没找到 swap，尝试常规匹配（但打印警告）
                    if not matched:
                        variants = self._generate_symbol_variants(symbol)
                        for variant in variants:
                            if variant in markets:
                                market_info = markets[variant]
                                market_type = market_info.get("type", "unknown")
                                if market_type == "spot":
                                    self.logger.warning(f"{ex_name}: {variant} 是现货市场，刷量需要永续合约！")
                                    continue  # 跳过现货市场
                                self.logger.debug(f"{ex_name}: {symbol} -> {variant} ({market_type})")
                                symbol_map[ex_name] = variant
                                matched = True
                                break

                        if not matched:
                            self.logger.warning(f"{ex_name} 不支持 {symbol} 的永续合约")
                else:
                    # 非 CCXT 交易所（如 Lighter），直接使用原始符号
                    symbol_map[ex_name] = symbol

            if len(symbol_map) == len(self.volume_exchanges):
                self.symbol_mapping[symbol] = symbol_map
                self.logger.info(f"符号映射: {symbol} -> {symbol_map}")
            else:
                self.logger.warning(f"{symbol} 未被所有交易所支持，跳过")

        return list(self.symbol_mapping.keys())

    def _generate_swap_symbol_variants(self, symbol: str) -> list[str]:
        """
        生成永续合约 (swap) 符号变体

        永续合约在 CCXT 中通常使用 BASE/QUOTE:SETTLE 格式，如:
        - ETH/USDC:USDC (以 USDC 结算的 ETH 永续合约)
        - BTC/USD:USDC (以 USDC 结算的 BTC 永续合约)
        """
        variants = []

        # 提取基础货币和计价货币
        if "/" in symbol:
            base_quote = symbol.split(":")[0] if ":" in symbol else symbol
            base, quote = base_quote.split("/")

            # 生成 swap 格式变体（优先 USDC 结算）
            variants.append(f"{base}/{quote}:USDC")  # ETH/USDC:USDC
            variants.append(f"{base}/USDC:USDC")  # ETH/USDC:USDC
            variants.append(f"{base}/USD:USDC")  # ETH/USD:USDC
            variants.append(f"{base}/{quote}:USDT")  # ETH/USDT:USDT
            variants.append(f"{base}/USDT:USDT")  # ETH/USDT:USDT

            # 如果原符号已经是 swap 格式
            if ":" in symbol:
                variants.insert(0, symbol)

        # 去重并保持顺序
        return list(dict.fromkeys(variants))

    def _generate_symbol_variants(self, symbol: str) -> list[str]:
        """生成交易对符号的常见变体"""

        variants = [symbol]

        # 常见变体转换规则
        # 1. 基础货币替换：USD <-> USDC
        if "/USD:" in symbol:
            # BTC/USD:USDC -> BTC/USDC:USDC
            variants.append(symbol.replace("/USD:", "/USDC:"))
        elif "/USD" in symbol and ":" not in symbol:
            # BTC/USD -> BTC/USDC
            variants.append(symbol.replace("/USD", "/USDC"))
            # BTC/USD -> BTC/USD:USDC
            variants.append(symbol + ":USDC")
            # BTC/USD -> BTC/USDC:USDC
            variants.append(symbol.replace("/USD", "/USDC") + ":USDC")

        if "/USDC:" in symbol:
            # BTC/USDC:USDC -> BTC/USD:USDC
            variants.append(symbol.replace("/USDC:", "/USD:"))
        elif "/USDC" in symbol and ":" not in symbol:
            # BTC/USDC -> BTC/USD
            variants.append(symbol.replace("/USDC", "/USD"))
            # BTC/USDC -> BTC/USD:USDC
            variants.append(symbol.replace("/USDC", "/USD") + ":USDC")
            # BTC/USDC -> BTC/USDC:USDC
            variants.append(symbol + ":USDC")

        # 2. 移除结算货币
        if ":" in symbol:
            # BTC/USD:USDC -> BTC/USD
            # BTC/USDC:USDC -> BTC/USDC
            variants.append(symbol.split(":")[0])

        # 去重并保持顺序
        return list(dict.fromkeys(variants))

    def _get_exchange_symbol(self, standard_symbol: str, exchange_name: str) -> str | None:
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
        short_price: float,
    ) -> float:
        """
        验证并调整交易数量以满足所有市场的最小要求

        Returns:
            调整后的交易数量
        """
        adjusted_size = size

        # 检查多头交易所的要求
        if hasattr(self.exchanges[long_exchange], "ccxt_exchange"):
            try:
                ccxt_client = self.exchanges[long_exchange].ccxt_exchange
                market = ccxt_client.market(long_symbol)
                limits = market.get("limits", {})

                # 检查最小成本要求
                min_cost = limits.get("cost", {}).get("min")
                if min_cost and long_price:
                    required_amount = float(min_cost) / long_price * 1.1  # 加10% buffer
                    if adjusted_size < required_amount:
                        self.logger.info(f"{long_exchange} 最小成本要求: ${min_cost}, 需要数量: {required_amount:.6f}")
                        adjusted_size = max(adjusted_size, required_amount)

                # 检查最小数量要求
                min_amount = limits.get("amount", {}).get("min")
                if min_amount:
                    adjusted_size = max(adjusted_size, float(min_amount) * 1.1)

                # 应用精度
                adjusted_size = float(ccxt_client.amount_to_precision(long_symbol, adjusted_size))

            except Exception as e:
                self.logger.warning(f"获取 {long_exchange} 市场限制失败: {e}")

        # 检查空头交易所的要求
        if hasattr(self.exchanges[short_exchange], "ccxt_exchange"):
            try:
                ccxt_client = self.exchanges[short_exchange].ccxt_exchange
                market = ccxt_client.market(short_symbol)
                limits = market.get("limits", {})

                # 检查最小成本要求
                min_cost = limits.get("cost", {}).get("min")
                if min_cost and short_price:
                    required_amount = float(min_cost) / short_price * 1.1  # 加10% buffer
                    if adjusted_size < required_amount:
                        self.logger.info(f"{short_exchange} 最小成本要求: ${min_cost}, 需要数量: {required_amount:.6f}")
                        adjusted_size = max(adjusted_size, required_amount)

                # 检查最小数量要求
                min_amount = limits.get("amount", {}).get("min")
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

            # 优先检查 BaseExchange 是否实现了 set_leverage (针对非CCXT交易所如 Lighter)
            if hasattr(exchange, "set_leverage"):
                await exchange.set_leverage(symbol, leverage)
                self.logger.info(f"✅ {exchange_name} (Native) 设置杠杆成功: {symbol} -> {leverage}x")
                return True

            # 检查是否是 CCXT 交易所
            elif hasattr(exchange, "ccxt_exchange") and exchange.ccxt_exchange:
                ccxt_client = exchange.ccxt_exchange

                # 检查交易所是否支持设置杠杆
                if hasattr(ccxt_client, "set_leverage"):
                    await ccxt_client.set_leverage(leverage, symbol)
                    self.logger.info(f"✅ {exchange_name} (CCXT) 设置杠杆成功: {symbol} -> {leverage}x")
                    return True
                else:
                    self.logger.debug(f"{exchange_name} 不支持 set_leverage 方法")
                    return False
            else:
                self.logger.debug(f"{exchange_name} 不支持设置杠杆")
                return False

        except Exception as e:
            # 某些交易所可能不支持或已经有默认杠杆，不作为错误处理
            self.logger.debug(f"{exchange_name} 设置杠杆时出现异常 ({symbol}, {leverage}x): {e}")
            return False

    async def _configure_exchanges_for_perp_trading(self):
        """
        配置所有刷量交易所为永续合约交易模式

        - 设置 CCXT 交易所的 defaultType 为 'swap'
        - 确保使用永续合约而非现货
        """
        print_substage("配置交易模式")
        print("  📋 类型: 永续合约 (Perpetual)")
        print(f"  📋 杠杆: {self.leverage}x")
        print()

        for ex_name in self.volume_exchanges:
            exchange = self.exchanges[ex_name]

            # 设置 CCXT 交易所的默认类型为 swap (永续合约)
            if hasattr(exchange, "ccxt_exchange") and exchange.ccxt_exchange:
                ccxt_client = exchange.ccxt_exchange

                # 确保 options 存在并设置 defaultType
                if not hasattr(ccxt_client, "options"):
                    ccxt_client.options = {}
                ccxt_client.options["defaultType"] = "swap"

                print(f"  ✅ {ex_name}: 永续合约模式 (CCXT swap)")
            else:
                # 非 CCXT 交易所 (如 Lighter) - 默认已是永续
                print(f"  ✅ {ex_name}: 原生 SDK (默认永续)")

        print()

    async def start_volume_farming(self, symbols: list[str]):
        """启动刷量任务"""
        # 配置交易所为永续合约模式
        await self._configure_exchanges_for_perp_trading()

        # 验证并构建交易对符号映射
        valid_symbols = await self._validate_symbols_for_exchanges(symbols)

        if not valid_symbols:
            self.logger.error("没有可用的交易对进行刷量")
            return

        # 检查初始资金
        print_substage("资金检查")
        if not await self._check_initial_funds():
            self.logger.error("初始资金检查失败")
            return

        self.is_running = True
        print_substage("启动刷量循环")
        print(f"  💰 交易对: {', '.join(valid_symbols)}")
        print(f"  🔄 对冲交易所: {', '.join(self.volume_exchanges)}")
        print()

        await asyncio.gather(self._farming_loop(valid_symbols), self._position_manager_loop(), return_exceptions=True)

    async def _check_initial_funds(self) -> bool:
        """检查所有刷量交易所的初始资金"""
        print(f"  💰 最低要求: ${self.min_fund_balance}")
        all_passed = True

        for exchange_name in self.volume_exchanges:
            try:
                balance = await self._get_available_funds(exchange_name)
                if balance < self.min_fund_balance:
                    print(f"  ❌ {exchange_name}: ${balance:.2f} (不足)")
                    all_passed = False
                else:
                    print(f"  ✅ {exchange_name}: ${balance:.2f}")
            except Exception as e:
                print(f"  ❌ {exchange_name}: 获取失败 - {e}")
                all_passed = False

        return all_passed

    async def _get_available_funds(self, exchange_name: str) -> float:
        """获取交易所可用资金 (USD)"""
        try:
            exchange = self.exchanges[exchange_name]
            balance_data = await exchange.fetch_balance()

            # self.logger.debug(f"{exchange_name} raw balance: {balance_data}")

            # 不同交易所结构可能不同，这里做简单适配
            # 假设返回结构包含 'free' 字段，且有 'USDC' 或 'USDT' 或 'USD'
            free_balances = balance_data.get("free", {})

            # 优先查找稳定币
            for currency in ["USDC", "USDT", "USD"]:
                if currency in free_balances:
                    val = free_balances[currency]
                    if val is not None:
                        return float(val)

            # 如果没有找到稳定币，尝试查找 total 中的 total (某些交易所直接返回总权益)
            if "total" in balance_data:
                total_val = balance_data["total"]
                # 有些交易所 total 可能是一个字典
                if isinstance(total_val, dict):
                    for currency in ["USDC", "USDT", "USD"]:
                        if currency in total_val and total_val[currency] is not None:
                            return float(total_val[currency])
                elif isinstance(total_val, (int, float, str)) and total_val is not None:
                    return float(total_val)

            # 如果还是没找到，打印警告并返回 0
            self.logger.warning(f"{exchange_name} 未找到可用稳定币余额 (USDC/USDT/USD), raw: {balance_data}")
            return 0.0

        except Exception as e:
            self.logger.error(f"获取 {exchange_name} 资金失败: {e}")
            # 再次尝试打印 raw data 以便调试
            try:
                exchange = self.exchanges[exchange_name]
                # balance_data = await exchange.fetch_balance() # 不要再次调用，可能导致死循环或限流
                pass
            except Exception:
                pass
            raise

    async def _close_smallest_position(self) -> bool:
        """关闭成本最小的仓位以释放资金"""
        if not self.active_positions:
            return False

        # 按成本排序，找最小的
        sorted_positions = sorted(self.active_positions, key=lambda p: p.calculate_cost())
        smallest_position = sorted_positions[0]

        self.logger.warning(
            f"📉 资金不足，尝试关闭最小仓位: {smallest_position.position_id} (Cost: ${smallest_position.calculate_cost():.2f})"
        )
        print(f"📉 资金不足，自动平仓释放资金: {smallest_position.position_id}...")

        await self._execute_hedge_close(smallest_position)
        return True

    async def _farming_loop(self, symbols: list[str]):
        """刷量主循环"""
        self.logger.info(f"刷量循环已启动 - 交易对: {symbols}")
        iteration = 0

        # 状态行变量
        last_status_line = ""

        def print_status(msg: str, end="\r"):
            nonlocal last_status_line
            # 清除上一行
            print(f"\r{' ' * 100}\r", end="")
            print(msg, end=end, flush=True)
            last_status_line = msg

        while self.is_running:
            try:
                # 循环开始时立即检查停止标志
                if not self.is_running:
                    print("\n⚠️  收到停止信号，退出刷量循环")
                    break

                iteration += 1
                # 移除每次循环的分隔符，减少噪音
                # print(f"\n{'='*60}")
                # print(f"🔄 刷量循环 #{iteration}")
                # print(f"{'='*60}")

                # 检查每日限额
                self._check_daily_reset()
                if self.daily_volume >= self.daily_max_volume:
                    msg = f"已达到每日交易量限额 {self.daily_max_volume}, 等待明日..."
                    print_status(f"⚠️  {msg}")
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
                    print_status(f"⚠️  {msg}")
                    self.logger.info(msg)
                    # 等待30秒，但每秒检查一次是否停止
                    for _ in range(30):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    continue

                # 随机选择交易对
                symbol = random.choice(symbols)
                # print(f"📊 选择交易对: {symbol}")

                # 随机选择两个交易所组合
                exchange_pair = self._select_exchange_pair()
                if not exchange_pair:
                    msg = "⚠️  没有足够的交易所进行对冲，等待..."
                    print_status(msg)
                    self.logger.warning(msg)
                    await asyncio.sleep(10)
                    continue

                # 智能检查价差并决定最优开仓方向
                print_status(f"🔍 [{symbol}] 检查价差 ({exchange_pair[0]} <-> {exchange_pair[1]})...")
                spread_check = await self._check_spread_and_determine_direction(symbol, exchange_pair)

                if not spread_check["acceptable"]:
                    reason = spread_check.get("reason", "未知")
                    # 只有在 verbose 模式或调试时才记录详细失败原因到日志
                    self.logger.debug(f"{symbol} 价差检查失败: {reason}")

                    # 随机等待5-15秒
                    wait_time = random.uniform(5, 15)
                    for i in range(int(wait_time * 10)):  # 0.1s interval for smooth UI
                        if not self.is_running:
                            break
                        remaining = wait_time - (i * 0.1)
                        print_status(
                            f"⏳ [{symbol}] 价差不满足 ({spread_check.get('pnl_pct', 0):.4f}%), 等待 {remaining:.1f}s..."
                        )
                        await asyncio.sleep(0.1)
                    continue

                # 发现机会！换行显示
                print()  # 结束状态行

                # 使用智能选择的方向
                long_ex = spread_check["long_exchange"]
                short_ex = spread_check["short_exchange"]

                # --- 资金检查逻辑 ---
                # 估算开仓成本 (使用最大可能仓位做保守估计)
                estimated_cost = self.max_order_value / self.leverage
                required_funds = self.min_fund_balance + estimated_cost

                funds_ok = True
                retry_count = 0
                max_retries = 3

                while retry_count < max_retries:
                    try:
                        long_funds = await self._get_available_funds(long_ex)
                        short_funds = await self._get_available_funds(short_ex)

                        if long_funds < required_funds or short_funds < required_funds:
                            low_exchange = long_ex if long_funds < required_funds else short_ex
                            low_balance = long_funds if long_funds < required_funds else short_funds

                            msg = (
                                f"⚠️  {low_exchange} 资金不足 (${low_balance:.2f} < ${required_funds:.2f}), 等待释放..."
                            )
                            print(msg)
                            self.logger.warning(msg)

                            # 等待一段时间（可中断）
                            wait_seconds = self.min_position_lifetime
                            print(f"⏳ 等待 {wait_seconds}秒...")
                            for _ in range(wait_seconds):
                                if not self.is_running:
                                    print("\n⚠️  收到停止信号，退出资金等待")
                                    break
                                await asyncio.sleep(1)

                            # 如果收到停止信号，跳出整个资金检查循环
                            if not self.is_running:
                                funds_ok = False
                                break

                            retry_count += 1
                            if retry_count >= max_retries:
                                funds_ok = False
                                break
                        else:
                            funds_ok = True
                            break
                    except Exception as e:
                        self.logger.error(f"资金检查异常: {e}")
                        funds_ok = False
                        break

                if not funds_ok:
                    print("❌ 多次检查资金不足，尝试平仓释放资金...")
                    closed = await self._close_smallest_position()
                    if not closed:
                        print("⚠️  无仓位可平，继续等待...")
                        await asyncio.sleep(60)
                    continue
                # ------------------

                # 显示优化后的方向选择
                cost_adv = spread_check.get("cost_advantage", 0)
                print()
                print(f"┌─ 🎯 发现机会 [{symbol}] ────────────────────────────────")
                if cost_adv < 0:
                    print(f"│  方向: {long_ex}(多) ↔ {short_ex}(空)")
                    print(f"│  预期收益: ${abs(cost_adv):.4f} (PnL: {spread_check['pnl_pct']:.4f}%)")
                else:
                    print(f"│  方向: {long_ex}(多) ↔ {short_ex}(空)")
                    print(f"│  成本: ${cost_adv:.4f} (PnL: {spread_check['pnl_pct']:.4f}%)")

                # 生成随机仓位大小 (基于 USD 价值)
                # 使用多头价格作为基准价格
                base_price = spread_check.get("long_price")
                if not base_price:
                    self.logger.warning("无法获取价格用于计算仓位大小，跳过")
                    print("└─ ❌ 无法获取价格")
                    continue

                size = self._generate_random_size(base_price)

                # 执行对冲开仓
                print(f"│  数量: {size:.6f} ETH (${size * base_price:.2f})")
                print("├─ 🚀 执行开仓...")
                position = await self._execute_hedge_open(symbol, long_ex, short_ex, size)

                if position:
                    self.active_positions.append(position)
                    # 记录 USD 交易量 (size * price)
                    usd_volume = size * base_price
                    self.daily_volume += usd_volume

                    # 更新刷量策略的进度跟踪
                    if self.volume_strategy:
                        self.volume_strategy.update_volume(symbol, usd_volume)

                    print(f"│  ✅ 成功 (ID: {position.position_id[-6:]})")
                    print(f"│  成本: ${position.calculate_cost():.4f}")
                    print(f"└─ 📊 今日量: ${self.daily_volume:.2f}/${self.daily_max_volume}")
                    print()
                    self.logger.info(f"开仓成功: {position.position_id}, 成本: {position.calculate_cost()}")

                else:
                    print("└─ ❌ 开仓失败")
                    print()

                # 随机等待下一次开仓
                wait_time = random.uniform(self.min_interval, self.max_interval)
                self.logger.debug(f"等待 {wait_time:.1f} 秒后继续...")

                for i in range(int(wait_time * 10)):
                    if not self.is_running:
                        print("\n⚠️  收到停止信号，退出刷量循环")
                        return
                    remaining = wait_time - (i * 0.1)
                    print_status(f"💤 休息中... 下次开仓: {remaining:.1f}s")
                    await asyncio.sleep(0.1)

            except Exception as e:
                error_msg = f"\n❌ 刷量循环错误: {e}"
                print(error_msg)
                self.logger.error(f"刷量循环错误: {e}", exc_info=True)
                await asyncio.sleep(10)

        print("\n✅ 刷量循环已正常退出")

    async def _position_manager_loop(self):
        """仓位管理循环 - 负责检查和关闭仓位"""
        self.logger.info("仓位管理循环已启动")
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
                self.logger.error(f"仓位管理循环错误: {e}", exc_info=True)

        self.logger.info("仓位管理循环已退出")

    def _check_daily_reset(self):
        """检查是否需要重置每日统计"""
        today = datetime.now().date()
        if today > self.last_reset_date:
            self.logger.info(f"每日统计重置 - 昨日交易量: {self.daily_volume:.2f}")
            self.daily_volume = 0.0
            self.last_reset_date = today

    def _select_exchange_pair(self) -> tuple[str, str] | None:
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

    def _generate_random_size(self, price: float) -> float:
        """
        生成随机仓位大小 (数量)

        Args:
            price: 当前标的价格 (USD)

        Returns:
            float: 交易数量 (例如 BTC 数量)
        """
        if price <= 0:
            return 0.0

        # 1. 生成随机 USD 价值
        if self.size_distribution == "lognormal":
            # 对数正态分布
            log_mean = (math.log(self.min_order_value) + math.log(self.max_order_value)) / 2
            log_std = (math.log(self.max_order_value) - math.log(self.min_order_value)) / 6
            usd_value = random.lognormvariate(log_mean, log_std)
            # 限制在范围内
            usd_value = max(self.min_order_value, min(self.max_order_value, usd_value))
        else:
            # 对数均匀分布（默认）
            log_min = math.log(self.min_order_value)
            log_max = math.log(self.max_order_value)
            random_log = random.uniform(log_min, log_max)
            usd_value = math.exp(random_log)

        # 添加一些噪音，让价值看起来更"自然"
        noise = random.uniform(0.95, 1.05)
        usd_value = usd_value * noise

        # 2. 转换为数量
        size = usd_value / price

        # 3. 四舍五入到合理的精度 (保留6位小数)
        return round(size, 6)

    async def _check_spread_and_determine_direction(self, symbol: str, exchange_pair: tuple[str, str]) -> dict:
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
                return {"acceptable": False, "spread_pct": 999.0, "reason": f"符号映射失败: {symbol}"}

            # 并发获取订单簿
            ob1, ob2 = await asyncio.gather(
                self.exchanges[ex1].fetch_orderbook(symbol1), self.exchanges[ex2].fetch_orderbook(symbol2)
            )

            # 检查订单簿有效性
            if (
                not ob1.get("asks")
                or not ob1.get("bids")
                or not ob2.get("asks")
                or not ob2.get("bids")
                or len(ob1["asks"]) == 0
                or len(ob1["bids"]) == 0
                or len(ob2["asks"]) == 0
                or len(ob2["bids"]) == 0
            ):
                return {"acceptable": False, "spread_pct": 999.0, "reason": "订单簿为空或无效"}

            # 获取价格
            ex1_buy_price = ob1["asks"][0][0]  # 在ex1买入的价格
            ex1_sell_price = ob1["bids"][0][0]  # 在ex1卖出的价格
            ex2_buy_price = ob2["asks"][0][0]  # 在ex2买入的价格
            ex2_sell_price = ob2["bids"][0][0]  # 在ex2卖出的价格

            # 获取费率 (Taker)
            ex1_fee = self.exchanges[ex1].get_fee_rate(symbol1, "market")
            ex2_fee = self.exchanges[ex2].get_fee_rate(symbol2, "market")

            # 计算两种方案的净盈亏 (Net PnL)
            # 方案1: ex1做多(买入), ex2做空(卖出)
            # 成本 = 买入价 * (1 + 费率)
            # 收入 = 卖出价 * (1 - 费率)
            # PnL = 收入 - 成本
            cost1_buy = ex1_buy_price * (1 + ex1_fee)
            revenue1_sell = ex2_sell_price * (1 - ex2_fee)
            pnl1 = revenue1_sell - cost1_buy
            pnl1_pct = (pnl1 / cost1_buy) * 100

            # 方案2: ex2做多(买入), ex1做空(卖出)
            cost2_buy = ex2_buy_price * (1 + ex2_fee)
            revenue2_sell = ex1_sell_price * (1 - ex1_fee)
            pnl2 = revenue2_sell - cost2_buy
            pnl2_pct = (pnl2 / cost2_buy) * 100

            # 选择 PnL 更高的方案
            if pnl1 >= pnl2:
                # 方案1更优
                long_exchange = ex1
                short_exchange = ex2
                long_price = ex1_buy_price
                short_price = ex2_sell_price
                pnl = pnl1
                pnl_pct = pnl1_pct
                spread_pct = (abs(ex1_buy_price - ex2_sell_price) / ((ex1_buy_price + ex2_sell_price) / 2)) * 100
                direction_note = f"{ex1}做多, {ex2}做空"
            else:
                # 方案2更优
                long_exchange = ex2
                short_exchange = ex1
                long_price = ex2_buy_price
                short_price = ex1_sell_price
                pnl = pnl2
                pnl_pct = pnl2_pct
                spread_pct = (abs(ex2_buy_price - ex1_sell_price) / ((ex2_buy_price + ex1_sell_price) / 2)) * 100
                direction_note = f"{ex2}做多, {ex1}做空"

            # 检查是否满足利润阈值
            # min_profit_threshold: 0.0=无损, >0=套利, <0=允许磨损
            acceptable = pnl_pct >= self.min_profit_threshold

            if not acceptable:
                reason = f"净盈亏 {pnl_pct:.4f}% 低于阈值 {self.min_profit_threshold}% (价差: {spread_pct:.3f}%)"
            else:
                reason = f"{direction_note}, 净盈亏 {pnl_pct:.4f}% 满足阈值 (价差: {spread_pct:.3f}%)"

            return {
                "acceptable": acceptable,
                "long_exchange": long_exchange,
                "short_exchange": short_exchange,
                "spread_pct": spread_pct,
                "pnl_pct": pnl_pct,
                "long_price": long_price,
                "short_price": short_price,
                "reason": reason,
                "cost_advantage": -pnl,  # 负数表示亏损，正数表示盈利，为了兼容旧逻辑取反？不，旧逻辑 cost_advantage < 0 是利润
                # 旧逻辑: cost_advantage = min(cost1, cost2)
                # cost = buy - sell. cost < 0 means sell > buy (profit)
                # 这里 pnl = sell - buy. pnl > 0 means sell > buy (profit)
                # 所以 cost_advantage 应该是 -pnl
            }

        except Exception as e:
            import traceback

            error_detail = f"{type(e).__name__}: {str(e)}"
            error_trace = traceback.format_exc()

            # 同时输出到控制台和日志
            # print(f"❌ 价差检查异常 ({ex1}-{ex2}): {error_detail}") # 移除控制台输出，避免刷屏
            self.logger.error(f"检查价差失败: {error_detail}\n{error_trace}")

            return {"acceptable": False, "spread_pct": 999.0, "pnl_pct": -999.0, "reason": error_detail}

    async def _check_spread_acceptable(self, symbol: str, long_exchange: str, short_exchange: str) -> dict:
        """检查价差是否可接受（已弃用，保留用于兼容性）"""
        try:
            # 获取每个交易所的实际符号
            long_symbol = self._get_exchange_symbol(symbol, long_exchange)
            short_symbol = self._get_exchange_symbol(symbol, short_exchange)

            if not long_symbol or not short_symbol:
                return {"acceptable": False, "spread_pct": 999.0, "reason": f"符号映射失败: {symbol}"}

            # 并发获取两个交易所的订单簿
            long_orderbook_task = self.exchanges[long_exchange].fetch_orderbook(long_symbol)
            short_orderbook_task = self.exchanges[short_exchange].fetch_orderbook(short_symbol)

            long_orderbook, short_orderbook = await asyncio.gather(long_orderbook_task, short_orderbook_task)

            # 检查订单簿是否有效
            if (
                not long_orderbook.get("asks")
                or not short_orderbook.get("bids")
                or len(long_orderbook["asks"]) == 0
                or len(short_orderbook["bids"]) == 0
            ):
                reason = f"订单簿为空或无效: long_asks={len(long_orderbook.get('asks', []))}, short_bids={len(short_orderbook.get('bids', []))}"
                return {"acceptable": False, "spread_pct": 999.0, "reason": reason}

            # 获取价格
            long_price = long_orderbook["asks"][0][0]  # 做多需要买入
            short_price = short_orderbook["bids"][0][0]  # 做空需要卖出

            # 计算价差百分比
            mid_price = (long_price + short_price) / 2
            spread = abs(long_price - short_price)
            spread_pct = (spread / mid_price) * 100

            acceptable = spread_pct <= self.max_spread_tolerance

            # 生成原因说明
            if not acceptable:
                reason = f"价差 {spread_pct:.3f}% 超过最大容忍度 {self.max_spread_tolerance:.3f}%"
            else:
                reason = f"价差 {spread_pct:.3f}% 在可接受范围内"

            return {
                "acceptable": acceptable,
                "spread_pct": spread_pct,
                "long_price": long_price,
                "short_price": short_price,
                "spread": spread,
                "reason": reason,
            }

        except Exception as e:
            import traceback

            error_detail = f"{type(e).__name__}: {str(e)}"
            error_trace = traceback.format_exc()

            # 同时输出到控制台和日志
            print(f"❌ 价差检查异常 ({long_exchange}-{short_exchange}): {error_detail}")
            self.logger.error(f"检查价差失败: {error_detail}\n{error_trace}")

            return {"acceptable": False, "spread_pct": 999.0, "reason": error_detail}

    async def _fetch_position_info(self, exchange_name: str, symbol: str) -> dict | None:
        """
        查询交易所的仓位信息
        返回格式化的仓位信息，如果查询失败则返回 None
        """
        try:
            exchange = self.exchanges[exchange_name]

            # 检查是否是 CCXT 交易所
            if hasattr(exchange, "ccxt_exchange") and exchange.ccxt_exchange:
                ccxt_client = exchange.ccxt_exchange

                # 尝试获取仓位信息
                try:
                    positions = await ccxt_client.fetch_positions([symbol])

                    if positions:
                        # 找到对应交易对的仓位
                        for pos in positions:
                            if pos.get("symbol") == symbol:
                                side = pos.get("side", "unknown")
                                size = pos.get("contracts", 0) or pos.get("contractSize", 0)
                                notional = pos.get("notional", 0)
                                entry_price = pos.get("entryPrice", 0)
                                unrealized_pnl = pos.get("unrealizedPnl", 0)

                                return {
                                    "exchange": exchange_name,
                                    "symbol": symbol,
                                    "side": side,
                                    "size": size,
                                    "notional": notional,
                                    "entry_price": entry_price,
                                    "unrealized_pnl": unrealized_pnl,
                                }

                        # 如果没有找到仓位，返回空仓
                        return {
                            "exchange": exchange_name,
                            "symbol": symbol,
                            "side": "none",
                            "size": 0,
                            "notional": 0,
                            "entry_price": 0,
                            "unrealized_pnl": 0,
                        }
                    else:
                        # 没有仓位
                        return {
                            "exchange": exchange_name,
                            "symbol": symbol,
                            "side": "none",
                            "size": 0,
                            "notional": 0,
                            "entry_price": 0,
                            "unrealized_pnl": 0,
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

    def _format_position_info(self, pos_info: dict | None) -> str:
        """格式化仓位信息为可读字符串"""
        if not pos_info:
            return "查询失败"

        if pos_info["side"] == "none":
            return "无仓位"

        # 确保所有数值字段都是数字类型
        try:
            size = float(pos_info["size"]) if pos_info["size"] else 0
            entry_price = float(pos_info["entry_price"]) if pos_info["entry_price"] else 0
            notional = float(pos_info["notional"]) if pos_info["notional"] else 0
            unrealized_pnl = float(pos_info["unrealized_pnl"]) if pos_info["unrealized_pnl"] else 0

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
        self, symbol: str, long_exchange: str, short_exchange: str, size: float
    ) -> HedgePosition | None:
        """执行对冲开仓"""
        position_id = f"{symbol.replace('/', '').replace(':', '')}_{long_exchange}_{short_exchange}_{int(datetime.now().timestamp())}"

        try:
            # 获取每个交易所的实际符号
            long_symbol = self._get_exchange_symbol(symbol, long_exchange)
            short_symbol = self._get_exchange_symbol(symbol, short_exchange)

            if not long_symbol or not short_symbol:
                self.logger.error(f"符号映射失败: {symbol}")
                return None

            self.logger.debug(
                f"准备开仓: {position_id} | "
                f"Long@{long_exchange}({long_symbol}) | Short@{short_exchange}({short_symbol}) | Size: {size}"
            )

            # 简洁的开仓信息
            print(f"│  Long:  {long_exchange:>12} @ {long_symbol}")
            print(f"│  Short: {short_exchange:>12} @ {short_symbol}")

            # 获取当前价格（用于某些交易所的市价单和验证最小成本）
            try:
                long_orderbook = await self.exchanges[long_exchange].fetch_orderbook(long_symbol, limit=1)
                short_orderbook = await self.exchanges[short_exchange].fetch_orderbook(short_symbol, limit=1)
                long_price = long_orderbook["asks"][0][0] if long_orderbook.get("asks") else None
                short_price = short_orderbook["bids"][0][0] if short_orderbook.get("bids") else None
            except Exception as e:
                self.logger.error(f"获取价格失败: {e}")
                return None

            # 验证并调整交易数量以满足市场限制
            original_size = size
            size = await self._validate_and_adjust_size(
                size, long_exchange, long_symbol, long_price, short_exchange, short_symbol, short_price
            )

            if size != original_size:
                self.logger.debug(f"数量已调整: {original_size:.6f} -> {size:.6f}")

            # 设置杠杆倍数
            # print(f"⚙️  设置杠杆倍数: {self.leverage}x")
            await asyncio.gather(
                self._set_leverage(long_exchange, long_symbol, self.leverage),
                self._set_leverage(short_exchange, short_symbol, self.leverage),
                return_exceptions=True,
            )

            # 并发执行两边开仓
            # 注意：Hyperliquid 市价单必须传 price
            if long_exchange == "hyperliquid":
                long_task = self.exchanges[long_exchange].create_order(
                    long_symbol, "market", "buy", size, price=long_price
                )
            else:
                long_task = self.exchanges[long_exchange].create_order(long_symbol, "market", "buy", size)

            if short_exchange == "hyperliquid":
                short_task = self.exchanges[short_exchange].create_order(
                    short_symbol, "market", "sell", size, price=short_price
                )
            else:
                short_task = self.exchanges[short_exchange].create_order(short_symbol, "market", "sell", size)

            results = await asyncio.gather(long_task, short_task, return_exceptions=True)
            long_order, short_order = results

            # 检查订单是否都成功
            if isinstance(long_order, Exception):
                self.logger.error(f"开多失败 {long_exchange}: {long_order}")
                # 如果空头已经成功，需要立即平掉
                if not isinstance(short_order, Exception):
                    self.logger.warning("空头成功但多头失败，立即平掉空头")
                    await self._emergency_close_order(short_exchange, short_symbol, "buy", size)
                return None

            if isinstance(short_order, Exception):
                self.logger.error(f"开空失败 {short_exchange}: {short_order}")
                # 如果多头已经成功，需要立即平掉
                if not isinstance(long_order, Exception):
                    self.logger.warning("多头成功但空头失败，立即平掉多头")
                    await self._emergency_close_order(long_exchange, long_symbol, "sell", size)
                return None

            # 提取成交价格 - 使用订单簿价格作为后备
            long_price = long_order.get("average") or long_order.get("price") or long_price
            short_price = short_order.get("average") or short_order.get("price") or short_price

            # 确保价格不为 None
            if long_price is None or short_price is None:
                self.logger.error(f"无法获取成交价格: long_price={long_price}, short_price={short_price}")
                # 尝试回滚 - 平掉已开的仓位
                if not isinstance(long_order, Exception):
                    await self._emergency_close_order(long_exchange, long_symbol, "sell", size)
                if not isinstance(short_order, Exception):
                    await self._emergency_close_order(short_exchange, short_symbol, "buy", size)
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
                status="open",
                long_order_id=long_order.get("id"),
                short_order_id=short_order.get("id"),
            )

            self.logger.debug(
                f"✅ 对冲开仓成功: {position_id} | "
                f"Long@{long_exchange}: {position.long_price:.4f} | "
                f"Short@{short_exchange}: {position.short_price:.4f} | "
                f"Size: {size} | Spread: ${position.calculate_cost():.4f}"
            )

            # 获取订单实际成交量（如果没有 filled 字段或为 None，使用预期的 size）
            long_filled = long_order.get("filled") if long_order.get("filled") is not None else size
            short_filled = short_order.get("filled") if short_order.get("filled") is not None else size

            # 检查本次成交量是否匹配
            if abs(long_filled - short_filled) > 0.001:  # 容忍 0.001 的差异
                warning_msg = f"⚠️  警告：本次开仓数量不匹配！Long={long_filled:.6f}, Short={short_filled:.6f}"
                print(warning_msg)
                self.logger.warning(warning_msg)

            # 查询并输出实际仓位信息
            self.logger.debug("查询开仓后的实际仓位...")

            long_pos_info = await self._fetch_position_info(long_exchange, long_symbol)
            short_pos_info = await self._fetch_position_info(short_exchange, short_symbol)

            long_pos_str = self._format_position_info(long_pos_info)
            short_pos_str = self._format_position_info(short_pos_info)

            # 仅在数量不匹配时打印详细仓位信息
            if abs(long_filled - short_filled) > 0.001:
                position_summary = (
                    f"📊 开仓后仓位情况:\n"
                    f"   本次开仓数量: Long={long_filled:.6f}, Short={short_filled:.6f}\n"
                    f"   {long_exchange}@{long_symbol}: {long_pos_str}\n"
                    f"   {short_exchange}@{short_symbol}: {short_pos_str}"
                )
                print(position_summary)

            self.logger.debug(
                f"仓位查询: Long={long_filled:.6f}, Short={short_filled:.6f} | "
                f"{long_exchange}: {long_pos_str} | {short_exchange}: {short_pos_str}"
            )

            return position

        except Exception as e:
            print(f"   ❌ 执行对冲开仓失败: {str(e)}")
            self.logger.error(f"执行对冲开仓失败: {e}", exc_info=True)
            return None

    async def _emergency_close_order(self, exchange: str, symbol: str, side: str, size: float):
        """紧急平仓（当对冲的一边失败时）"""
        try:
            self.logger.warning(f"执行紧急平仓: {exchange} {symbol} {side} {size}")

            # Hyperliquid 市价单必须传 price
            if exchange == "hyperliquid":
                # 获取当前价格
                orderbook = await self.exchanges[exchange].fetch_orderbook(symbol, limit=1)
                if side == "buy":
                    price = orderbook["asks"][0][0] if orderbook.get("asks") else None
                else:  # sell
                    price = orderbook["bids"][0][0] if orderbook.get("bids") else None

                await self.exchanges[exchange].create_order(symbol, "market", side, size, price=price)
            else:
                # 其他交易所市价单不传 price
                await self.exchanges[exchange].create_order(symbol, "market", side, size)
        except Exception as e:
            self.logger.error(f"紧急平仓失败: {e}", exc_info=True)

    async def _check_and_close_positions(self):
        """检查并关闭过期仓位"""
        if not self.active_positions:
            return

        positions_to_close = []

        for position in self.active_positions:
            lifetime = position.get_lifetime_seconds()

            # 检查是否达到最大持仓时间
            if lifetime >= self.max_position_lifetime:
                positions_to_close.append(position)
                self.logger.info(f"仓位 {position.position_id} 达到最大持仓时间 {lifetime:.0f}s, 准备平仓")
            # 检查是否超过最小持仓时间，并使用概率决定是否平仓
            elif lifetime >= self.min_position_lifetime:
                # 随机概率平仓（持仓时间越长概率越大）
                time_factor = (lifetime - self.min_position_lifetime) / (
                    self.max_position_lifetime - self.min_position_lifetime
                )
                close_probability = time_factor * 0.3  # 最高30%概率

                if random.random() < close_probability:
                    positions_to_close.append(position)
                    self.logger.info(f"仓位 {position.position_id} 随机触发平仓 (lifetime: {lifetime:.0f}s)")

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
                position.status = "failed"
                return

            # 获取当前价格
            try:
                long_orderbook = await self.exchanges[position.long_exchange].fetch_orderbook(long_symbol, limit=1)
                short_orderbook = await self.exchanges[position.short_exchange].fetch_orderbook(short_symbol, limit=1)
                long_close_price = long_orderbook["bids"][0][0] if long_orderbook.get("bids") else None
                short_close_price = short_orderbook["asks"][0][0] if short_orderbook.get("asks") else None
            except Exception as e:
                self.logger.error(f"获取平仓价格失败: {e}")
                position.status = "failed"
                return

            # 反向操作：平多头和平空头
            # 注意：Hyperliquid 市价单必须传 price
            if position.long_exchange == "hyperliquid":
                close_long_task = self.exchanges[position.long_exchange].create_order(
                    long_symbol, "market", "sell", position.size, price=long_close_price
                )
            else:
                close_long_task = self.exchanges[position.long_exchange].create_order(
                    long_symbol, "market", "sell", position.size
                )

            if position.short_exchange == "hyperliquid":
                close_short_task = self.exchanges[position.short_exchange].create_order(
                    short_symbol, "market", "buy", position.size, price=short_close_price
                )
            else:
                close_short_task = self.exchanges[position.short_exchange].create_order(
                    short_symbol, "market", "buy", position.size
                )

            results = await asyncio.gather(close_long_task, close_short_task, return_exceptions=True)

            close_long_order, close_short_order = results

            # 计算盈亏（不考虑手续费的理论盈亏）
            if not isinstance(close_long_order, Exception) and not isinstance(close_short_order, Exception):
                # 从订单中获取成交价格，如果没有则使用订单簿价格
                order_long_price = close_long_order.get("average") or close_long_order.get("price")
                order_short_price = close_short_order.get("average") or close_short_order.get("price")

                final_long_close_price = order_long_price if order_long_price is not None else long_close_price
                final_short_close_price = order_short_price if order_short_price is not None else short_close_price

                # 如果价格仍为 None，使用 0 避免错误
                if final_long_close_price is None or final_short_close_price is None:
                    self.logger.warning(
                        f"无法获取平仓价格: long={final_long_close_price}, short={final_short_close_price}，跳过 PnL 计算"
                    )
                    position.pnl = 0.0
                else:
                    # 多头盈亏 = (平仓价 - 开仓价) * 仓位
                    # 空头盈亏 = (开仓价 - 平仓价) * 仓位
                    long_pnl = (float(final_long_close_price) - position.long_price) * position.size
                    short_pnl = (position.short_price - float(final_short_close_price)) * position.size
                    position.pnl = long_pnl + short_pnl
            else:
                # 有订单失败，PnL 设为 0
                self.logger.warning("平仓订单有异常，跳过 PnL 计算")
                position.pnl = 0.0

            # 更新仓位状态并移动到历史记录
            position.status = "closed"
            position.closed_at = datetime.now()
            self.active_positions.remove(position)
            self.position_history.append(position)

            self.logger.info(
                f"✅ 平仓完成: {position.position_id} | "
                f"持仓时长: {position.get_lifetime_seconds():.0f}s | "
                f"PnL: ${position.pnl:.4f}"
            )

            # 查询并输出平仓后的实际仓位信息
            self.logger.debug("查询平仓后的实际仓位...")

            long_pos_info = await self._fetch_position_info(position.long_exchange, long_symbol)
            short_pos_info = await self._fetch_position_info(position.short_exchange, short_symbol)

            long_pos_str = self._format_position_info(long_pos_info)
            short_pos_str = self._format_position_info(short_pos_info)

            self.logger.debug(
                f"平仓后仓位: {position.long_exchange}@{long_symbol}: {long_pos_str} | "
                f"{position.short_exchange}@{short_symbol}: {short_pos_str}"
            )

        except Exception as e:
            self.logger.error(f"平仓失败 {position.position_id}: {e}", exc_info=True)
            position.status = "failed"

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

    def get_statistics(self) -> dict:
        """获取刷量统计"""
        # 合并活跃仓位和历史仓位
        all_positions = self.active_positions + self.position_history
        closed_positions = self.position_history

        total_positions = len(all_positions)
        # 累计交易量 = 所有仓位的 USD 名义价值之和
        # 使用开仓价格计算每个仓位的名义价值
        total_volume = sum((p.long_price + p.short_price) / 2 * p.size for p in all_positions)
        total_cost = sum(p.calculate_cost() for p in all_positions)
        # 只有已平仓的仓位才有 PnL
        total_pnl = sum(p.pnl for p in closed_positions)

        # 计算平均持仓时间（只统计已平仓的）
        avg_lifetime = 0
        if len(closed_positions) > 0:
            avg_lifetime = sum(p.get_lifetime_seconds() for p in closed_positions) / len(closed_positions)

        return {
            "active_positions": len(self.active_positions),
            "total_positions_opened": total_positions,
            "total_volume_usd": round(total_volume, 2),  # USD 价值
            "total_spread_cost": round(total_cost, 4),
            "total_pnl": round(total_pnl, 4),
            "avg_spread_cost": round(total_cost / total_positions, 4) if total_positions > 0 else 0,
            "avg_lifetime_seconds": round(avg_lifetime, 1),
            "daily_volume_usd": round(self.daily_volume, 2),  # USD 价值
            "daily_volume_remaining": round(self.daily_max_volume - self.daily_volume, 2),
        }
