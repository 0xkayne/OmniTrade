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

        # Binance demo trading: uses demo-api.binance.com (not api.binance.com).
        # ccxt's enable_demo_trading() swaps urls.api → urls.demo automatically.
        if self.name == "binance" and self.network_type == NetworkType.TESTNET:
            try:
                self.ccxt_exchange.enable_demo_trading(True)
                self.logger.debug("Binance demo trading 已启用")
            except Exception as exc:
                self.logger.warning(f"Binance demo trading 启用失败: {exc}")

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
        """Return Instrument objects for all active spot/perp markets on this exchange."""
        from src.market.asset import Asset
        from src.market.instrument import Instrument

        instruments: list = []
        if not self.ccxt_exchange or not getattr(self.ccxt_exchange, "markets", None):
            return instruments

        taker_fee = self.fees.get("taker", 0.0)
        maker_fee = self.fees.get("maker", 0.0)

        for symbol, market in self.ccxt_exchange.markets.items():
            try:
                if not market.get("active"):
                    continue

                ccxt_type = market.get("type", "")
                if ccxt_type == "swap":
                    market_type = "perp"
                elif ccxt_type == "spot":
                    market_type = "spot"
                else:
                    continue

                limits = market.get("limits", {}) or {}
                amount_limits = limits.get("amount", {}) or {}
                precision = market.get("precision", {}) or {}

                min_qty = float(amount_limits.get("min") or 0)
                qty_step = float(precision.get("amount") or 0)
                price_step = float(precision.get("price") or 0)

                base = Asset(str(market["base"]))
                quote = Asset(str(market["quote"]))

                instrument = Instrument(
                    venue=self.name,
                    market_type=market_type,  # type: ignore[arg-type]
                    base=base,
                    quote=quote,
                    venue_symbol=str(market["symbol"]),
                    min_qty=min_qty,
                    qty_step=qty_step,
                    price_step=price_step,
                    taker_fee_rate=taker_fee,
                    maker_fee_rate=maker_fee,
                    listing_status="trading",
                )
                instruments.append(instrument)
            except Exception:
                self.logger.debug(f"Failed to convert market '{symbol}': skipped", exc_info=True)

        return instruments

    async def close(self):
        if self.ccxt_exchange:
            try:
                await self.ccxt_exchange.close()
            except Exception as exc:
                self.logger.warning(f"{self.name} 关闭CCXT实例时出错: {exc}")
        await super().close()

    # ── Auto-generated ccxt wrappers ──────────────────────────

    async def add_margin(self, symbol, amount, params=None) -> dict:
        return await self.ccxt_exchange.add_margin(symbol, amount, params=params)

    async def borrow_cross_margin(self, code, amount, params=None) -> dict:
        return await self.ccxt_exchange.borrow_cross_margin(code, amount, params=params)

    async def borrow_isolated_margin(self, symbol, code, amount, params=None) -> dict:
        return await self.ccxt_exchange.borrow_isolated_margin(symbol, code, amount, params=params)

    async def borrow_margin(self, code, amount, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.borrow_margin(code, amount, symbol, params=params)

    async def cancel_all_contract_orders(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_all_contract_orders(symbol, params=params)

    async def cancel_all_orders(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_all_orders(symbol, params=params)

    async def cancel_all_orders_after(self, timeout, params=None) -> dict:
        return await self.ccxt_exchange.cancel_all_orders_after(timeout, params=params)

    async def cancel_all_orders_ws(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_all_orders_ws(symbol, params=params)

    async def cancel_all_spot_orders(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_all_spot_orders(symbol, params=params)

    async def cancel_contract_order(self, id, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_contract_order(id, symbol, params=params)

    async def cancel_order_with_client_order_id(self, clientOrderId, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_order_with_client_order_id(clientOrderId, symbol, params=params)

    async def cancel_order_ws(self, id, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_order_ws(id, symbol, params=params)

    async def cancel_orders(self, ids, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_orders(ids, symbol, params=params)

    async def cancel_orders_for_symbols(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.cancel_orders_for_symbols(orders, params=params)

    async def cancel_orders_with_client_order_ids(self, clientOrderIds, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_orders_with_client_order_ids(clientOrderIds, symbol, params=params)

    async def cancel_orders_ws(self, ids, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_orders_ws(ids, symbol, params=params)

    async def cancel_spot_order(self, id, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_spot_order(id, symbol, params=params)

    async def cancel_unified_order(self, order, params=None) -> dict:
        return await self.ccxt_exchange.cancel_unified_order(order, params=params)

    async def close_all_positions(self, params=None) -> dict:
        return await self.ccxt_exchange.close_all_positions(params=params)

    async def close_position(self, symbol, side=None, params=None) -> dict:
        return await self.ccxt_exchange.close_position(symbol, side, params=params)

    async def close_proxy_sessions(self, ) -> dict:
        return await self.ccxt_exchange.close_proxy_sessions()

    async def create_contract_orders(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.create_contract_orders(orders, params=params)

    async def create_convert_trade(self, id, fromCode, toCode, amount=None, params=None) -> dict:
        return await self.ccxt_exchange.create_convert_trade(id, fromCode, toCode, amount, params=params)

    async def create_deposit_address(self, code, params=None) -> dict:
        return await self.ccxt_exchange.create_deposit_address(code, params=params)

    async def create_limit_buy_order(self, symbol, amount, price, params=None) -> dict:
        return await self.ccxt_exchange.create_limit_buy_order(symbol, amount, price, params=params)

    async def create_limit_buy_order_ws(self, symbol, amount, price, params=None) -> dict:
        return await self.ccxt_exchange.create_limit_buy_order_ws(symbol, amount, price, params=params)

    async def create_limit_order(self, symbol, side, amount, price, params=None) -> dict:
        return await self.ccxt_exchange.create_limit_order(symbol, side, amount, price, params=params)

    async def create_limit_order_ws(self, symbol, side, amount, price, params=None) -> dict:
        return await self.ccxt_exchange.create_limit_order_ws(symbol, side, amount, price, params=params)

    async def create_limit_sell_order(self, symbol, amount, price, params=None) -> dict:
        return await self.ccxt_exchange.create_limit_sell_order(symbol, amount, price, params=params)

    async def create_limit_sell_order_ws(self, symbol, amount, price, params=None) -> dict:
        return await self.ccxt_exchange.create_limit_sell_order_ws(symbol, amount, price, params=params)

    async def create_market_buy_order(self, symbol, amount, params=None) -> dict:
        return await self.ccxt_exchange.create_market_buy_order(symbol, amount, params=params)

    async def create_market_buy_order_with_cost(self, symbol, cost, params=None) -> dict:
        return await self.ccxt_exchange.create_market_buy_order_with_cost(symbol, cost, params=params)

    async def create_market_buy_order_ws(self, symbol, amount, params=None) -> dict:
        return await self.ccxt_exchange.create_market_buy_order_ws(symbol, amount, params=params)

    async def create_market_order(self, symbol, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.create_market_order(symbol, side, amount, price, params=params)

    async def create_market_order_with_cost(self, symbol, side, cost, params=None) -> dict:
        return await self.ccxt_exchange.create_market_order_with_cost(symbol, side, cost, params=params)

    async def create_market_order_with_cost_ws(self, symbol, side, cost, params=None) -> dict:
        return await self.ccxt_exchange.create_market_order_with_cost_ws(symbol, side, cost, params=params)

    async def create_market_order_ws(self, symbol, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.create_market_order_ws(symbol, side, amount, price, params=params)

    async def create_market_sell_order(self, symbol, amount, params=None) -> dict:
        return await self.ccxt_exchange.create_market_sell_order(symbol, amount, params=params)

    async def create_market_sell_order_with_cost(self, symbol, cost, params=None) -> dict:
        return await self.ccxt_exchange.create_market_sell_order_with_cost(symbol, cost, params=params)

    async def create_market_sell_order_ws(self, symbol, amount, params=None) -> dict:
        return await self.ccxt_exchange.create_market_sell_order_ws(symbol, amount, params=params)

    async def create_order_with_take_profit_and_stop_loss(self, symbol, type, side, amount, price=None, takeProfit=None, stopLoss=None, params=None) -> dict:
        return await self.ccxt_exchange.create_order_with_take_profit_and_stop_loss(symbol, type, side, amount, price, takeProfit, stopLoss, params=params)

    async def create_order_with_take_profit_and_stop_loss_ws(self, symbol, type, side, amount, price=None, takeProfit=None, stopLoss=None, params=None) -> dict:
        return await self.ccxt_exchange.create_order_with_take_profit_and_stop_loss_ws(symbol, type, side, amount, price, takeProfit, stopLoss, params=params)

    async def create_order_ws(self, symbol, type, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.create_order_ws(symbol, type, side, amount, price, params=params)

    async def create_orders(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.create_orders(orders, params=params)

    async def create_orders_ws(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.create_orders_ws(orders, params=params)

    async def create_post_only_order(self, symbol, type, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.create_post_only_order(symbol, type, side, amount, price, params=params)

    async def create_post_only_order_ws(self, symbol, type, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.create_post_only_order_ws(symbol, type, side, amount, price, params=params)

    async def create_reduce_only_order(self, symbol, type, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.create_reduce_only_order(symbol, type, side, amount, price, params=params)

    async def create_reduce_only_order_ws(self, symbol, type, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.create_reduce_only_order_ws(symbol, type, side, amount, price, params=params)

    async def create_spot_orders(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.create_spot_orders(orders, params=params)

    async def create_stop_limit_order(self, symbol, side, amount, price, triggerPrice, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_limit_order(symbol, side, amount, price, triggerPrice, params=params)

    async def create_stop_limit_order_ws(self, symbol, side, amount, price, triggerPrice, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_limit_order_ws(symbol, side, amount, price, triggerPrice, params=params)

    async def create_stop_loss_order(self, symbol, type, side, amount, price=None, stopLossPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_loss_order(symbol, type, side, amount, price, stopLossPrice, params=params)

    async def create_stop_loss_order_ws(self, symbol, type, side, amount, price=None, stopLossPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_loss_order_ws(symbol, type, side, amount, price, stopLossPrice, params=params)

    async def create_stop_market_order(self, symbol, side, amount, triggerPrice, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_market_order(symbol, side, amount, triggerPrice, params=params)

    async def create_stop_market_order_ws(self, symbol, side, amount, triggerPrice, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_market_order_ws(symbol, side, amount, triggerPrice, params=params)

    async def create_stop_order(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_order(symbol, type, side, amount, price, triggerPrice, params=params)

    async def create_stop_order_ws(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_order_ws(symbol, type, side, amount, price, triggerPrice, params=params)

    async def create_sub_account(self, name, params=None) -> dict:
        return await self.ccxt_exchange.create_sub_account(name, params=params)

    async def create_take_profit_order(self, symbol, type, side, amount, price=None, takeProfitPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_take_profit_order(symbol, type, side, amount, price, takeProfitPrice, params=params)

    async def create_take_profit_order_ws(self, symbol, type, side, amount, price=None, takeProfitPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_take_profit_order_ws(symbol, type, side, amount, price, takeProfitPrice, params=params)

    async def create_trailing_amount_order(self, symbol, type, side, amount, price=None, trailingAmount=None, trailingTriggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_trailing_amount_order(symbol, type, side, amount, price, trailingAmount, trailingTriggerPrice, params=params)

    async def create_trailing_amount_order_ws(self, symbol, type, side, amount, price=None, trailingAmount=None, trailingTriggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_trailing_amount_order_ws(symbol, type, side, amount, price, trailingAmount, trailingTriggerPrice, params=params)

    async def create_trailing_percent_order(self, symbol, type, side, amount, price=None, trailingPercent=None, trailingTriggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_trailing_percent_order(symbol, type, side, amount, price, trailingPercent, trailingTriggerPrice, params=params)

    async def create_trailing_percent_order_ws(self, symbol, type, side, amount, price=None, trailingPercent=None, trailingTriggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_trailing_percent_order_ws(symbol, type, side, amount, price, trailingPercent, trailingTriggerPrice, params=params)

    async def create_trigger_order(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_trigger_order(symbol, type, side, amount, price, triggerPrice, params=params)

    async def create_trigger_order_ws(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_trigger_order_ws(symbol, type, side, amount, price, triggerPrice, params=params)

    async def create_twap_order(self, symbol, side, amount, duration, params=None) -> dict:
        return await self.ccxt_exchange.create_twap_order(symbol, side, amount, duration, params=params)

    async def edit_limit_buy_order(self, id, symbol, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.edit_limit_buy_order(id, symbol, amount, price, params=params)

    async def edit_limit_order(self, id, symbol, side, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.edit_limit_order(id, symbol, side, amount, price, params=params)

    async def edit_limit_sell_order(self, id, symbol, amount, price=None, params=None) -> dict:
        return await self.ccxt_exchange.edit_limit_sell_order(id, symbol, amount, price, params=params)

    async def edit_order(self, id, symbol, type, side, amount=None, price=None, params=None) -> dict:
        return await self.ccxt_exchange.edit_order(id, symbol, type, side, amount, price, params=params)

    async def edit_order_with_client_order_id(self, clientOrderId, symbol, type, side, amount=None, price=None, params=None) -> dict:
        return await self.ccxt_exchange.edit_order_with_client_order_id(clientOrderId, symbol, type, side, amount, price, params=params)

    async def edit_order_ws(self, id, symbol, type, side, amount=None, price=None, params=None) -> dict:
        return await self.ccxt_exchange.edit_order_ws(id, symbol, type, side, amount, price, params=params)

    async def edit_orders(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.edit_orders(orders, params=params)

    async def fetch_accounts(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_accounts(params=params)

    async def fetch_adl_rank(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_adl_rank(symbol, params=params)

    async def fetch_all_greeks(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_all_greeks(symbols, params=params)

    async def fetch_bids_asks(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_bids_asks(symbols, params=params)

    async def fetch_borrow_interest(self, code=None, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_borrow_interest(code, symbol, since, limit, params=params)

    async def fetch_borrow_rate(self, code, amount, params=None) -> dict:
        return await self.ccxt_exchange.fetch_borrow_rate(code, amount, params=params)

    async def fetch_canceled_and_closed_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_canceled_and_closed_orders(symbol, since, limit, params=params)

    async def fetch_canceled_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_canceled_orders(symbol, since, limit, params=params)

    async def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_closed_orders(symbol, since, limit, params=params)

    async def fetch_contract_deposit_address(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_contract_deposit_address(code, params=params)

    async def fetch_contract_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_contract_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_contract_tickers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_contract_tickers(symbols, params=params)

    async def fetch_convert_currencies(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_convert_currencies(params=params)

    async def fetch_convert_quote(self, fromCode, toCode, amount=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_convert_quote(fromCode, toCode, amount, params=params)

    async def fetch_convert_trade(self, id, code=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_convert_trade(id, code, params=params)

    async def fetch_convert_trade_history(self, code=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_convert_trade_history(code, since, limit, params=params)

    async def fetch_cross_borrow_rate(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_cross_borrow_rate(code, params=params)

    async def fetch_cross_borrow_rates(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_cross_borrow_rates(params=params)

    async def fetch_currencies(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_currencies(params=params)

    async def fetch_deposit_address(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_deposit_address(code, params=params)

    async def fetch_deposit_addresses(self, codes=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_deposit_addresses(codes, params=params)

    async def fetch_deposit_addresses_by_network(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_deposit_addresses_by_network(code, params=params)

    async def fetch_deposit_withdraw_fee(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_deposit_withdraw_fee(code, params=params)

    async def fetch_deposit_withdraw_fees(self, codes=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_deposit_withdraw_fees(codes, params=params)

    async def fetch_deposits(self, code=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_deposits(code, since, limit, params=params)

    async def fetch_deposits_withdrawals(self, code=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_deposits_withdrawals(code, since, limit, params=params)

    async def fetch_free_balance(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_free_balance(params=params)

    async def fetch_funding_history(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_history(symbol, since, limit, params=params)

    async def fetch_funding_interval(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_interval(symbol, params=params)

    async def fetch_funding_intervals(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_intervals(symbols, params=params)

    async def fetch_funding_rate(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_rate(symbol, params=params)

    async def fetch_funding_rate_history(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_rate_history(symbol, since, limit, params=params)

    async def fetch_funding_rates(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_rates(symbols, params=params)

    async def fetch_greeks(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_greeks(symbol, params=params)

    async def fetch_index_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_index_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_isolated_borrow_rate(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_isolated_borrow_rate(symbol, params=params)

    async def fetch_isolated_borrow_rates(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_isolated_borrow_rates(params=params)

    async def fetch_l2_order_book(self, symbol, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_l2_order_book(symbol, limit, params=params)

    async def fetch_l3_order_book(self, symbol, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_l3_order_book(symbol, limit, params=params)

    async def fetch_last_prices(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_last_prices(symbols, params=params)

    async def fetch_ledger(self, code=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_ledger(code, since, limit, params=params)

    async def fetch_ledger_entry(self, id, code=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_ledger_entry(id, code, params=params)

    async def fetch_leverage(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_leverage(symbol, params=params)

    async def fetch_leverage_tiers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_leverage_tiers(symbols, params=params)

    async def fetch_leverages(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_leverages(symbols, params=params)

    async def fetch_liquidations(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_liquidations(symbol, since, limit, params=params)

    async def fetch_long_short_ratio(self, symbol, timeframe=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_long_short_ratio(symbol, timeframe, params=params)

    async def fetch_long_short_ratio_history(self, symbol=None, timeframe=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_long_short_ratio_history(symbol, timeframe, since, limit, params=params)

    async def fetch_margin_adjustment_history(self, symbol=None, type=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_margin_adjustment_history(symbol, type, since, limit, params=params)

    async def fetch_margin_mode(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_margin_mode(symbol, params=params)

    async def fetch_margin_modes(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_margin_modes(symbols, params=params)

    async def fetch_mark_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_mark_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_mark_price(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_mark_price(symbol, params=params)

    async def fetch_mark_prices(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_mark_prices(symbols, params=params)

    async def fetch_market_leverage_tiers(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_market_leverage_tiers(symbol, params=params)

    async def fetch_markets(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_markets(params=params)

    async def fetch_my_liquidations(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_my_liquidations(symbol, since, limit, params=params)

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_my_trades(symbol, since, limit, params=params)

    async def fetch_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_ohlcv_ws(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_ohlcv_ws(symbol, timeframe, since, limit, params=params)

    async def fetch_open_interest(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_open_interest(symbol, params=params)

    async def fetch_open_interest_history(self, symbol, timeframe='1h', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_open_interest_history(symbol, timeframe, since, limit, params=params)

    async def fetch_open_interests(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_open_interests(symbols, params=params)

    async def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_open_orders(symbol, since, limit, params=params)

    async def fetch_option(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_option(symbol, params=params)

    async def fetch_option_chain(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_option_chain(code, params=params)

    async def fetch_order_book(self, symbol, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_order_book(symbol, limit, params=params)

    async def fetch_order_books(self, symbols=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_order_books(symbols, limit, params=params)

    async def fetch_order_status(self, id, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_order_status(id, symbol, params=params)

    async def fetch_order_trades(self, id, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_order_trades(id, symbol, since, limit, params=params)

    async def fetch_order_with_client_order_id(self, clientOrderId, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_order_with_client_order_id(clientOrderId, symbol, params=params)

    async def fetch_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_orders(symbol, since, limit, params=params)

    async def fetch_partial_balance(self, part, params=None) -> dict:
        return await self.ccxt_exchange.fetch_partial_balance(part, params=params)

    async def fetch_payment_methods(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_payment_methods(params=params)

    async def fetch_position(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_position(symbol, params=params)

    async def fetch_position_adl_rank(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_position_adl_rank(symbol, params=params)

    async def fetch_position_history(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_position_history(symbol, since, limit, params=params)

    async def fetch_position_mode(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_position_mode(symbol, params=params)

    async def fetch_positions(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_positions(symbols, params=params)

    async def fetch_positions_adl_rank(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_positions_adl_rank(symbols, params=params)

    async def fetch_positions_for_symbol(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_positions_for_symbol(symbol, params=params)

    async def fetch_positions_for_symbol_ws(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_positions_for_symbol_ws(symbol, params=params)

    async def fetch_positions_history(self, symbols=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_positions_history(symbols, since, limit, params=params)

    async def fetch_positions_risk(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_positions_risk(symbols, params=params)

    async def fetch_premium_index_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_premium_index_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_spot_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_spot_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_spot_tickers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_spot_tickers(symbols, params=params)

    async def fetch_status(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_status(params=params)

    async def fetch_ticker(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_ticker(symbol, params=params)

    async def fetch_tickers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_tickers(symbols, params=params)

    async def fetch_time(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_time(params=params)

    async def fetch_total_balance(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_total_balance(params=params)

    async def fetch_trades(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trades(symbol, since, limit, params=params)

    async def fetch_trades_ws(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trades_ws(symbol, since, limit, params=params)

    async def fetch_trading_fee(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trading_fee(symbol, params=params)

    async def fetch_trading_fees(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trading_fees(params=params)

    async def fetch_trading_limits(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trading_limits(symbols, params=params)

    async def fetch_transaction_fee(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_transaction_fee(code, params=params)

    async def fetch_transaction_fees(self, codes=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_transaction_fees(codes, params=params)

    async def fetch_transactions(self, code=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_transactions(code, since, limit, params=params)

    async def fetch_transfer(self, id, code=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_transfer(id, code, params=params)

    async def fetch_transfers(self, code=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_transfers(code, since, limit, params=params)

    async def fetch_unified_order(self, order, params=None) -> dict:
        return await self.ccxt_exchange.fetch_unified_order(order, params=params)

    async def fetch_used_balance(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_used_balance(params=params)

    async def fetch_withdrawals(self, code=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_withdrawals(code, since, limit, params=params)

    async def is_uta_enabled(self, params=None) -> dict:
        return await self.ccxt_exchange.is_uta_enabled(params=params)

    async def load_accounts(self, reload=False, params=None) -> dict:
        return await self.ccxt_exchange.load_accounts(reload, params=params)

    async def load_fees(self, reload=False) -> dict:
        return await self.ccxt_exchange.load_fees(reload)

    async def load_markets(self, reload=False, params=None) -> dict:
        return await self.ccxt_exchange.load_markets(reload, params=params)

    async def load_time_difference(self, params=None) -> dict:
        return await self.ccxt_exchange.load_time_difference(params=params)

    async def load_trading_limits(self, symbols=None, reload=False, params=None) -> dict:
        return await self.ccxt_exchange.load_trading_limits(symbols, reload, params=params)

    async def reduce_margin(self, symbol, amount, params=None) -> dict:
        return await self.ccxt_exchange.reduce_margin(symbol, amount, params=params)

    async def repay_cross_margin(self, code, amount, params=None) -> dict:
        return await self.ccxt_exchange.repay_cross_margin(code, amount, params=params)

    async def repay_isolated_margin(self, symbol, code, amount, params=None) -> dict:
        return await self.ccxt_exchange.repay_isolated_margin(symbol, code, amount, params=params)

    async def repay_margin(self, code, amount, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.repay_margin(code, amount, symbol, params=params)

    async def set_leverage(self, leverage, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.set_leverage(leverage, symbol, params=params)

    async def set_margin(self, symbol, amount, params=None) -> dict:
        return await self.ccxt_exchange.set_margin(symbol, amount, params=params)

    async def set_margin_mode(self, marginMode, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.set_margin_mode(marginMode, symbol, params=params)

    async def set_position_mode(self, hedged, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.set_position_mode(hedged, symbol, params=params)

    async def sign_in(self, params=None) -> dict:
        return await self.ccxt_exchange.sign_in(params=params)

    async def transfer(self, code, amount, fromAccount, toAccount, params=None) -> dict:
        return await self.ccxt_exchange.transfer(code, amount, fromAccount, toAccount, params=params)

    async def un_watch_bids_asks(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_bids_asks(symbols, params=params)

    async def un_watch_funding_rate(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_funding_rate(symbol, params=params)

    async def un_watch_funding_rates(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_funding_rates(symbols, params=params)

    async def un_watch_mark_price(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_mark_price(symbol, params=params)

    async def un_watch_mark_prices(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_mark_prices(symbols, params=params)

    async def un_watch_my_trades(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_my_trades(symbol, params=params)

    async def un_watch_ohlcv(self, symbol, timeframe='1m', params=None) -> dict:
        return await self.ccxt_exchange.un_watch_ohlcv(symbol, timeframe, params=params)

    async def un_watch_ohlcv_for_symbols(self, symbolsAndTimeframes, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_ohlcv_for_symbols(symbolsAndTimeframes, params=params)

    async def un_watch_order_book(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_order_book(symbol, params=params)

    async def un_watch_order_book_for_symbols(self, symbols, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_order_book_for_symbols(symbols, params=params)

    async def un_watch_orders(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_orders(symbol, params=params)

    async def un_watch_positions(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_positions(symbols, params=params)

    async def un_watch_ticker(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_ticker(symbol, params=params)

    async def un_watch_tickers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_tickers(symbols, params=params)

    async def un_watch_trades(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_trades(symbol, params=params)

    async def un_watch_trades_for_symbols(self, symbols, params=None) -> dict:
        return await self.ccxt_exchange.un_watch_trades_for_symbols(symbols, params=params)

    async def watch_balance(self, params=None) -> dict:
        return await self.ccxt_exchange.watch_balance(params=params)

    async def watch_bids_asks(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_bids_asks(symbols, params=params)

    async def watch_funding_rate(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.watch_funding_rate(symbol, params=params)

    async def watch_funding_rates(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_funding_rates(symbols, params=params)

    async def watch_funding_rates_for_symbols(self, symbols, params=None) -> dict:
        return await self.ccxt_exchange.watch_funding_rates_for_symbols(symbols, params=params)

    async def watch_liquidations(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_liquidations(symbol, since, limit, params=params)

    async def watch_liquidations_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_liquidations_for_symbols(symbols, since, limit, params=params)

    async def watch_mark_price(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.watch_mark_price(symbol, params=params)

    async def watch_mark_prices(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_mark_prices(symbols, params=params)

    async def watch_my_liquidations(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_my_liquidations(symbol, since, limit, params=params)

    async def watch_my_liquidations_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_my_liquidations_for_symbols(symbols, since, limit, params=params)

    async def watch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_my_trades(symbol, since, limit, params=params)

    async def watch_my_trades_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_my_trades_for_symbols(symbols, since, limit, params=params)

    async def watch_ohlcv(self, symbol, timeframe='1m', since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_ohlcv(symbol, timeframe, since, limit, params=params)

    async def watch_ohlcv_for_symbols(self, symbolsAndTimeframes, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_ohlcv_for_symbols(symbolsAndTimeframes, since, limit, params=params)

    async def watch_order_book(self, symbol, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_order_book(symbol, limit, params=params)

    async def watch_order_book_for_symbols(self, symbols, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_order_book_for_symbols(symbols, limit, params=params)

    async def watch_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_orders(symbol, since, limit, params=params)

    async def watch_orders_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_orders_for_symbols(symbols, since, limit, params=params)

    async def watch_position(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_position(symbol, params=params)

    async def watch_position_for_symbols(self, symbols=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_position_for_symbols(symbols, since, limit, params=params)

    async def watch_positions(self, symbols=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_positions(symbols, since, limit, params=params)

    async def watch_ticker(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.watch_ticker(symbol, params=params)

    async def watch_tickers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_tickers(symbols, params=params)

    async def watch_trades(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_trades(symbol, since, limit, params=params)

    async def watch_trades_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_trades_for_symbols(symbols, since, limit, params=params)

    async def withdraw(self, code, amount, address, tag=None, params=None) -> dict:
        return await self.ccxt_exchange.withdraw(code, amount, address, tag, params=params)

    async def withdraw_ws(self, code, amount, address, tag=None, params=None) -> dict:
        return await self.ccxt_exchange.withdraw_ws(code, amount, address, tag, params=params)

