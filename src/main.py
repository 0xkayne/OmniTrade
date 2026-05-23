import asyncio
import fcntl
import logging
import os
import signal

import yaml

from src.core.arbitrage_engine import ArbitrageEngine
from src.core.exchange_factory import ExchangeFactory
from src.core.volume_engine import VolumeEngine
from src.strategies.hedge_volume import HedgeVolumeStrategy, VolumeTarget
from src.utils.log_utils import LogStage, print_section_end, print_stage, print_substage
from src.utils.network_manager import NetworkManager, NetworkType

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)


class TradeBot:
    def __init__(
        self,
        config_path: str = "config/exchanges.yaml",
        secrets_path: str = "config/secrets.yaml",
        volume_config_path: str = "config/volume_farming.yaml",
    ):
        self.config_path = config_path
        self.secrets_path = secrets_path
        self.volume_config_path = volume_config_path
        self.exchanges = {}
        self.engine = None
        self.arbitrage_engine = None  # 套利引擎
        self.volume_engine = None
        self.volume_strategy = None
        self.network_manager = None
        self.is_running = False
        self.run_mode = "arbitrage"  # 'arbitrage', 'volume', 'both'
        self.lock_file = None  # 进程锁文件

    def _acquire_lock(self, mode: str) -> bool:
        """
        获取进程锁，防止多个实例同时运行

        Args:
            mode: 运行模式，用于生成锁文件名

        Returns:
            bool: 是否成功获取锁
        """
        lock_file_path = f"/tmp/arbitrage_bot_{mode}.lock"
        current_pid = os.getpid()

        try:
            # 先尝试读取现有锁文件，检查进程是否还在运行
            if os.path.exists(lock_file_path):
                try:
                    with open(lock_file_path) as f:
                        existing_pid = f.read().strip()

                    if existing_pid:
                        # 检查该进程是否还存在
                        try:
                            os.kill(int(existing_pid), 0)  # 不发送信号，只检查进程存在
                            # 进程存在，锁有效
                            print(f"❌ 检测到 {mode} 模式已有进程在运行 (PID: {existing_pid})")
                            print("   请先停止现有进程，或使用以下命令强制停止：")
                            print(f"   kill -INT {existing_pid}  # 优雅停止")
                            print(f"   kill -9 {existing_pid}    # 强制停止")
                            return False
                        except OSError:
                            # 进程不存在，清理过期的锁文件
                            print(f"🧹 清理过期的锁文件 (PID {existing_pid} 已不存在)")
                            os.remove(lock_file_path)
                except Exception:
                    # 读取失败，尝试删除
                    pass

            # 创建/打开锁文件并获取排他锁
            self.lock_file = open(lock_file_path, "w")
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # 写入当前进程 PID
            self.lock_file.write(str(current_pid))
            self.lock_file.flush()
            os.fsync(self.lock_file.fileno())  # 强制同步到磁盘

            print(f"🔒 进程锁已获取: {lock_file_path} (PID: {current_pid})")
            return True

        except OSError as e:
            # 锁文件被其他进程占用
            print(f"❌ 检测到 {mode} 模式已有进程在运行")
            print(f"   锁文件: {lock_file_path}")
            print(f"   错误: {e}")

            if self.lock_file:
                try:
                    self.lock_file.close()
                except Exception:
                    pass
                self.lock_file = None
            return False
        except Exception as e:
            print(f"❌ 获取进程锁时出错: {e}")
            if self.lock_file:
                try:
                    self.lock_file.close()
                except Exception:
                    pass
                self.lock_file = None
            return False

    def _release_lock(self):
        """释放进程锁"""
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                print("🔓 进程锁已释放")
            except Exception as e:
                print(f"⚠️  释放进程锁时出错: {e}")
            finally:
                self.lock_file = None

    async def initialize(self, target_network: NetworkType = None, mode: str = "volume"):
        """初始化机器人

        Args:
            target_network: 目标网络（mainnet/testnet）
            mode: 运行模式 ('arbitrage', 'volume', 'both')
        """
        # 初始化阶段分隔符
        print_stage(LogStage.INITIALIZATION, f"模式: {mode}")
        self.run_mode = mode

        # 获取进程锁
        if not self._acquire_lock(mode):
            return False  # 获取锁失败，停止初始化

        # 加载配置
        with open(self.config_path) as f:
            config = yaml.safe_load(f)

        with open(self.secrets_path) as f:
            secrets = yaml.safe_load(f)

        # 确定需要初始化的交易所
        exchanges_config = config["exchanges"]

        # 在 volume 模式下，只初始化 volume_farming.yaml 中指定的交易所
        if mode in ["volume"]:
            try:
                with open(self.volume_config_path) as f:
                    volume_config = yaml.safe_load(f)

                volume_exchanges = volume_config.get("volume_farming", {}).get("exchanges", [])
                if volume_exchanges:
                    # 只保留刷量配置中指定的交易所
                    filtered_config = {
                        name: cfg
                        for name, cfg in exchanges_config.items()
                        if name in volume_exchanges and cfg.get("enabled", False)
                    }
                    if filtered_config:
                        print(f"  📋 Volume 模式: 只初始化刷量交易所 {list(filtered_config.keys())}")
                        exchanges_config = filtered_config
            except FileNotFoundError:
                pass  # 使用默认配置

        print_substage("连接交易所")

        # 初始化交易所 - 直接使用目标网络
        self.exchanges = await ExchangeFactory.initialize_exchanges(
            exchanges_config, secrets, target_network=target_network
        )

        if not self.exchanges:
            raise Exception("没有可用的交易所连接")

        # ⭐ 刷量模式下检查：必须有至少2个交易所才能进行对冲
        if mode == "volume":
            required_exchanges = (
                volume_config.get("volume_farming", {}).get("exchanges", []) if "volume_config" in dir() else []
            )
            initialized_exchanges = list(self.exchanges.keys())

            if len(initialized_exchanges) < 2:
                missing = set(required_exchanges) - set(initialized_exchanges)
                print("\n❌ 刷量模式需要至少 2 个交易所进行对冲")
                print(f"   已初始化: {initialized_exchanges}")
                if missing:
                    print(f"   未初始化: {list(missing)}")
                print("   请检查交易所配置和密钥后重试")
                return False

        # 初始化网络管理器并检查一致性
        self.network_manager = NetworkManager(self.exchanges)

        # 检查网络一致性
        if not self.network_manager.check_network_consistency():
            print("⚠️  警告: 交易所网络不一致!")

        # 根据模式初始化引擎
        print_substage("初始化引擎")
        if mode in ["arbitrage", "both"]:
            # 初始化套利引擎
            self.engine = ArbitrageEngine(self.exchanges, min_spread=0.5)
            print("  ✅ 套利引擎已就绪")

        if mode in ["volume", "both"]:
            # 加载刷量配置
            try:
                with open(self.volume_config_path) as f:
                    volume_config = yaml.safe_load(f)

                if volume_config.get("volume_farming", {}).get("enabled", False):
                    # 先初始化刷量策略
                    targets_config = volume_config["volume_farming"].get("targets", [])
                    targets = [
                        VolumeTarget(
                            symbol=t["symbol"],
                            daily_target_volume=t["daily_target_volume"],
                            priority=t.get("priority", 1),
                        )
                        for t in targets_config
                    ]

                    strategy_config = volume_config["volume_farming"].get("strategy", {})
                    self.volume_strategy = HedgeVolumeStrategy(targets, strategy_config)

                    # 然后初始化刷量引擎，传入策略用于进度跟踪
                    self.volume_engine = VolumeEngine(
                        self.exchanges, volume_config["volume_farming"], volume_strategy=self.volume_strategy
                    )

                else:
                    print("  ⚠️  刷量功能未启用")
                    if mode == "volume":
                        self.run_mode = "arbitrage"  # 降级到套利模式
            except FileNotFoundError:
                print(f"  ⚠️  未找到刷量配置: {self.volume_config_path}")
                if mode == "volume":
                    self.run_mode = "arbitrage"

        # 显示初始化完成信息
        print_substage("初始化完成")
        print(f"  ✅ 模式: {self.run_mode}")
        print(f"  ✅ 交易所: {', '.join(self.exchanges.keys())}")
        if self.volume_strategy:
            print(f"  ✅ 刷量目标: {len(targets)} 个交易对")
        print_section_end()
        return True  # 初始化成功

    async def run(self):
        """运行主循环 - 支持多模式"""
        # 交易阶段分隔符
        print_stage(LogStage.TRADING)

        self.is_running = True
        tasks = []

        # 根据模式创建任务
        if self.run_mode in ["arbitrage", "both"]:
            print("  🔍 启动套利监控...")
            tasks.append(asyncio.create_task(self._run_arbitrage()))

        if self.run_mode in ["volume", "both"]:
            print("  🔄 启动刷量任务...")
            tasks.append(asyncio.create_task(self._run_volume_farming()))
            # 添加统计报告任务
            tasks.append(asyncio.create_task(self._report_volume_stats()))

        if not tasks:
            print("  ⚠️  没有任务运行")
            return

        # 并行运行所有任务
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            print(f"运行错误: {e}")

    async def _run_arbitrage(self):
        """运行套利监控"""
        print("开始监控套利机会...")

        # 获取所有启用的交易对
        symbols = set()
        for exchange in self.exchanges.values():
            symbols.update(exchange.config.get("symbols", []))

        while self.is_running:
            try:
                opportunities = await self.engine.monitor_spreads(list(symbols))

                for opp in opportunities:
                    print(
                        f"📈 套利机会: {opp.symbol} | "
                        f"{opp.exchange_a}({opp.exchange_a_price:.2f}) -> "
                        f"{opp.exchange_b}({opp.exchange_b_price:.2f}) | "
                        f"价差: {opp.spread_percentage:.2f}%"
                    )

                # 控制监控频率
                await asyncio.sleep(0.1)  # 100ms

            except Exception as e:
                print(f"套利监控错误: {e}")
                await asyncio.sleep(1)

    async def _run_volume_farming(self):
        """运行刷量任务"""
        if not self.volume_engine:
            print("  ⚠️  刷量引擎未初始化")
            return

        # 从刷量策略中获取目标交易对
        if self.volume_strategy:
            symbols = list(self.volume_strategy.targets.keys())
        else:
            # 降级方案：从交易所配置中获取
            symbols = set()
            for exchange in self.exchanges.values():
                symbols.update(exchange.config.get("symbols", []))
            symbols = list(symbols)

        try:
            await self.volume_engine.start_volume_farming(symbols)
        except Exception as e:
            import traceback

            print(f"❌ 刷量任务错误: {e}")
            print(traceback.format_exc())

    async def _report_volume_stats(self):
        """定期报告刷量统计"""
        while self.is_running:
            try:
                # 等待5分钟，但每10秒检查一次是否停止
                for _ in range(30):  # 30 * 10秒 = 300秒 = 5分钟
                    if not self.is_running:
                        break
                    await asyncio.sleep(10)

                if not self.is_running:
                    break

                if self.volume_engine:
                    stats = self.volume_engine.get_statistics()
                    print_substage("📊 刷量统计")
                    print(f"  活跃仓位: {stats['active_positions']} | 总开仓: {stats['total_positions_opened']}")
                    print(f"  交易量: ${stats['total_volume_usd']:.2f} | 价差成本: ${stats['total_spread_cost']:.4f}")
                    print(f"  累计盈亏: ${stats['total_pnl']:.4f} | 平均成本: ${stats['avg_spread_cost']:.4f}")
                    print(
                        f"  今日量: ${stats['daily_volume_usd']:.2f} | 剩余额度: ${stats['daily_volume_remaining']:.2f}"
                    )

                if self.volume_strategy:
                    print(self.volume_strategy.get_summary())

            except Exception as e:
                print(f"统计报告错误: {e}")

    async def stop(self):
        """停止机器人"""
        if not self.is_running and not self.exchanges:
            # 已经停止过了
            return

        self.is_running = False

        # 关闭阶段分隔符
        print_stage(LogStage.SHUTDOWN)

        # 停止刷量引擎并平掉所有仓位
        if self.volume_engine:
            self.volume_engine.stop()

            # 平掉所有活跃仓位
            print_substage("关闭仓位")
            try:
                active_count = len(self.volume_engine.active_positions)
                if active_count > 0:
                    print(f"  🔄 关闭 {active_count} 个活跃仓位...")
                    await self.volume_engine.close_all_positions()
                    print("  ✅ 所有仓位已关闭")
                else:
                    print("  ✅ 无需关闭仓位")
            except Exception as e:
                print(f"  ⚠️  关闭仓位时出错: {e}")

            # 打印最终统计
            print_substage("最终统计")
            try:
                stats = self.volume_engine.get_statistics()
                print(f"  总仓位数: {stats['total_positions_opened']}")
                print(f"  总交易量: ${stats['total_volume_usd']:.2f} USD")
                print(f"  总价差成本: ${stats['total_spread_cost']:.4f}")
                print(f"  总盈亏: ${stats['total_pnl']:.4f}")
            except Exception as e:
                print(f"  ⚠️  获取统计信息失败: {e}")

        if self.volume_strategy:
            try:
                print("\n" + self.volume_strategy.get_summary())
            except Exception as e:
                print(f"⚠️  获取策略摘要失败: {e}")

        # 清理资源 - 正确关闭所有交易所连接
        if self.exchanges:
            print_substage("关闭连接")
            close_tasks = []
            for name, exchange in self.exchanges.items():
                try:
                    close_tasks.append(exchange.close())
                except Exception as e:
                    print(f"  ⚠️  创建关闭任务 {name} 时出错: {e}")

            if close_tasks:
                try:
                    await asyncio.gather(*close_tasks, return_exceptions=True)
                    print("  ✅ 所有连接已关闭")
                except Exception as e:
                    print(f"  ⚠️  关闭连接时出错: {e}")

        # 释放进程锁
        self._release_lock()

        print_section_end()
        print("👋 再见!")


async def main():
    # 可以通过命令行参数指定网络和模式
    import argparse

    parser = argparse.ArgumentParser(
        description="OmniTrade - 多功能交易机器人",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行模式说明:
  arbitrage  - 只运行套利监控
  volume     - 只运行刷量模式
  both       - 同时运行套利和刷量

示例:
  # 在测试网运行套利监控
  python -m src.main --network testnet --mode arbitrage

  # 在测试网运行刷量
  python -m src.main --network testnet --mode volume

  # 同时运行两种模式
  python -m src.main --network testnet --mode both
        """,
    )
    parser.add_argument("--network", choices=["mainnet", "testnet"], default="testnet", help="目标网络 (默认: testnet)")
    parser.add_argument(
        "--mode", choices=["arbitrage", "volume", "both"], default="arbitrage", help="运行模式 (默认: arbitrage)"
    )
    args = parser.parse_args()

    target_network = NetworkType(args.network)

    # 启动阶段分隔符
    print_stage(LogStage.STARTUP, f"OmniTrade v1.0 | {args.network.upper()}")

    bot = TradeBot()

    # 设置信号处理 - 使用标志而不是直接退出
    def signal_handler(sig, frame):
        print("\n⚡ 收到停止信号...")
        bot.is_running = False
        # 同时停止所有引擎
        if hasattr(bot, "volume_engine") and bot.volume_engine:
            bot.volume_engine.stop()
        if hasattr(bot, "arbitrage_engine") and bot.arbitrage_engine:
            bot.arbitrage_engine.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # 初始化机器人
        init_success = await bot.initialize(target_network=target_network, mode=args.mode)

        if init_success is False:
            # 初始化失败（可能是进程锁冲突）
            print("程序退出。")
            return

        # 运行机器人
        await bot.run()
    except KeyboardInterrupt:
        pass  # 信号处理器已经设置了标志
    except Exception as e:
        print(f"机器人运行错误: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # 确保在退出前正确清理
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
