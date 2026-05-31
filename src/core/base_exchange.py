import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from src.market.instrument import Instrument


class NetworkType(Enum):
    MAINNET = "mainnet"
    TESTNET = "testnet"


class BaseExchange(ABC):
    """所有交易所适配器的统一接口基类 - 增强网络支持"""

    def __init__(self, name: str, config: dict, secrets: dict):
        self.name = name
        self.config = config
        self.secrets = secrets
        self.logger = logging.getLogger(f"exchange.{name}")

        # 网络配置
        self.network_type = NetworkType(config.get("default_network", "testnet"))
        self.network_config = config["networks"][self.network_type.value]

        # HTTP 会话
        self._session: aiohttp.ClientSession | None = None
        self._websocket: Any | None = None

        # 端点
        self.rest_base_url = self.network_config["rest_base_url"]
        self.websocket_url = self.network_config["websocket_url"]
        self.api_paths = self.network_config.get("api_paths", {})

        # 费率配置
        self.fees = config.get("fees", {"taker": 0.0005, "maker": 0.0002})

        # 余额缓存
        self._balance_cache: dict | None = None
        self._balance_cache_ts: float = 0.0
        self._balance_cache_ttl: float = 2.0  # seconds

    def get_fee_rate(self, symbol: str = None, order_type: str = "market", side: str = "buy") -> float:
        """
        获取费率

        Args:
            symbol: 交易对
            order_type: 订单类型 ('market' or 'limit')
            side: 方向 ('buy' or 'sell')

        Returns:
            float: 费率 (e.g. 0.0005 for 0.05%)
        """
        # 简单实现：市价单用 taker，限价单用 maker
        if order_type == "market":
            return self.fees.get("taker", 0.0005)
        else:
            return self.fees.get("maker", 0.0002)

    def get_network_info(self) -> dict:
        """获取当前网络信息"""
        return {
            "network": self.network_type.value,
            "rest_base_url": self.rest_base_url,
            "websocket_url": self.websocket_url,
            "is_testnet": self.network_type == NetworkType.TESTNET,
        }

    def switch_network(self, network: NetworkType) -> bool:
        """切换网络（主网/测试网）"""
        if network.value not in self.config["networks"]:
            self.logger.error(f"网络 {network.value} 不支持")
            return False

        old_network = self.network_type
        self.network_type = network
        self.network_config = self.config["networks"][network.value]
        self.rest_base_url = self.network_config["rest_base_url"]
        self.websocket_url = self.network_config["websocket_url"]
        self.api_paths = self.network_config.get("api_paths", {})

        self.logger.info(f"已切换网络: {old_network.value} -> {network.value}")
        return True

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保HTTP会话存在"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _http_request(self, method: str, endpoint: str, authenticated: bool = False, **kwargs) -> dict:
        """统一的HTTP请求方法"""
        session = await self._ensure_session()
        url = f"{self.rest_base_url}{endpoint}"

        headers = kwargs.pop("headers", {})
        if authenticated:
            headers.update(self._get_auth_headers(method, endpoint, kwargs.get("data")))

        try:
            async with session.request(method, url, headers=headers, **kwargs) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP请求失败: {e}")
            raise
        except Exception as e:
            self.logger.error(f"请求处理失败: {e}")
            raise

    def _get_auth_headers(self, method: str, endpoint: str, data: dict | None = None) -> dict:
        """生成认证头信息 - 子类可重写"""
        # 基础实现，具体交易所需要实现自己的签名逻辑
        return {"API-KEY": self.secrets.get("api_key", ""), "Content-Type": "application/json"}

    @abstractmethod
    async def list_markets(self) -> list["Instrument"]:
        """返回该交易所所有可交易品种的 Instrument 列表。"""

    @abstractmethod
    async def connect_websocket(self) -> bool:
        """连接WebSocket"""

    @abstractmethod
    async def subscribe_orderbook(self, symbol: str):
        """订阅订单簿更新"""

    async def connect(self):
        """建立交易所连接"""

    async def fetch_balance(self) -> dict:
        now = time.time()
        if self._balance_cache is not None and (now - self._balance_cache_ts) < self._balance_cache_ttl:
            return self._balance_cache
        balance = await self._fetch_balance_impl()
        self._balance_cache = balance
        self._balance_cache_ts = time.time()
        return balance

    def invalidate_balance_cache(self) -> None:
        self._balance_cache = None

    @abstractmethod
    async def _fetch_balance_impl(self) -> dict:
        """Actual balance fetch — implemented by subclasses."""

    @abstractmethod
    async def fetch_orderbook(self, symbol: str, limit: int = 10) -> dict:
        """获取订单簿"""

    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        """创建订单"""

    @abstractmethod
    async def cancel_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> bool:
        """取消订单"""

    @abstractmethod
    async def fetch_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> dict:
        """获取订单状态"""

    async def close(self):
        """清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._websocket:
            await self._websocket.close()

    # ── Full ccxt API surface (default NotImplementedError) ────────
    # Subclasses must override to opt-in; ccxt-based adapters override all.

    async def add_margin(self, symbol, amount, params=None) -> dict:
        raise NotImplementedError(f"add_margin not implemented for {self.name}")

    async def borrow_cross_margin(self, code, amount, params=None) -> dict:
        raise NotImplementedError(f"borrow_cross_margin not implemented for {self.name}")

    async def borrow_isolated_margin(self, symbol, code, amount, params=None) -> dict:
        raise NotImplementedError(f"borrow_isolated_margin not implemented for {self.name}")

    async def borrow_margin(self, code, amount, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"borrow_margin not implemented for {self.name}")

    async def cancel_all_contract_orders(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_all_contract_orders not implemented for {self.name}")

    async def cancel_all_orders(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_all_orders not implemented for {self.name}")

    async def cancel_all_orders_after(self, timeout, params=None) -> dict:
        raise NotImplementedError(f"cancel_all_orders_after not implemented for {self.name}")

    async def cancel_all_orders_ws(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_all_orders_ws not implemented for {self.name}")

    async def cancel_all_spot_orders(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_all_spot_orders not implemented for {self.name}")

    async def cancel_contract_order(self, id, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_contract_order not implemented for {self.name}")

    async def cancel_order_with_client_order_id(self, clientOrderId, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_order_with_client_order_id not implemented for {self.name}")

    async def cancel_order_ws(self, id, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_order_ws not implemented for {self.name}")

    async def cancel_orders(self, ids, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_orders not implemented for {self.name}")

    async def cancel_orders_for_symbols(self, orders, params=None) -> dict:
        raise NotImplementedError(f"cancel_orders_for_symbols not implemented for {self.name}")

    async def cancel_orders_with_client_order_ids(self, clientOrderIds, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_orders_with_client_order_ids not implemented for {self.name}")

    async def cancel_orders_ws(self, ids, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_orders_ws not implemented for {self.name}")

    async def cancel_spot_order(self, id, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"cancel_spot_order not implemented for {self.name}")

    async def cancel_unified_order(self, order, params=None) -> dict:
        raise NotImplementedError(f"cancel_unified_order not implemented for {self.name}")

    async def close_all_positions(self, params=None) -> dict:
        raise NotImplementedError(f"close_all_positions not implemented for {self.name}")

    async def close_position(self, symbol, side=None, params=None) -> dict:
        raise NotImplementedError(f"close_position not implemented for {self.name}")

    async def close_proxy_sessions(self) -> dict:
        raise NotImplementedError(f"close_proxy_sessions not implemented for {self.name}")

    async def create_contract_orders(self, orders, params=None) -> dict:
        raise NotImplementedError(f"create_contract_orders not implemented for {self.name}")

    async def create_convert_trade(self, id, fromCode, toCode, amount=None, params=None) -> dict:
        raise NotImplementedError(f"create_convert_trade not implemented for {self.name}")

    async def create_deposit_address(self, code, params=None) -> dict:
        raise NotImplementedError(f"create_deposit_address not implemented for {self.name}")

    async def create_limit_buy_order(self, symbol, amount, price, params=None) -> dict:
        raise NotImplementedError(f"create_limit_buy_order not implemented for {self.name}")

    async def create_limit_buy_order_ws(self, symbol, amount, price, params=None) -> dict:
        raise NotImplementedError(f"create_limit_buy_order_ws not implemented for {self.name}")

    async def create_limit_order(self, symbol, side, amount, price, params=None) -> dict:
        raise NotImplementedError(f"create_limit_order not implemented for {self.name}")

    async def create_limit_order_ws(self, symbol, side, amount, price, params=None) -> dict:
        raise NotImplementedError(f"create_limit_order_ws not implemented for {self.name}")

    async def create_limit_sell_order(self, symbol, amount, price, params=None) -> dict:
        raise NotImplementedError(f"create_limit_sell_order not implemented for {self.name}")

    async def create_limit_sell_order_ws(self, symbol, amount, price, params=None) -> dict:
        raise NotImplementedError(f"create_limit_sell_order_ws not implemented for {self.name}")

    async def create_market_buy_order(self, symbol, amount, params=None) -> dict:
        raise NotImplementedError(f"create_market_buy_order not implemented for {self.name}")

    async def create_market_buy_order_with_cost(self, symbol, cost, params=None) -> dict:
        raise NotImplementedError(f"create_market_buy_order_with_cost not implemented for {self.name}")

    async def create_market_buy_order_ws(self, symbol, amount, params=None) -> dict:
        raise NotImplementedError(f"create_market_buy_order_ws not implemented for {self.name}")

    async def create_market_order(self, symbol, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"create_market_order not implemented for {self.name}")

    async def create_market_order_with_cost(self, symbol, side, cost, params=None) -> dict:
        raise NotImplementedError(f"create_market_order_with_cost not implemented for {self.name}")

    async def create_market_order_with_cost_ws(self, symbol, side, cost, params=None) -> dict:
        raise NotImplementedError(f"create_market_order_with_cost_ws not implemented for {self.name}")

    async def create_market_order_ws(self, symbol, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"create_market_order_ws not implemented for {self.name}")

    async def create_market_sell_order(self, symbol, amount, params=None) -> dict:
        raise NotImplementedError(f"create_market_sell_order not implemented for {self.name}")

    async def create_market_sell_order_with_cost(self, symbol, cost, params=None) -> dict:
        raise NotImplementedError(f"create_market_sell_order_with_cost not implemented for {self.name}")

    async def create_market_sell_order_ws(self, symbol, amount, params=None) -> dict:
        raise NotImplementedError(f"create_market_sell_order_ws not implemented for {self.name}")

    async def create_order_with_take_profit_and_stop_loss(self, symbol, type, side, amount, price=None, takeProfit=None, stopLoss=None, params=None) -> dict:
        raise NotImplementedError(f"create_order_with_take_profit_and_stop_loss not implemented for {self.name}")

    async def create_order_with_take_profit_and_stop_loss_ws(self, symbol, type, side, amount, price=None, takeProfit=None, stopLoss=None, params=None) -> dict:
        raise NotImplementedError(f"create_order_with_take_profit_and_stop_loss_ws not implemented for {self.name}")

    async def create_order_ws(self, symbol, type, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"create_order_ws not implemented for {self.name}")

    async def create_orders(self, orders, params=None) -> dict:
        raise NotImplementedError(f"create_orders not implemented for {self.name}")

    async def create_orders_ws(self, orders, params=None) -> dict:
        raise NotImplementedError(f"create_orders_ws not implemented for {self.name}")

    async def create_post_only_order(self, symbol, type, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"create_post_only_order not implemented for {self.name}")

    async def create_post_only_order_ws(self, symbol, type, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"create_post_only_order_ws not implemented for {self.name}")

    async def create_reduce_only_order(self, symbol, type, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"create_reduce_only_order not implemented for {self.name}")

    async def create_reduce_only_order_ws(self, symbol, type, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"create_reduce_only_order_ws not implemented for {self.name}")

    async def create_spot_orders(self, orders, params=None) -> dict:
        raise NotImplementedError(f"create_spot_orders not implemented for {self.name}")

    async def create_stop_limit_order(self, symbol, side, amount, price, triggerPrice, params=None) -> dict:
        raise NotImplementedError(f"create_stop_limit_order not implemented for {self.name}")

    async def create_stop_limit_order_ws(self, symbol, side, amount, price, triggerPrice, params=None) -> dict:
        raise NotImplementedError(f"create_stop_limit_order_ws not implemented for {self.name}")

    async def create_stop_loss_order(self, symbol, type, side, amount, price=None, stopLossPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_stop_loss_order not implemented for {self.name}")

    async def create_stop_loss_order_ws(self, symbol, type, side, amount, price=None, stopLossPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_stop_loss_order_ws not implemented for {self.name}")

    async def create_stop_market_order(self, symbol, side, amount, triggerPrice, params=None) -> dict:
        raise NotImplementedError(f"create_stop_market_order not implemented for {self.name}")

    async def create_stop_market_order_ws(self, symbol, side, amount, triggerPrice, params=None) -> dict:
        raise NotImplementedError(f"create_stop_market_order_ws not implemented for {self.name}")

    async def create_stop_order(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_stop_order not implemented for {self.name}")

    async def create_stop_order_ws(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_stop_order_ws not implemented for {self.name}")

    async def create_sub_account(self, name, params=None) -> dict:
        raise NotImplementedError(f"create_sub_account not implemented for {self.name}")

    async def create_take_profit_order(self, symbol, type, side, amount, price=None, takeProfitPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_take_profit_order not implemented for {self.name}")

    async def create_take_profit_order_ws(self, symbol, type, side, amount, price=None, takeProfitPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_take_profit_order_ws not implemented for {self.name}")

    async def create_trailing_amount_order(self, symbol, type, side, amount, price=None, trailingAmount=None, trailingTriggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_trailing_amount_order not implemented for {self.name}")

    async def create_trailing_amount_order_ws(self, symbol, type, side, amount, price=None, trailingAmount=None, trailingTriggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_trailing_amount_order_ws not implemented for {self.name}")

    async def create_trailing_percent_order(self, symbol, type, side, amount, price=None, trailingPercent=None, trailingTriggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_trailing_percent_order not implemented for {self.name}")

    async def create_trailing_percent_order_ws(self, symbol, type, side, amount, price=None, trailingPercent=None, trailingTriggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_trailing_percent_order_ws not implemented for {self.name}")

    async def create_trigger_order(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_trigger_order not implemented for {self.name}")

    async def create_trigger_order_ws(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        raise NotImplementedError(f"create_trigger_order_ws not implemented for {self.name}")

    async def create_twap_order(self, symbol, side, amount, duration, params=None) -> dict:
        raise NotImplementedError(f"create_twap_order not implemented for {self.name}")

    async def edit_limit_buy_order(self, id, symbol, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"edit_limit_buy_order not implemented for {self.name}")

    async def edit_limit_order(self, id, symbol, side, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"edit_limit_order not implemented for {self.name}")

    async def edit_limit_sell_order(self, id, symbol, amount, price=None, params=None) -> dict:
        raise NotImplementedError(f"edit_limit_sell_order not implemented for {self.name}")

    async def edit_order(self, id, symbol, type, side, amount=None, price=None, params=None) -> dict:
        raise NotImplementedError(f"edit_order not implemented for {self.name}")

    async def edit_order_with_client_order_id(self, clientOrderId, symbol, type, side, amount=None, price=None, params=None) -> dict:
        raise NotImplementedError(f"edit_order_with_client_order_id not implemented for {self.name}")

    async def edit_order_ws(self, id, symbol, type, side, amount=None, price=None, params=None) -> dict:
        raise NotImplementedError(f"edit_order_ws not implemented for {self.name}")

    async def edit_orders(self, orders, params=None) -> dict:
        raise NotImplementedError(f"edit_orders not implemented for {self.name}")

    async def fetch_accounts(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_accounts not implemented for {self.name}")

    async def fetch_adl_rank(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_adl_rank not implemented for {self.name}")

    async def fetch_all_greeks(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_all_greeks not implemented for {self.name}")

    async def fetch_bids_asks(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_bids_asks not implemented for {self.name}")

    async def fetch_borrow_interest(self, code=None, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_borrow_interest not implemented for {self.name}")

    async def fetch_borrow_rate(self, code, amount, params=None) -> dict:
        raise NotImplementedError(f"fetch_borrow_rate not implemented for {self.name}")

    async def fetch_canceled_and_closed_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_canceled_and_closed_orders not implemented for {self.name}")

    async def fetch_canceled_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_canceled_orders not implemented for {self.name}")

    async def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_closed_orders not implemented for {self.name}")

    async def fetch_contract_deposit_address(self, code, params=None) -> dict:
        raise NotImplementedError(f"fetch_contract_deposit_address not implemented for {self.name}")

    async def fetch_contract_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_contract_ohlcv not implemented for {self.name}")

    async def fetch_contract_tickers(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_contract_tickers not implemented for {self.name}")

    async def fetch_convert_currencies(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_convert_currencies not implemented for {self.name}")

    async def fetch_convert_quote(self, fromCode, toCode, amount=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_convert_quote not implemented for {self.name}")

    async def fetch_convert_trade(self, id, code=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_convert_trade not implemented for {self.name}")

    async def fetch_convert_trade_history(self, code=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_convert_trade_history not implemented for {self.name}")

    async def fetch_cross_borrow_rate(self, code, params=None) -> dict:
        raise NotImplementedError(f"fetch_cross_borrow_rate not implemented for {self.name}")

    async def fetch_cross_borrow_rates(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_cross_borrow_rates not implemented for {self.name}")

    async def fetch_currencies(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_currencies not implemented for {self.name}")

    async def fetch_deposit_address(self, code, params=None) -> dict:
        raise NotImplementedError(f"fetch_deposit_address not implemented for {self.name}")

    async def fetch_deposit_addresses(self, codes=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_deposit_addresses not implemented for {self.name}")

    async def fetch_deposit_addresses_by_network(self, code, params=None) -> dict:
        raise NotImplementedError(f"fetch_deposit_addresses_by_network not implemented for {self.name}")

    async def fetch_deposit_withdraw_fee(self, code, params=None) -> dict:
        raise NotImplementedError(f"fetch_deposit_withdraw_fee not implemented for {self.name}")

    async def fetch_deposit_withdraw_fees(self, codes=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_deposit_withdraw_fees not implemented for {self.name}")

    async def fetch_deposits(self, code=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_deposits not implemented for {self.name}")

    async def fetch_deposits_withdrawals(self, code=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_deposits_withdrawals not implemented for {self.name}")

    async def fetch_free_balance(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_free_balance not implemented for {self.name}")

    async def fetch_funding_history(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_funding_history not implemented for {self.name}")

    async def fetch_funding_interval(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_funding_interval not implemented for {self.name}")

    async def fetch_funding_intervals(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_funding_intervals not implemented for {self.name}")

    async def fetch_funding_rate(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_funding_rate not implemented for {self.name}")

    async def fetch_funding_rate_history(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_funding_rate_history not implemented for {self.name}")

    async def fetch_funding_rates(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_funding_rates not implemented for {self.name}")

    async def fetch_greeks(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_greeks not implemented for {self.name}")

    async def fetch_index_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_index_ohlcv not implemented for {self.name}")

    async def fetch_isolated_borrow_rate(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_isolated_borrow_rate not implemented for {self.name}")

    async def fetch_isolated_borrow_rates(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_isolated_borrow_rates not implemented for {self.name}")

    async def fetch_l2_order_book(self, symbol, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_l2_order_book not implemented for {self.name}")

    async def fetch_l3_order_book(self, symbol, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_l3_order_book not implemented for {self.name}")

    async def fetch_last_prices(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_last_prices not implemented for {self.name}")

    async def fetch_ledger(self, code=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_ledger not implemented for {self.name}")

    async def fetch_ledger_entry(self, id, code=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_ledger_entry not implemented for {self.name}")

    async def fetch_leverage(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_leverage not implemented for {self.name}")

    async def fetch_leverage_tiers(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_leverage_tiers not implemented for {self.name}")

    async def fetch_leverages(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_leverages not implemented for {self.name}")

    async def fetch_liquidations(self, symbol, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_liquidations not implemented for {self.name}")

    async def fetch_long_short_ratio(self, symbol, timeframe=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_long_short_ratio not implemented for {self.name}")

    async def fetch_long_short_ratio_history(self, symbol=None, timeframe=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_long_short_ratio_history not implemented for {self.name}")

    async def fetch_margin_adjustment_history(self, symbol=None, type=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_margin_adjustment_history not implemented for {self.name}")

    async def fetch_margin_mode(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_margin_mode not implemented for {self.name}")

    async def fetch_margin_modes(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_margin_modes not implemented for {self.name}")

    async def fetch_mark_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_mark_ohlcv not implemented for {self.name}")

    async def fetch_mark_price(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_mark_price not implemented for {self.name}")

    async def fetch_mark_prices(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_mark_prices not implemented for {self.name}")

    async def fetch_market_leverage_tiers(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_market_leverage_tiers not implemented for {self.name}")

    async def fetch_markets(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_markets not implemented for {self.name}")

    async def fetch_my_liquidations(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_my_liquidations not implemented for {self.name}")

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_my_trades not implemented for {self.name}")

    async def fetch_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_ohlcv not implemented for {self.name}")

    async def fetch_ohlcv_ws(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_ohlcv_ws not implemented for {self.name}")

    async def fetch_open_interest(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_open_interest not implemented for {self.name}")

    async def fetch_open_interest_history(self, symbol, timeframe='1h', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_open_interest_history not implemented for {self.name}")

    async def fetch_open_interests(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_open_interests not implemented for {self.name}")

    async def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_open_orders not implemented for {self.name}")

    async def fetch_option(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_option not implemented for {self.name}")

    async def fetch_option_chain(self, code, params=None) -> dict:
        raise NotImplementedError(f"fetch_option_chain not implemented for {self.name}")

    async def fetch_order_book(self, symbol, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_order_book not implemented for {self.name}")

    async def fetch_order_books(self, symbols=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_order_books not implemented for {self.name}")

    async def fetch_order_status(self, id, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_order_status not implemented for {self.name}")

    async def fetch_order_trades(self, id, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_order_trades not implemented for {self.name}")

    async def fetch_order_with_client_order_id(self, clientOrderId, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_order_with_client_order_id not implemented for {self.name}")

    async def fetch_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_orders not implemented for {self.name}")

    async def fetch_partial_balance(self, part, params=None) -> dict:
        raise NotImplementedError(f"fetch_partial_balance not implemented for {self.name}")

    async def fetch_payment_methods(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_payment_methods not implemented for {self.name}")

    async def fetch_position(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_position not implemented for {self.name}")

    async def fetch_position_adl_rank(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_position_adl_rank not implemented for {self.name}")

    async def fetch_position_history(self, symbol, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_position_history not implemented for {self.name}")

    async def fetch_position_mode(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_position_mode not implemented for {self.name}")

    async def fetch_positions(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_positions not implemented for {self.name}")

    async def fetch_positions_adl_rank(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_positions_adl_rank not implemented for {self.name}")

    async def fetch_positions_for_symbol(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_positions_for_symbol not implemented for {self.name}")

    async def fetch_positions_for_symbol_ws(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_positions_for_symbol_ws not implemented for {self.name}")

    async def fetch_positions_history(self, symbols=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_positions_history not implemented for {self.name}")

    async def fetch_positions_risk(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_positions_risk not implemented for {self.name}")

    async def fetch_premium_index_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_premium_index_ohlcv not implemented for {self.name}")

    async def fetch_spot_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_spot_ohlcv not implemented for {self.name}")

    async def fetch_spot_tickers(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_spot_tickers not implemented for {self.name}")

    async def fetch_status(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_status not implemented for {self.name}")

    async def fetch_ticker(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_ticker not implemented for {self.name}")

    async def fetch_tickers(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_tickers not implemented for {self.name}")

    async def fetch_time(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_time not implemented for {self.name}")

    async def fetch_total_balance(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_total_balance not implemented for {self.name}")

    async def fetch_trades(self, symbol, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_trades not implemented for {self.name}")

    async def fetch_trades_ws(self, symbol, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_trades_ws not implemented for {self.name}")

    async def fetch_trading_fee(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"fetch_trading_fee not implemented for {self.name}")

    async def fetch_trading_fees(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_trading_fees not implemented for {self.name}")

    async def fetch_trading_limits(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_trading_limits not implemented for {self.name}")

    async def fetch_transaction_fee(self, code, params=None) -> dict:
        raise NotImplementedError(f"fetch_transaction_fee not implemented for {self.name}")

    async def fetch_transaction_fees(self, codes=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_transaction_fees not implemented for {self.name}")

    async def fetch_transactions(self, code=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_transactions not implemented for {self.name}")

    async def fetch_transfer(self, id, code=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_transfer not implemented for {self.name}")

    async def fetch_transfers(self, code=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_transfers not implemented for {self.name}")

    async def fetch_unified_order(self, order, params=None) -> dict:
        raise NotImplementedError(f"fetch_unified_order not implemented for {self.name}")

    async def fetch_used_balance(self, params=None) -> dict:
        raise NotImplementedError(f"fetch_used_balance not implemented for {self.name}")

    async def fetch_withdrawals(self, code=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"fetch_withdrawals not implemented for {self.name}")

    async def is_uta_enabled(self, params=None) -> dict:
        raise NotImplementedError(f"is_uta_enabled not implemented for {self.name}")

    async def load_accounts(self, reload=False, params=None) -> dict:
        raise NotImplementedError(f"load_accounts not implemented for {self.name}")

    async def load_fees(self, reload=False) -> dict:
        raise NotImplementedError(f"load_fees not implemented for {self.name}")

    async def load_markets(self, reload=False, params=None) -> dict:
        raise NotImplementedError(f"load_markets not implemented for {self.name}")

    async def load_time_difference(self, params=None) -> dict:
        raise NotImplementedError(f"load_time_difference not implemented for {self.name}")

    async def load_trading_limits(self, symbols=None, reload=False, params=None) -> dict:
        raise NotImplementedError(f"load_trading_limits not implemented for {self.name}")

    async def reduce_margin(self, symbol, amount, params=None) -> dict:
        raise NotImplementedError(f"reduce_margin not implemented for {self.name}")

    async def repay_cross_margin(self, code, amount, params=None) -> dict:
        raise NotImplementedError(f"repay_cross_margin not implemented for {self.name}")

    async def repay_isolated_margin(self, symbol, code, amount, params=None) -> dict:
        raise NotImplementedError(f"repay_isolated_margin not implemented for {self.name}")

    async def repay_margin(self, code, amount, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"repay_margin not implemented for {self.name}")

    async def set_leverage(self, leverage, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"set_leverage not implemented for {self.name}")

    async def set_margin(self, symbol, amount, params=None) -> dict:
        raise NotImplementedError(f"set_margin not implemented for {self.name}")

    async def set_margin_mode(self, marginMode, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"set_margin_mode not implemented for {self.name}")

    async def set_position_mode(self, hedged, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"set_position_mode not implemented for {self.name}")

    async def sign_in(self, params=None) -> dict:
        raise NotImplementedError(f"sign_in not implemented for {self.name}")

    async def transfer(self, code, amount, fromAccount, toAccount, params=None) -> dict:
        raise NotImplementedError(f"transfer not implemented for {self.name}")

    async def un_watch_bids_asks(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"un_watch_bids_asks not implemented for {self.name}")

    async def un_watch_funding_rate(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"un_watch_funding_rate not implemented for {self.name}")

    async def un_watch_funding_rates(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"un_watch_funding_rates not implemented for {self.name}")

    async def un_watch_mark_price(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"un_watch_mark_price not implemented for {self.name}")

    async def un_watch_mark_prices(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"un_watch_mark_prices not implemented for {self.name}")

    async def un_watch_my_trades(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"un_watch_my_trades not implemented for {self.name}")

    async def un_watch_ohlcv(self, symbol, timeframe='1m', params=None) -> dict:
        raise NotImplementedError(f"un_watch_ohlcv not implemented for {self.name}")

    async def un_watch_ohlcv_for_symbols(self, symbolsAndTimeframes, params=None) -> dict:
        raise NotImplementedError(f"un_watch_ohlcv_for_symbols not implemented for {self.name}")

    async def un_watch_order_book(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"un_watch_order_book not implemented for {self.name}")

    async def un_watch_order_book_for_symbols(self, symbols, params=None) -> dict:
        raise NotImplementedError(f"un_watch_order_book_for_symbols not implemented for {self.name}")

    async def un_watch_orders(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"un_watch_orders not implemented for {self.name}")

    async def un_watch_positions(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"un_watch_positions not implemented for {self.name}")

    async def un_watch_ticker(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"un_watch_ticker not implemented for {self.name}")

    async def un_watch_tickers(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"un_watch_tickers not implemented for {self.name}")

    async def un_watch_trades(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"un_watch_trades not implemented for {self.name}")

    async def un_watch_trades_for_symbols(self, symbols, params=None) -> dict:
        raise NotImplementedError(f"un_watch_trades_for_symbols not implemented for {self.name}")

    async def watch_balance(self, params=None) -> dict:
        raise NotImplementedError(f"watch_balance not implemented for {self.name}")

    async def watch_bids_asks(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"watch_bids_asks not implemented for {self.name}")

    async def watch_funding_rate(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"watch_funding_rate not implemented for {self.name}")

    async def watch_funding_rates(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"watch_funding_rates not implemented for {self.name}")

    async def watch_funding_rates_for_symbols(self, symbols, params=None) -> dict:
        raise NotImplementedError(f"watch_funding_rates_for_symbols not implemented for {self.name}")

    async def watch_liquidations(self, symbol, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_liquidations not implemented for {self.name}")

    async def watch_liquidations_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_liquidations_for_symbols not implemented for {self.name}")

    async def watch_mark_price(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"watch_mark_price not implemented for {self.name}")

    async def watch_mark_prices(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"watch_mark_prices not implemented for {self.name}")

    async def watch_my_liquidations(self, symbol, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_my_liquidations not implemented for {self.name}")

    async def watch_my_liquidations_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_my_liquidations_for_symbols not implemented for {self.name}")

    async def watch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_my_trades not implemented for {self.name}")

    async def watch_my_trades_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_my_trades_for_symbols not implemented for {self.name}")

    async def watch_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_ohlcv not implemented for {self.name}")

    async def watch_ohlcv_for_symbols(self, symbolsAndTimeframes, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_ohlcv_for_symbols not implemented for {self.name}")

    async def watch_order_book(self, symbol, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_order_book not implemented for {self.name}")

    async def watch_order_book_for_symbols(self, symbols, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_order_book_for_symbols not implemented for {self.name}")

    async def watch_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_orders not implemented for {self.name}")

    async def watch_orders_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_orders_for_symbols not implemented for {self.name}")

    async def watch_position(self, symbol=None, params=None) -> dict:
        raise NotImplementedError(f"watch_position not implemented for {self.name}")

    async def watch_position_for_symbols(self, symbols=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_position_for_symbols not implemented for {self.name}")

    async def watch_positions(self, symbols=None, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_positions not implemented for {self.name}")

    async def watch_ticker(self, symbol, params=None) -> dict:
        raise NotImplementedError(f"watch_ticker not implemented for {self.name}")

    async def watch_tickers(self, symbols=None, params=None) -> dict:
        raise NotImplementedError(f"watch_tickers not implemented for {self.name}")

    async def watch_trades(self, symbol, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_trades not implemented for {self.name}")

    async def watch_trades_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        raise NotImplementedError(f"watch_trades_for_symbols not implemented for {self.name}")

    async def withdraw(self, code, amount, address, tag=None, params=None) -> dict:
        raise NotImplementedError(f"withdraw not implemented for {self.name}")

    async def withdraw_ws(self, code, amount, address, tag=None, params=None) -> dict:
        raise NotImplementedError(f"withdraw_ws not implemented for {self.name}")

