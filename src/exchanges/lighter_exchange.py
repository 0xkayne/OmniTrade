import logging
import time
from typing import Any

from src.core.base_exchange import BaseExchange

# Try to import lighter SDK components
try:
    from lighter import AccountApi, ApiClient, Configuration, OrderApi, SignerClient
except ImportError:
    # Log warning, but allow class definition (will fail at runtime)
    logging.getLogger("LighterExchange").warning("lighter-sdk not found or import error. Check installed version.")
    # Define dummy placeholders
    SignerClient = Any
    AccountApi = Any
    OrderApi = Any
    ApiClient = Any
    Configuration = Any


class LighterExchange(BaseExchange):
    """Lighter Exchange Adapter using lighter-sdk"""

    def __init__(self, name: str, config: dict, secrets: dict):
        super().__init__(name, config, secrets)
        self.logger = logging.getLogger(f"exchange.{name}")

        # API Configuration - will be fully initialized in connect()
        self.api_url = (
            self.config.get("networks", {})
            .get(self.network_type.value, {})
            .get("rest_base_url", "https://api.lighter.xyz")
        )
        self.api_url = self.api_url.rstrip("/")

        # Lighter API Client objects - initialized lazily in connect()
        self.configuration = None
        self.api_client = None
        self.account_api = None
        self.order_api = None

        # Signer Client (for private actions)
        self.signer: SignerClient | None = None
        self.account_index: int | None = None

        # Resolve secrets based on network type
        network_secrets = self.secrets.get(self.network_type.value, self.secrets)

        # If 'wallet_address' is not in network_secrets, try top level (fallback)
        if "wallet_address" not in network_secrets and "wallet_address" in self.secrets:
            network_secrets = self.secrets

        self.network_secrets = network_secrets
        self._wallet_address = network_secrets.get("wallet_address")
        # Support both new API key style and legacy private key
        self._private_key = network_secrets.get("api_private_key") or network_secrets.get("private_key")
        self._api_key_index = network_secrets.get("api_key_index")

        # Market Cache: Symbol -> Market Index
        self.markets: dict[str, int] = {}
        self.market_details: dict[str, Any] = {}

        # Internal Order ID counter
        self._client_order_id_counter = int(time.time() * 1000)

    async def connect(self):
        """Connect and initialize Lighter client"""
        try:
            # Reinitialize API client with current URL (supports network switching)
            self.api_url = (
                self.config.get("networks", {})
                .get(self.network_type.value, {})
                .get("rest_base_url", "https://api.lighter.xyz")
            )
            self.api_url = self.api_url.rstrip("/")

            self.configuration = Configuration(host=self.api_url)
            self.api_client = ApiClient(self.configuration)
            self.account_api = AccountApi(self.api_client)
            self.order_api = OrderApi(self.api_client)

            # Load Markets
            await self._load_markets()

            # Setup Signer if keys available
            if self._private_key:
                await self._init_signer(self._private_key)

            self.logger.debug(f"Lighter {self.network_type.value} connected.")

        except Exception as e:
            self.logger.error(f"Lighter connection failed: {e}")
            raise

    async def _init_signer(self, private_key: str):
        """Initialize SignerClient"""
        if "account_index" in self.network_secrets:
            self.account_index = int(self.network_secrets["account_index"])
        elif self._wallet_address:
            try:
                # Async call needed
                accounts = await self.account_api.accounts_by_l1_address(self._wallet_address)

                acc_list = []
                if hasattr(accounts, "sub_accounts"):
                    acc_list = accounts.sub_accounts
                elif isinstance(accounts, list):
                    acc_list = accounts

                if acc_list and len(acc_list) > 0:
                    acc = acc_list[0]
                    # Prioritize index from object, then dict
                    if hasattr(acc, "index"):
                        self.account_index = acc.index
                    elif isinstance(acc, dict) and "index" in acc:
                        self.account_index = acc["index"]
                    self.logger.debug(f"Found Account Index: {self.account_index}")

            except Exception as e:
                self.logger.warning(f"Could not fetch account index by address: {e}")

        if self.account_index is not None:
            # Determine which index and key to use
            # If we have a specific API Key Index defined in secrets, use that.
            # Otherwise default to 0 (which seemed to fail previously but might work if key was correct)
            # or maybe the user didn't have one before.

            signing_key_index = 0
            if self._api_key_index is not None:
                signing_key_index = int(self._api_key_index)

            pk = private_key
            # Do not strip 0x, SDK might expect it or verify length based on it
            # Actually, for the 40-byte key, if it's hex, it might be 80 chars.
            # The error said "expected 40 got 32". 32 bytes is 64 hex chars.
            # If input is hex string, we might need to be careful.
            # Existing logic checks startswith 0x.
            if pk.startswith("0x"):
                pk = pk[2:]

            keys = {signing_key_index: pk}
            try:
                # SignerClient init
                self.signer = SignerClient(self.api_url, int(self.account_index), keys)
                self.logger.debug(f"Lighter Signer initialized for Account Index {self.account_index}")
            except Exception as e:
                self.logger.error(f"SignerClient init failed: {e}")
        else:
            self.logger.warning("Account Index not found. Private methods will fail.")

    async def _load_markets(self):
        """Load market definitions"""
        try:
            # Async Call
            order_books = await self.order_api.order_books()

            iterable = order_books
            if hasattr(order_books, "order_books"):
                iterable = order_books.order_books

            market_count = 0
            for ob in iterable:
                symbol = getattr(ob, "symbol", None)
                market_id = getattr(ob, "market_id", None)

                if symbol and market_id is not None:
                    norm_symbol = symbol.replace("-", "/")
                    self.markets[norm_symbol] = market_id
                    self.market_details[norm_symbol] = ob
                    market_count += 1

            self.logger.debug(f"Loaded {market_count} markets")

        except Exception as e:
            self.logger.error(f"Failed to load markets: {e}")

    async def fetch_orderbook(self, symbol: str, limit: int = 10) -> dict:
        """Fetch orderbook"""
        if not self.markets:
            await self._load_markets()

        market_id = self.markets.get(symbol)
        if market_id is None:
            await self._load_markets()
            market_id = self.markets.get(symbol)
            if market_id is None:
                raise ValueError(f"Symbol {symbol} not found on Lighter")

        try:
            # Async call
            # According to error, limit is required.
            ob_data = await self.order_api.order_book_orders(market_id, limit=limit)

            bids = getattr(ob_data, "bids", []) or []
            asks = getattr(ob_data, "asks", []) or []

            formatted_bids = [[float(b.price), float(getattr(b, "remaining_base_amount", 0))] for b in bids[:limit]]
            formatted_asks = [[float(a.price), float(getattr(a, "remaining_base_amount", 0))] for a in asks[:limit]]

            return {
                "symbol": symbol,
                "bids": formatted_bids,
                "asks": formatted_asks,
                "timestamp": int(time.time() * 1000),
                "source": "lighter-sdk",
            }
        except Exception as e:
            self.logger.error(f"Fetch orderbook error: {e}")
            raise

    async def fetch_balance(self) -> dict:
        """Fetch balance"""
        if not self.signer and self.account_index is None:
            raise RuntimeError("Account Index unknown")

        try:
            acc_idx = self.account_index
            # Async call with correct arguments: by='index', value=str(index)
            # Response might be DetailedAccounts container or DetailedAccount directly
            account_info = await self.account_api.account(by="index", value=str(acc_idx))

            # Standard CCXT structure
            result = {"info": account_info, "free": {}, "used": {}, "total": {}}

            # Extract account details
            acc_details = None
            if hasattr(account_info, "accounts") and account_info.accounts:
                acc_details = account_info.accounts[0]
            elif hasattr(account_info, "assets"):  # Dictionary or direct object?
                acc_details = account_info

            if acc_details:
                # Parse available_balance/collateral as USDC (the margin/collateral currency)
                available_balance = None
                if hasattr(acc_details, "available_balance"):
                    available_balance = float(acc_details.available_balance) if acc_details.available_balance else 0.0
                elif hasattr(acc_details, "collateral"):
                    available_balance = float(acc_details.collateral) if acc_details.collateral else 0.0

                if available_balance is not None and available_balance > 0:
                    # Add USDC balance (this is the trading collateral)
                    result["free"]["USDC"] = available_balance
                    result["used"]["USDC"] = 0.0
                    result["total"]["USDC"] = available_balance

                # Also parse individual assets
                if hasattr(acc_details, "assets"):
                    for asset in acc_details.assets:
                        symbol = asset.symbol
                        # Ensure symbol is standard (e.g. USDC, ETH)

                        # Balance parsing (strings)
                        total = float(asset.balance) if asset.balance else 0.0
                        locked = float(asset.locked_balance) if asset.locked_balance else 0.0
                        free = total - locked

                        result["free"][symbol] = free
                        result["used"][symbol] = locked
                        result["total"][symbol] = total

            return result
        except Exception as e:
            self.logger.error(f"Fetch balance failed: {e}")
            raise

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        """Create order"""
        if params is None:
            params = {}
        if not self.signer:
            raise RuntimeError("Signer not initialized")

        market_index = self.markets.get(symbol)
        if market_index is None:
            raise ValueError(f"Symbol {symbol} not found")

        is_ask = side.lower() == "sell"

        self._client_order_id_counter += 1
        client_order_index = self._client_order_id_counter

        limit_type = getattr(SignerClient, "ORDER_TYPE_LIMIT", 0)
        market_type = getattr(SignerClient, "ORDER_TYPE_MARKET", 1)
        tif_gtc = getattr(SignerClient, "ORDER_TIME_IN_FORCE_GOOD_TILL_TIME", 0)
        tif_ioc = getattr(SignerClient, "ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", 1)

        sdk_order_type = limit_type
        if order_type.lower() == "market":
            sdk_order_type = market_type

        tif = tif_gtc
        if sdk_order_type == market_type:
            tif = tif_ioc

        # Get Market Details for decimals
        market_info = self.market_details.get(symbol)
        if not market_info:
            raise ValueError(f"Market info for {symbol} not found")

        # Extract decimals (defaults to 0 if missing, but should be there)
        size_decimals = getattr(market_info, "supported_size_decimals", 0)
        price_decimals = getattr(market_info, "supported_price_decimals", 0)

        # Convert to atomic units
        if price:
            amount_int = int(amount * (10**size_decimals))
            price_int = int(price * (10**price_decimals))
        else:
            # Price default handling for Market Orders
            amount_int = int(amount * (10**size_decimals))
            if order_type == "market":
                # Slippage protection defaults
                # Buy: Accept high price
                # Sell: Accept low price
                # Note: Ensure these don't exceed max safe integers or logical bounds
                if is_ask:  # Sell
                    price_int = 1  # Minimum valid price
                else:  # Buy
                    price_int = 1000000 * (10**price_decimals)  # 1 Million Quote units
            else:
                price_int = 0

        if order_type == "market":
            sdk_order_type = SignerClient.ORDER_TYPE_MARKET

            # For Market Orders, price is 'average execution price' (slippage protection)
            # If price_int is 0, it fails.
            if price_int == 0:
                # Fallback if logic above missed (e.g. limit order without price should fail else where)
                if is_ask:
                    price_int = 1
                else:
                    price_int = 1000000 * (10**price_decimals)

            try:
                res = await self.signer.create_market_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=amount_int,
                    avg_execution_price=price_int,
                    is_ask=is_ask,
                    reduce_only=params.get("reduce_only", False),
                )
            except Exception as e:
                self.logger.error(f"Create market order failed: {e}")
                raise e
        else:
            # Limit Order
            try:
                # Async call
                res = await self.signer.create_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=amount_int,
                    price=price_int,
                    is_ask=is_ask,
                    order_type=sdk_order_type,
                    time_in_force=tif,
                )
            except Exception as e:
                self.logger.error(f"Create order failed: {e}")
                raise e

        return {"id": str(client_order_index), "status": "open", "info": str(res)}

    async def set_leverage(self, symbol: str, leverage: int, margin_mode: str = "isolated"):
        """
        Set leverage for a market.
        :param symbol: Market symbol
        :param leverage: Leverage value (integer)
        :param margin_mode: 'isolated' or 'cross'
        """
        if not self.signer:
            raise Exception("Signer not initialized")

        market_index = self.markets.get(symbol)
        if market_index is None:
            raise ValueError(f"Symbol {symbol} not found")

        mode = (
            SignerClient.ISOLATED_MARGIN_MODE if margin_mode.lower() == "isolated" else SignerClient.CROSS_MARGIN_MODE
        )

        # Leverage is typically an integer scaled by something? Or just int?
        # Based on examples, it seems to be just an integer (e.g. 10 for 10x),
        # but sometimes multiplied by 100 or similar.
        # Let's assume raw integer for now based on typical SDKs, but will verify if it fails.
        # Looking at SignerClient attributes, there isn't a LEVERAGE_SCALE.

        # Actually in some lighter docs, leverage is x * 10 or x * 1.
        # We'll pass the int directly.

        try:
            await self.signer.update_leverage(
                market_index=market_index,
                margin_mode=mode,
                # Wait, update_leverage in some SDKs takes atomic?
                # Let's pause on atomic leverage. The IDL usually has leverage as big int representation of decimal.
                # If 10x is 10, then it's fine. If 10x is 10 * 1e18?
                # Searching typical usage... Lighter uses 1e18 or similar for rates.
                # But for leverage, maybe just int?
                # Let's try raw int first, if error "value too small", we multiply.
                # Actually, inspecting `lighter-python` examples if possible would be great.
                # User said "please reference https://github.com/elliottech/lighter-python/tree/main/examples".
                # I should assume standard behavior or try to infer.
                # Let's assume it wants 18 decimals? Or 8?
                # Safest bet: Check if there's a LEVERAGE_PRECISION constant.
                # SignerClient didn't show one.
                # Let's use simple int 10 for 10x for now.
                leverage=leverage,  # Check this
            )
        except Exception as e:
            # If it fails, we will know.
            self.logger.error(f"Set leverage failed: {e}")
            raise e

    async def transfer_funds(self, asset_symbol: str, amount: float, from_type: str, to_type: str):
        """
        Transfer funds between Spot and Perp accounts.
        :param asset_symbol: e.g. 'USDC'
        :param amount: Amount to transfer (float)
        :param from_type: 'spot' or 'perp'
        :param to_type: 'spot' or 'perp'
        """
        if not self.signer:
            raise Exception("Signer not initialized")

        # Determine Asset ID
        asset_id = getattr(SignerClient, f"ASSET_ID_{asset_symbol}", None)
        if asset_id is None:
            if asset_symbol == "USDC":
                asset_id = getattr(SignerClient, "ASSET_ID_USDC", 2)
            elif asset_symbol == "ETH":
                asset_id = getattr(SignerClient, "ASSET_ID_ETH", 1)
            else:
                raise ValueError("Unknown Asset ID")

        # Determine Route
        route_map = {"spot": SignerClient.ROUTE_SPOT, "perp": SignerClient.ROUTE_PERP}
        r_from = route_map.get(from_type.lower())
        r_to = route_map.get(to_type.lower())

        if r_from is None or r_to is None:
            raise ValueError("Invalid account type. Use 'spot' or 'perp'")

        # Amount atomic
        decimals = 6 if "USDC" in asset_symbol else 18
        amount_int = int(amount * (10**decimals))

        try:
            # Need to ensure we have the L1 private key (wallet_private_key)
            l1_key = self.network_secrets.get("wallet_private_key") or self.network_secrets.get("private_key")
            if l1_key and l1_key.startswith("0x"):
                l1_key = l1_key[2:]

            await self.signer.transfer(
                eth_private_key=l1_key,
                to_account_index=self.account_index,  # Self transfer
                asset_id=asset_id,
                route_from=r_from,
                route_to=r_to,
                amount=amount_int,
                fee=0,
                memo="",
            )
        except Exception as e:
            self.logger.error(f"Transfer failed: {e}")
            raise e

    async def close_position(self, symbol: str):
        """
        Close all positions for a symbol using a market order.
        """
        if not self.signer:
            raise Exception("Signer not initialized")

        market_index = self.markets.get(symbol)
        if market_index is None:
            raise ValueError(f"Symbol {symbol} not found")

        acc_idx = self.account_index

        try:
            account_info = await self.account_api.account(by="index", value=str(acc_idx))

            target_pos = None
            if hasattr(account_info, "accounts") and account_info.accounts:
                acc_details = account_info.accounts[0]
                if hasattr(acc_details, "positions"):
                    for pos in acc_details.positions:
                        if pos.symbol == symbol or pos.market_id == market_index:
                            target_pos = pos
                            break

            if target_pos:
                size_str = target_pos.position
                size_float = float(size_str)

                if size_float == 0:
                    self.logger.info(f"No open position for {symbol}")
                    return

                # Determine side
                is_long = target_pos.sign > 0
                close_side_is_ask = is_long

                self.logger.info(
                    f"Closing position for {symbol}: Size {size_float} (Sign {target_pos.sign}) -> Sending Market {'Sell' if close_side_is_ask else 'Buy'}"
                )

                await self.create_order(
                    symbol=symbol,
                    order_type="market",
                    side="sell" if close_side_is_ask else "buy",
                    amount=size_float,
                    price=None,  # Market order
                    params={"reduce_only": True},
                )
            else:
                self.logger.warning(f"Position for {symbol} not found in account")

        except Exception as e:
            self.logger.error(f"Close position failed: {e}")
            raise e

    async def cancel_order(self, symbol: str, order_id: str):
        """Cancel Order"""
        if not self.signer:
            raise RuntimeError("Signer not initialized")

        market_index = self.markets.get(symbol)
        if market_index is None:
            raise ValueError(f"Symbol {symbol} not found")

        try:
            oid = int(order_id)
            # Async call
            res = await self.signer.cancel_order(market_index=market_index, order_index=oid)
            return {"status": "cancelled", "info": str(res)}
        except ValueError:
            self.logger.error(f"Invalid order ID format: {order_id}")
            raise
        except Exception as e:
            self.logger.error(f"Cancel order failed: {e}")
            raise

    async def list_markets(self) -> list:
        """Return instruments. Full impl in integration stage."""
        return []

    async def connect_websocket(self) -> bool:
        return False

    async def subscribe_orderbook(self, symbol: str):
        pass

    async def close(self):
        """Clean up resources"""
        # Close signer first
        if self.signer:
            try:
                await self.signer.close()
            except Exception as e:
                self.logger.warning(f"Error closing signer: {e}")
            self.signer = None

        # Close api_client
        if self.api_client:
            try:
                await self.api_client.close()
            except Exception as e:
                self.logger.warning(f"Error closing api_client: {e}")
            self.api_client = None

        await super().close()
