import asyncio

from src.core.base_exchange import BaseExchange, NetworkType
from src.exchanges.ccxt_exchange import CCXTExchange

CONNECT_TIMEOUT_SECONDS = 15.0


class ExchangeFactory:
    """交易所工厂，统一创建和管理交易所实例 - 增强网络支持"""

    @staticmethod
    def create_exchange(name: str, config: dict, secrets: dict) -> BaseExchange:
        """根据配置创建交易所实例"""
        exchange_type = config.get("type", "ccxt")

        if exchange_type == "native":
            raise ValueError(f"不支持的native交易所 '{name}': 当前只支持 CCXT 交易所")

        elif exchange_type == "ccxt":
            return CCXTExchange(name, config, secrets)

        else:
            raise ValueError(f"不支持的交易所类型: {exchange_type}")

    @staticmethod
    async def initialize_exchanges(
        exchange_configs: dict, secrets: dict, target_network: NetworkType | None = None
    ) -> dict[str, BaseExchange]:
        """批量初始化所有启用的交易所

        Args:
            exchange_configs: 交易所配置字典
            secrets: 交易所密钥字典
            target_network: 目标网络，如果指定则覆盖配置中的 default_network
        """
        exchanges = {}

        for name, config in exchange_configs.items():
            if config.get("enabled", False):
                try:
                    # 如果指定了目标网络，覆盖配置中的 default_network
                    if target_network:
                        config = config.copy()  # 避免修改原始配置
                        config["default_network"] = target_network.value

                    exchange = ExchangeFactory.create_exchange(name, config, secrets.get(name, {}))
                    await asyncio.wait_for(exchange.connect(), timeout=CONNECT_TIMEOUT_SECONDS)

                    # 简洁输出网络信息
                    network_info = exchange.get_network_info()
                    print(f"  ✅ {name}: {network_info['network']}")

                    exchanges[name] = exchange
                except asyncio.TimeoutError:
                    print(f"  ❌ {name}: connect timed out after {CONNECT_TIMEOUT_SECONDS}s")
                except Exception as e:
                    print(f"  ❌ {name}: {e}")

        return exchanges
