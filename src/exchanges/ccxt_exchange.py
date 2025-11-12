try:
    import ccxt.async_support as ccxt  # type: ignore
    ASYNC_CCXT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    import ccxt  # type: ignore
    ASYNC_CCXT_AVAILABLE = False
from typing import Any, Dict, Optional
from src.core.base_exchange import BaseExchange, NetworkType

class CCXTExchange(BaseExchange):
    """CCXT支持的交易所统一适配器 - 增强网络支持"""
    
    def __init__(self, name: str, config: Dict, secrets: Dict):
        super().__init__(name, config, secrets)
        self.ccxt_exchange = None

    def _sanitize_config_for_log(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """移除敏感字段，便于日志输出调试"""
        redacted_keys = {'apiKey', 'secret', 'walletAddress', 'privateKey', 'vaultAddress'}
        sanitized: Dict[str, Any] = {}
        for key, value in config.items():
            if isinstance(value, dict):
                sanitized[key] = self._sanitize_config_for_log(value)
            elif key in redacted_keys and value:
                sanitized[key] = '***'
            else:
                sanitized[key] = value
        return sanitized

    def _build_ccxt_config(self) -> Dict[str, Any]:
        """根据交易所类型和网络配置生成CCXT初始化参数"""
        config: Dict[str, Any] = {
            'enableRateLimit': True,
        }

        if self.name == 'hyperliquid':
            wallet_address = self.secrets.get('walletAddress') or self.secrets.get('wallet_address')
            private_key = self.secrets.get('privateKey') or self.secrets.get('private_key')
            vault_address = self.secrets.get('vaultAddress') or self.secrets.get('vault_address')

            if wallet_address:
                config['walletAddress'] = wallet_address
            if private_key:
                config['privateKey'] = private_key

            options = config.setdefault('options', {})
            options['testnet'] = self.network_type == NetworkType.TESTNET
            if vault_address:
                options['vaultAddress'] = vault_address
        elif self.name == 'paradex':
            def get_secret(*keys: str) -> Optional[Any]:
                for key in keys:
                    value = self.secrets.get(key)
                    if isinstance(value, str):
                        value = value.strip()
                    if value not in (None, '', {}):
                        return value
                return None

            def normalize_hex(value: Any) -> str:
                if isinstance(value, int):
                    hex_str = hex(value)
                else:
                    hex_str = str(value).strip()
                if not hex_str.startswith('0x'):
                    if len(hex_str) % 2 != 0:
                        hex_str = '0' + hex_str
                    hex_str = '0x' + hex_str
                else:
                    body = hex_str[2:]
                    if len(body) % 2 != 0:
                        hex_str = '0x0' + body
                return hex_str.lower()

            options = config.setdefault('options', {})
            options['testnet'] = self.network_type == NetworkType.TESTNET

            # 方式一：ETH 私钥 + 钱包地址
            eth_private_raw = get_secret(
                'eth_private_key',
                'ethPrivateKey',
                'privateKey',
                'private_key'
            )
            eth_wallet_raw = get_secret(
                'eth_wallet_address',
                'ethWalletAddress',
                'walletAddress',
                'wallet_address',
                'eth_address'
            )

            if eth_private_raw and eth_wallet_raw:
                config['privateKey'] = normalize_hex(eth_private_raw)
                config['walletAddress'] = str(eth_wallet_raw)
                options.pop('paradexAccount', None)
            else:
                config.pop('privateKey', None)

                # 方式二：Starknet 账号信息
                stark_private_raw = get_secret(
                    'stark_private_key',
                    'starkPrivateKey',
                    'paradex_private_key',
                    'privatekey'
                )
                stark_public_raw = get_secret(
                    'stark_public_key',
                    'starkPublicKey',
                    'paradex_public_key',
                    'publickey'
                )
                stark_eth_raw = get_secret(
                    'stark_address',
                    'starkAddress',
                    'paradex_address',
                    'eth_address',
                    'address'
                )

                if not (stark_private_raw and stark_public_raw and stark_eth_raw):
                    raise ValueError(
                        "Paradex 配置缺少必需的凭证：请提供 ETH 私钥与地址，"
                        "或同时配置 stark_private_key / stark_public_key / stark_address"
                    )

                options['paradexAccount'] = {
                    'privateKey': normalize_hex(stark_private_raw),
                    'publicKey': normalize_hex(stark_public_raw),
                    'address': str(stark_eth_raw),
                }
                config['walletAddress'] = str(stark_eth_raw)
        else:
            api_key = self.secrets.get('api_key') or self.secrets.get('apiKey')
            secret = self.secrets.get('secret') or self.secrets.get('secretKey')

            if api_key:
                config['apiKey'] = api_key
            if secret:
                config['secret'] = secret

        if getattr(self, 'rest_base_url', None):
            config.setdefault('urls', {})
            config['urls']['api'] = {
                'public': self.rest_base_url,
                'private': self.rest_base_url,
            }

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
            self.logger.info(f"{self.name} CCXT连接已建立 - 网络: {self.network_type.value}")

            if self.name == 'paradex':
                try:
                    current_options = getattr(self.ccxt_exchange, 'options', {})
                    existing_account = None
                    if isinstance(current_options, dict):
                        existing_account = current_options.get('paradexAccount')

                    if not existing_account:
                        account = await self.ccxt_exchange.retrieve_account()
                        if isinstance(account, dict):
                            if isinstance(account.get('publicKey'), int):
                                account['publicKey'] = hex(account['publicKey'])
                            current_options['paradexAccount'] = account

                    await self.ccxt_exchange.onboarding()
                    self.logger.info("Paradex Onboarding 已完成或此前已注册")
                except Exception as onboarding_error:
                    message = str(onboarding_error)
                    if "ALREADY_ONBOARDED" in message:
                        self.logger.info("Paradex 已完成 Onboarding")
                    else:
                        self.logger.error(f"Paradex Onboarding 失败: {onboarding_error}")
                        raise

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
        
    async def fetch_balance(self, params: Optional[Dict[str, Any]] = None) -> Dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_balance(params)

    async def fetch_orderbook(self, symbol: str, limit: int = 10,
                              params: Optional[Dict[str, Any]] = None) -> Dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_order_book(symbol, limit, params)

    async def create_order(self, symbol: str, order_type: str, side: str, 
                         amount: float, price: Optional[float] = None,
                         params: Optional[Dict[str, Any]] = None) -> Dict:
        params = params or {}
        return await self.ccxt_exchange.create_order(symbol, order_type, side, amount, price, params)

    async def cancel_order(self, order_id: str, symbol: str,
                           params: Optional[Dict[str, Any]] = None) -> bool:
        params = params or {}
        result = await self.ccxt_exchange.cancel_order(order_id, symbol, params)
        return result.get('status') in ['canceled', 'closed']

    async def fetch_order(self, order_id: str, symbol: str,
                          params: Optional[Dict[str, Any]] = None) -> Dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_order(order_id, symbol, params)

    async def close(self):
        if self.ccxt_exchange:
            try:
                await self.ccxt_exchange.close()
            except Exception as exc:
                self.logger.warning(f"{self.name} 关闭CCXT实例时出错: {exc}")
        await super().close()