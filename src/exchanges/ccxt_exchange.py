try:
    import ccxt.async_support as ccxt  # type: ignore

    ASYNC_CCXT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    import ccxt  # type: ignore

    ASYNC_CCXT_AVAILABLE = False
from typing import Any

from src.core.base_exchange import BaseExchange, NetworkType


class CCXTExchange(BaseExchange):
    """CCXT支持的交易所统一适配器 - 增强网络支持"""

    def __init__(self, name: str, config: dict, secrets: dict):
        super().__init__(name, config, secrets)
        self.ccxt_exchange = None

    def _sanitize_config_for_log(self, config: dict[str, Any]) -> dict[str, Any]:
        """移除敏感字段，便于日志输出调试"""
        redacted_keys = {"apiKey", "secret", "walletAddress", "privateKey", "vaultAddress"}
        sanitized: dict[str, Any] = {}
        for key, value in config.items():
            if isinstance(value, dict):
                sanitized[key] = self._sanitize_config_for_log(value)
            elif key in redacted_keys and value:
                sanitized[key] = "***"
            else:
                sanitized[key] = value
        return sanitized

    def _build_ccxt_config(self) -> dict[str, Any]:
        """根据交易所类型和网络配置生成CCXT初始化参数"""
        config: dict[str, Any] = {
            "enableRateLimit": True,
        }

        # 所有交易所默认使用永续合约 (swap) 而非现货 (spot)
        options = config.setdefault("options", {})
        options["defaultType"] = "swap"

        if self.name == "hyperliquid":
            wallet_address = self.secrets.get("walletAddress") or self.secrets.get("wallet_address")
            private_key = self.secrets.get("privateKey") or self.secrets.get("private_key")
            vault_address = self.secrets.get("vaultAddress") or self.secrets.get("vault_address")

            if wallet_address:
                config["walletAddress"] = wallet_address
            if private_key:
                config["privateKey"] = private_key

            options = config.setdefault("options", {})
            options["testnet"] = self.network_type == NetworkType.TESTNET
            # Disable fetching HIP3 (user-generated) markets entirely to avoid "Too many DEXes found"
            # By setting types to only ['spot', 'swap'], we skip hip3 market fetching
            options.setdefault("fetchMarkets", {})["types"] = ["spot", "swap"]
            if vault_address:
                options["vaultAddress"] = vault_address
        else:
            api_key = self.secrets.get("api_key") or self.secrets.get("apiKey")
            secret = self.secrets.get("secret") or self.secrets.get("secretKey")

            if api_key:
                config["apiKey"] = api_key
            if secret:
                config["secret"] = secret

        if getattr(self, "rest_base_url", None):
            config.setdefault("urls", {})
            config["urls"]["api"] = {
                "public": self.rest_base_url,
                "private": self.rest_base_url,
            }

        # Merge additional options from config file
        if "options" in self.config:
            existing_options = config.setdefault("options", {})
            # Deep merge or update? Update for now
            # self.config['options'] comes from yaml
            # e.g. {'fetchMarkets': {'hip3': {'dex': []}}}
            # We want to merge this into existing_options

            # Simple update (might overwrite testnet flag if conflict, but yaml shouldn't have testnet flag usually)
            # Better to update carefully
            user_options = self.config["options"]
            for k, v in user_options.items():
                if k == "fetchMarkets" and "fetchMarkets" in existing_options:
                    # Merge fetchMarkets separately if needed, but usually it's empty in default
                    existing_options[k].update(v)
                else:
                    existing_options[k] = v

        return config

    async def connect(self):
        """初始化CCXT交易所实例，支持网络切换"""
        if not ASYNC_CCXT_AVAILABLE:
            raise RuntimeError("未安装 ccxt.async_support，无法使用异步CCXT适配器")

        exchange_class = getattr(ccxt, self.name)

        ccxt_config = self._build_ccxt_config()
        self.logger.debug(f"{self.name} CCXT初始化配置: {self._sanitize_config_for_log(ccxt_config)}")

        self.ccxt_exchange = exchange_class(ccxt_config)

        try:
            await self.ccxt_exchange.load_markets()
            self.logger.debug(f"{self.name} CCXT连接已建立 - 网络: {self.network_type.value}")

        except Exception as e:
            self.logger.error(f"{self.name} 市场加载失败: {e}")
            raise

    async def connect_websocket(self) -> bool:
        """CCXT通常不直接处理WebSocket，返回False让使用独立WebSocket连接"""
        self.logger.info(f"{self.name} CCXT适配器使用REST API，WebSocket需要单独实现")
        return False

    async def subscribe_orderbook(self, symbol: str):
        """CCXT通常不直接处理WebSocket订阅"""
        pass

    async def fetch_balance(self, params: dict[str, Any] | None = None) -> dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_balance(params)

    async def fetch_orderbook(self, symbol: str, limit: int = 10, params: dict[str, Any] | None = None) -> dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_order_book(symbol, limit, params)

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict:
        params = params or {}
        return await self.ccxt_exchange.create_order(symbol, order_type, side, amount, price, params)

    async def cancel_order(self, order_id: str, symbol: str, params: dict[str, Any] | None = None) -> bool:
        params = params or {}
        result = await self.ccxt_exchange.cancel_order(order_id, symbol, params)
        return result.get("status") in ["canceled", "closed"]

    async def fetch_order(self, order_id: str, symbol: str, params: dict[str, Any] | None = None) -> dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_order(order_id, symbol, params)

    async def list_markets(self) -> list:
        """Return instruments for this exchange. Full impl in integration stage."""
        return []

    async def close(self):
        if self.ccxt_exchange:
            try:
                await self.ccxt_exchange.close()
            except Exception as exc:
                self.logger.warning(f"{self.name} 关闭CCXT实例时出错: {exc}")
        await super().close()
