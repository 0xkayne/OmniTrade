import asyncio
import yaml
import signal
import sys
from src.core.exchange_factory import ExchangeFactory
from src.core.arbitrage_engine import ArbitrageEngine
from src.utils.network_manager import NetworkManager, NetworkType

class ArbitrageBot:
    def __init__(self, config_path: str = "config/exchanges.yaml", 
                 secrets_path: str = "config/secrets.yaml"):
        self.config_path = config_path
        self.secrets_path = secrets_path
        self.exchanges = {}
        self.engine = None
        self.network_manager = None
        self.is_running = False
        
    async def initialize(self, target_network: NetworkType = None):
        """初始化机器人"""
        print("🚀 初始化套利机器人...")
        
        # 加载配置
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
            
        with open(self.secrets_path, 'r') as f:
            secrets = yaml.safe_load(f)
        
        # 初始化交易所
        self.exchanges = await ExchangeFactory.initialize_exchanges(
            config['exchanges'], secrets
        )
        
        if not self.exchanges:
            raise Exception("没有可用的交易所连接")
            
        # 初始化网络管理器
        self.network_manager = NetworkManager(self.exchanges)
        
        # 如果指定了目标网络，切换所有交易所
        if target_network:
            print(f"切换所有交易所到 {target_network.value} 网络...")
            results = await self.network_manager.switch_all_networks(target_network)
            for name, success in results.items():
                print(f"  {name}: {'成功' if success else '失败'}")
        
        # 检查网络一致性
        if not self.network_manager.check_network_consistency():
            print("⚠️  警告: 交易所网络不一致!")
        
        # 输出网络状态
        print("\n📊 当前网络状态:")
        status = self.network_manager.get_network_status()
        for name, info in status.items():
            print(f"  {name}: {info['network']} ({'测试网' if info['is_testnet'] else '主网'})")
        
        # 初始化套利引擎
        self.engine = ArbitrageEngine(self.exchanges, min_spread=0.5)
        
        print(f"✅ 机器人初始化完成，已连接 {len(self.exchanges)} 个交易所")
        
    async def run(self):
        """运行主循环"""
        self.is_running = True
        print("开始监控套利机会...")
        
        # 获取所有启用的交易对
        symbols = set()
        for exchange in self.exchanges.values():
            symbols.update(exchange.config.get('symbols', []))
        
        while self.is_running:
            try:
                opportunities = await self.engine.monitor_spreads(list(symbols))
                
                for opp in opportunities:
                    print(f"📈 套利机会: {opp.symbol} | "
                          f"{opp.exchange_a}({opp.exchange_a_price:.2f}) -> "
                          f"{opp.exchange_b}({opp.exchange_b_price:.2f}) | "
                          f"价差: {opp.spread_percentage:.2f}%")
                
                # 控制监控频率
                await asyncio.sleep(0.1)  # 100ms
                
            except Exception as e:
                print(f"监控循环错误: {e}")
                await asyncio.sleep(1)
                
    def stop(self):
        """停止机器人"""
        self.is_running = False
        print("🛑 停止套利机器人")
        
        # 清理资源
        for exchange in self.exchanges.values():
            asyncio.create_task(exchange.close())

async def main():
    # 可以通过命令行参数指定网络
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--network', choices=['mainnet', 'testnet'], 
                       default='testnet', help='目标网络')
    args = parser.parse_args()
    
    target_network = NetworkType(args.network)
    
    bot = ArbitrageBot()
    
    # 设置信号处理
    def signal_handler(sig, frame):
        print("\n收到停止信号...")
        bot.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await bot.initialize(target_network=target_network)
        await bot.run()
    except KeyboardInterrupt:
        bot.stop()
    except Exception as e:
        print(f"机器人运行错误: {e}")
        bot.stop()

if __name__ == "__main__":
    asyncio.run(main())