"""
对冲刷量策略 - 提供更智能的刷量决策
"""
import random
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class VolumeTarget:
    """刷量目标配置"""
    symbol: str
    daily_target_volume: float    # 每日目标交易量
    current_volume: float = 0.0   # 当前已完成量
    priority: int = 1             # 优先级（1-10，数字越大优先级越高）
    
    def get_completion_rate(self) -> float:
        """获取完成率"""
        if self.daily_target_volume <= 0:
            return 1.0
        return min(self.current_volume / self.daily_target_volume, 1.0)
    
    def is_completed(self) -> bool:
        """是否已完成目标"""
        return self.get_completion_rate() >= 1.0
    
    def get_remaining_volume(self) -> float:
        """获取剩余目标量"""
        return max(0, self.daily_target_volume - self.current_volume)


class HedgeVolumeStrategy:
    """对冲刷量策略 - 更智能的刷量决策"""
    
    def __init__(self, targets: List[VolumeTarget], config: Dict):
        """
        初始化策略
        
        Args:
            targets: 刷量目标列表
            config: 策略配置
        """
        self.targets = {t.symbol: t for t in targets}
        self.config = config
        self.logger = logging.getLogger('strategy.hedge_volume')
        self.last_reset_date = datetime.now().date()
        
        # 策略参数
        self.default_size = config.get('default_size', 0.01)
        self.max_spread_cost = config.get('max_spread_cost', 100)
        self.min_position_lifetime = config.get('min_position_lifetime', 300)
        self.max_position_lifetime = config.get('max_position_lifetime', 3600)
        self.risk_per_trade = config.get('risk_per_trade', 0.02)
        
        self.logger.info(
            f"刷量策略初始化 - 目标数量: {len(targets)}, "
            f"总目标量: {sum(t.daily_target_volume for t in targets)}"
        )
    
    def select_next_symbol(self, available_symbols: Optional[List[str]] = None) -> str:
        """
        根据目标完成度和优先级选择下一个交易对
        
        Args:
            available_symbols: 可用的交易对列表，如果为None则从所有目标中选择
        
        Returns:
            选中的交易对符号
        """
        self._check_daily_reset()
        
        # 筛选可用的目标
        if available_symbols:
            candidates = {
                s: t for s, t in self.targets.items() 
                if s in available_symbols
            }
        else:
            candidates = self.targets
        
        if not candidates:
            # 如果没有目标配置，从可用列表中随机选择
            if available_symbols:
                return random.choice(available_symbols)
            raise ValueError("没有可用的交易对")
        
        # 计算每个symbol的权重（未完成度 * 优先级）
        weights = {}
        for symbol, target in candidates.items():
            if not target.is_completed():
                completion_rate = target.get_completion_rate()
                # 权重 = (1 - 完成率) * 优先级
                # 越未完成、优先级越高，权重越大
                weights[symbol] = (1 - completion_rate) * target.priority
        
        # 如果所有目标都完成了
        if not weights:
            self.logger.info("所有刷量目标已完成，随机选择交易对")
            return random.choice(list(candidates.keys()))
        
        # 加权随机选择
        symbols = list(weights.keys())
        weight_values = list(weights.values())
        selected = random.choices(symbols, weights=weight_values, k=1)[0]
        
        target = self.targets[selected]
        self.logger.debug(
            f"选择交易对: {selected} (完成率: {target.get_completion_rate():.1%}, "
            f"优先级: {target.priority}, 权重: {weights[selected]:.2f})"
        )
        
        return selected
    
    def calculate_optimal_size(
        self,
        symbol: str,
        current_spread: float,
        available_balance: float,
        min_size: float = 0.001,
        max_size: float = 0.1
    ) -> float:
        """
        计算最优仓位大小
        
        Args:
            symbol: 交易对
            current_spread: 当前价差
            available_balance: 可用余额
            min_size: 最小仓位
            max_size: 最大仓位
        
        Returns:
            建议的仓位大小
        """
        target = self.targets.get(symbol)
        
        # 如果没有目标配置，使用默认大小
        if not target:
            return self._add_randomness(self.default_size, min_size, max_size)
        
        # 1. 考虑剩余目标量
        remaining = target.get_remaining_volume()
        if remaining <= 0:
            # 目标已完成，使用较小的默认值
            return self._add_randomness(min_size * 2, min_size, max_size)
        
        # 2. 考虑资金限制（单次最多用 risk_per_trade 的资金）
        max_affordable = available_balance * self.risk_per_trade
        max_by_balance = max_affordable / current_spread if current_spread > 0 else max_size
        
        # 3. 考虑价差成本（单次价差成本不超过设定值）
        max_by_spread = self.max_spread_cost / current_spread if current_spread > 0 else max_size
        
        # 4. 考虑剩余目标的合理分配
        # 假设还需要开N次仓位来完成目标，这里简单假设N=10
        suggested_by_target = remaining / 10
        
        # 取所有限制的最小值
        optimal = min(
            remaining,              # 不超过剩余目标
            max_by_balance,         # 资金限制
            max_by_spread,          # 价差成本限制
            suggested_by_target,    # 合理分配
            max_size                # 绝对最大值
        )
        
        # 确保不小于最小值
        optimal = max(optimal, min_size)
        
        # 添加随机性（±30%）
        return self._add_randomness(optimal, min_size, max_size)
    
    def _add_randomness(
        self, 
        base_size: float, 
        min_size: float, 
        max_size: float
    ) -> float:
        """给仓位大小添加随机性"""
        # 在 base_size 的 70% ~ 100% 之间随机
        randomized = base_size * random.uniform(0.7, 1.0)
        # 限制在范围内
        randomized = max(min_size, min(max_size, randomized))
        return round(randomized, 6)
    
    def should_close_position(
        self, 
        position, 
        current_spread: Optional[float] = None
    ) -> bool:
        """
        判断是否应该平仓
        
        Args:
            position: 仓位对象（HedgePosition）
            current_spread: 当前价差，如果提供则会考虑价差变化
        
        Returns:
            是否应该平仓
        """
        lifetime = position.get_lifetime_seconds()
        
        # 1. 检查持仓时间 - 如果低于最小时间，不平仓
        if lifetime < self.min_position_lifetime:
            return False
        
        # 2. 检查持仓时间 - 如果超过最大时间，必须平仓
        if lifetime >= self.max_position_lifetime:
            self.logger.info(
                f"仓位 {position.position_id} 超过最大持仓时间，必须平仓"
            )
            return True
        
        # 3. 如果提供了当前价差，检查是否价差大幅缩小（可以降低成本）
        if current_spread is not None:
            opening_spread = position.get_spread()
            if current_spread < opening_spread * 0.5:
                self.logger.info(
                    f"仓位 {position.position_id} 价差大幅缩小 "
                    f"({opening_spread:.4f} -> {current_spread:.4f})，建议平仓"
                )
                return True
        
        # 4. 使用随机概率决定（持仓时间越长概率越大）
        # 在 min 和 max 之间，概率从 0 线性增长到 0.5
        time_range = self.max_position_lifetime - self.min_position_lifetime
        time_elapsed = lifetime - self.min_position_lifetime
        close_probability = (time_elapsed / time_range) * 0.5
        
        should_close = random.random() < close_probability
        
        if should_close:
            self.logger.debug(
                f"仓位 {position.position_id} 随机触发平仓 "
                f"(lifetime: {lifetime:.0f}s, prob: {close_probability:.2%})"
            )
        
        return should_close
    
    def update_volume(self, symbol: str, volume: float):
        """
        更新交易量
        
        Args:
            symbol: 交易对
            volume: 本次交易量
        """
        if symbol in self.targets:
            self.targets[symbol].current_volume += volume
            target = self.targets[symbol]
            self.logger.info(
                f"更新 {symbol} 交易量: +{volume:.4f}, "
                f"当前: {target.current_volume:.4f}, "
                f"目标: {target.daily_target_volume:.4f}, "
                f"完成率: {target.get_completion_rate():.1%}"
            )
    
    def _check_daily_reset(self):
        """检查是否需要重置每日统计"""
        today = datetime.now().date()
        if today > self.last_reset_date:
            self.logger.info("每日刷量目标重置")
            for target in self.targets.values():
                target.current_volume = 0.0
            self.last_reset_date = today
    
    def get_progress_report(self) -> Dict:
        """
        获取进度报告
        
        Returns:
            包含各交易对完成情况的字典
        """
        self._check_daily_reset()
        
        report = {
            'targets': [],
            'total_target': 0.0,
            'total_completed': 0.0,
            'overall_completion': 0.0
        }
        
        for symbol, target in self.targets.items():
            report['targets'].append({
                'symbol': symbol,
                'target_volume': target.daily_target_volume,
                'current_volume': target.current_volume,
                'remaining_volume': target.get_remaining_volume(),
                'completion_rate': target.get_completion_rate(),
                'priority': target.priority,
                'is_completed': target.is_completed()
            })
            report['total_target'] += target.daily_target_volume
            report['total_completed'] += target.current_volume
        
        if report['total_target'] > 0:
            report['overall_completion'] = report['total_completed'] / report['total_target']
        
        return report
    
    def get_summary(self) -> str:
        """
        获取进度摘要字符串
        
        Returns:
            格式化的进度摘要
        """
        report = self.get_progress_report()
        
        lines = ["=== 刷量进度报告 ==="]
        lines.append(
            f"总体进度: {report['total_completed']:.2f}/{report['total_target']:.2f} "
            f"({report['overall_completion']:.1%})"
        )
        lines.append("")
        
        for t in sorted(report['targets'], key=lambda x: x['completion_rate']):
            status = "✅" if t['is_completed'] else "🔄"
            lines.append(
                f"{status} {t['symbol']:12s} | "
                f"{t['current_volume']:8.2f}/{t['target_volume']:8.2f} "
                f"({t['completion_rate']:5.1%}) | "
                f"优先级: {t['priority']}"
            )
        
        return "\n".join(lines)

