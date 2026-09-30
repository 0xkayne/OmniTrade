try:
    import ccxt.async_support as ccxt  # type: ignore

    ASYNC_CCXT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    import ccxt  # type: ignore

    ASYNC_CCXT_AVAILABLE = False
import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal
from math import isfinite
from re import fullmatch
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.exchange.base import BaseExchange
from src.exchange.order import (
    OrderAccountSnapshot,
    OrderCapabilities,
    OrderPositionSnapshot,
    OrderRequest,
    OrderSnapshot,
)
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType

# Credential values that look like the placeholder / example sentinels in
# secrets.<network>.example.yaml (e.g. "your_binance_demo_api_key"). Public market data needs
# no credentials; sending one of these as a real key makes Binance reject the
# request (-2008 Invalid Api-Key ID). Treat them as absent so oneFill can read
# public OHLCV / orderbooks anonymously.
_PLACEHOLDER_HINTS = (
    "your_",
    "your ",
    "xxx",
    "todo",
    "changeme",
    "replace",
    "example",
    "sample",
    "placeholder",
    "dummy",
    "fill_in",
    "add_your",
    "pending",
    "please",
    "****",
    "********",
)


def _is_placeholder_value(value: str | None) -> bool:
    """Return True if a credential looks like an unfilled placeholder."""
    if not value:
        return True
    v = value.lower()
    if any(hint in v for hint in _PLACEHOLDER_HINTS):
        return True
    # All-same-character sentinel, e.g. "0000..." / "aaaa..." / "xxxxx"
    return len(value) >= 4 and len(set(value)) == 1


def _instrument_from_ccxt_market(
    venue: str, network: NetworkType, market: dict, fees: dict, precision_mode=None
) -> Instrument | None:
    """Map verified CCXT contract facts without silently defaulting derivatives."""
    if not market.get("active") or market.get("type") not in {"spot", "swap"} or market.get("expiry"):
        return None
    is_contract = market["type"] == "swap"
    size = market.get("contractSize") if is_contract else 1.0
    settle = market.get("settle") if is_contract else None
    inverse = market.get("inverse") if is_contract else False
    if is_contract and (
        size is None or not isfinite(float(size)) or float(size) <= 0 or not settle or not isinstance(inverse, bool)
    ):
        return None
    limits, precision = market.get("limits") or {}, market.get("precision") or {}

    def step(name):
        value = precision.get(name)
        return float(10**-value if precision_mode == 2 else value) if value is not None else 0.0

    leverage = (limits.get("leverage") or {}).get("max")
    return Instrument(
        venue=venue,
        network=network,
        market_type="perp" if is_contract else "spot",
        base=Asset(str(market["base"])),
        quote=Asset(str(market["quote"])),
        venue_symbol=str(market["symbol"]),
        min_qty=float((limits.get("amount") or {}).get("min") or 0),
        qty_step=step("amount"),
        price_step=step("price"),
        min_notional=float((limits.get("cost") or {}).get("min") or 0),
        taker_fee_rate=float(market.get("taker") if market.get("taker") is not None else fees.get("taker", 0)),
        maker_fee_rate=float(market.get("maker") if market.get("maker") is not None else fees.get("maker", 0)),
        contract_size=float(size),
        is_inverse=bool(inverse),
        settlement_asset=Asset(str(settle)) if settle else None,
        quantity_unit="contracts" if is_contract else "base",
        max_leverage=float(leverage) if leverage else None,
    )


class CCXTExchange(BaseExchange):
    """CCXT支持的交易所统一适配器 - 增强网络支持"""

    def __init__(self, name: str, config: dict, secrets: dict):
        super().__init__(name, config, secrets)
        self.ccxt_exchange = None
        self._ws_client = None
        self._ws_client_lock = asyncio.Lock()
        self.supports_user_fills = self.name == "hyperliquid"
        if self.name == "hyperliquid":
            self._hyperliquid_credentials()

    def _hyperliquid_credentials(self) -> tuple[str | None, str | None, str | None]:
        """Validate the funded account and distinct API signer before any I/O."""
        legacy = {"walletAddress", "wallet_address", "privateKey", "private_key"} & self.secrets.keys()
        if legacy:
            raise ValueError(
                "hyperliquid: legacy credential fields are unsupported; replace "
                + ", ".join(sorted(legacy))
                + " with master_wallet_address, api_wallet_address and api_wallet_private_key"
            )

        def credential(field: str) -> str | None:
            value = self.secrets.get(field)
            if value is None or value == "":
                return None
            if not isinstance(value, str):
                raise ValueError(f"hyperliquid: {field} must be a quoted string")
            value = value.strip()
            if _is_placeholder_value(value):
                return None
            return value.lower().removeprefix("0x")

        master = credential("master_wallet_address")
        address = credential("api_wallet_address")
        private = credential("api_wallet_private_key")
        for field, value in (("master_wallet_address", master), ("api_wallet_address", address)):
            if value is not None and not fullmatch(r"[0-9a-f]{40}", value):
                raise ValueError(f"hyperliquid: {field} must be an Ethereum address (20 bytes)")
        if bool(address) != bool(private):
            raise ValueError("hyperliquid: api_wallet_address and api_wallet_private_key must be configured together")
        if private is not None:
            if master is None:
                raise ValueError("hyperliquid: master_wallet_address is required with API wallet credentials")
            try:
                if not fullmatch(r"[0-9a-f]{64}", private):
                    raise ValueError("invalid key encoding")
                public = (
                    ec.derive_private_key(int(private, 16), ec.SECP256K1())
                    .public_key()
                    .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
                )
            except ValueError:
                raise ValueError(
                    "hyperliquid: api_wallet_private_key must be a valid 32-byte secp256k1 private key"
                ) from None
            derived = ccxt.Exchange.hash(public[1:], "keccak", "hex")[-40:]
            if derived.lower() != address:
                raise ValueError("hyperliquid: api_wallet_address does not match api_wallet_private_key")
            if address == master:
                raise ValueError(
                    "hyperliquid: API wallet must differ from master_wallet_address; configure an approved API wallet"
                )
        return ("0x" + master if master else None, "0x" + address if address else None, private)

    def has_credentials(self, instrument: Instrument | None = None) -> bool:
        if self.name != "hyperliquid":
            return super().has_credentials(instrument)
        master, address, private = self._hyperliquid_credentials()
        return bool(master and address and private)

    def _sanitize_config_for_log(self, config: dict[str, Any]) -> dict[str, Any]:
        """移除敏感字段，便于日志输出调试"""
        redacted_keys = {
            "apiKey",
            "secret",
            "walletAddress",
            "privateKey",
            "vaultAddress",
            "master_wallet_address",
            "api_wallet_address",
            "api_wallet_private_key",
        }
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

        options = config.setdefault("options", {})
        options["defaultType"] = "swap"

        if self.name == "hyperliquid":
            wallet_address, _, private_key = self._hyperliquid_credentials()
            vault_address = self.secrets.get("vaultAddress") or self.secrets.get("vault_address")

            if wallet_address and not _is_placeholder_value(wallet_address):
                config["walletAddress"] = wallet_address
            if private_key and not _is_placeholder_value(private_key):
                config["privateKey"] = private_key

            options = config.setdefault("options", {})
            options["testnet"] = self.network_type == NetworkType.TESTNET
            # Disable fetching HIP3 (user-generated) markets entirely to avoid "Too many DEXes found"
            # By setting types to only ['spot', 'swap'], we skip hip3 market fetching
            options.setdefault("fetchMarkets", {})["types"] = ["spot", "swap"]
            if vault_address and not _is_placeholder_value(vault_address):
                options["vaultAddress"] = vault_address
        else:
            api_key = self.secrets.get("api_key") or self.secrets.get("apiKey")
            secret = self.secrets.get("secret") or self.secrets.get("secretKey")

            if api_key and not _is_placeholder_value(api_key):
                config["apiKey"] = api_key
            if secret and not _is_placeholder_value(secret):
                config["secret"] = secret

        # Override urls only when a base is provided. Binance is excluded: its
        # ccxt default public base already ends in /api/v3 (e.g.
        # https://api.binance.com/api/v3), and blindly replacing the whole
        # urls.api.public with bare https://api.binance.com drops that path —
        # exchangeInfo then resolves to a 403. Let ccxt keep its default for
        # Binance (also required for enable_demo_trading to work).
        if getattr(self, "rest_base_url", None) and self.name != "binance":
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

        if self.name == "hyperliquid":
            # CCXT signs Hyperliquid actions using sandboxMode, not testnet.
            # Pin the signing domain after user options so it always matches
            # the endpoint/network selected by the adapter.
            config["options"]["sandboxMode"] = self.network_type is NetworkType.TESTNET
            config["options"]["testnet"] = self.network_type is NetworkType.TESTNET
            # Trading permission does not authorize referral changes or a
            # separate builder-fee approval during CCXT initialization.
            config["options"]["builderFee"] = False
            config["options"]["refSet"] = True

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
        # ccxt's enable_demo_trading() swaps urls.api → urls.demo for REST,
        # but does NOT swap urls.api.ws for WebSocket.  Manually point WS at
        # the demo stream endpoint (wss://demo-stream.binance.com/ws).
        if self.name == "binance" and self.network_type == NetworkType.TESTNET:
            try:
                self.ccxt_exchange.enable_demo_trading(True)
                # Swap WebSocket URLs to demo equivalents if available
                demo_ws = self.ccxt_exchange.urls.get("demo", {}).get("ws")
                if demo_ws and "ws" in self.ccxt_exchange.urls.get("api", {}):
                    self.ccxt_exchange.urls["api"]["ws"] = demo_ws
                self.logger.debug("Binance demo trading 已启用")
            except Exception as exc:
                await self.ccxt_exchange.close()
                raise RuntimeError("binance: Demo Trading initialization failed") from exc

        # HIP-3 perp markets are only present on specific networks (e.g. the
        # `io`/EntropyIO dex is mainnet-only).  Filter the configured dex
        # whitelist against what actually exists on *this* network before
        # load_markets, else ccxt's fetch_hip3_markets KeyErrors on an absent
        # dex and the whole connection fails.
        if self.name == "hyperliquid":
            await self._filter_hip3_dexes()

        try:
            await self.ccxt_exchange.load_markets()
            self.logger.debug(f"{self.name} CCXT连接已建立 - 网络: {self.network_type.value}")

        except Exception as e:
            self.logger.error(f"{self.name} 市场加载失败: {e}")
            raise

    async def _filter_hip3_dexes(self) -> None:
        """Intersect the configured HIP-3 dex whitelist with dexes that exist
        on the current network's perpDexs endpoint.

        ccxt's fetch_hip3_markets builds perpDexesOffset from the live perpDexs
        response and then does ``offset = perpDexesOffset[dexName]`` for every
        whitelisted dex. If a dex is not present on that network (e.g. the
        mainnet-only ``io`` dex when running on testnet) this raises KeyError
        and the whole ``load_markets``/``connect`` fails. We drop absent dexes
        (best-effort); if *all* are absent we disable HIP-3 loading entirely so
        ``load_markets`` never calls ``fetch_hip3_markets``.

        Mutates ``self.ccxt_exchange.options["fetchMarkets"]`` — the dict that
        ``load_markets`` actually reads (ccxt deep-merges ``config`` into
        ``self.options`` on construction).
        """
        if self.name != "hyperliquid":
            return
        fetch_markets = self.ccxt_exchange.options.get("fetchMarkets", {})
        types = fetch_markets.get("types", [])
        if "hip3" not in types:
            return
        hip3 = fetch_markets.get("hip3", {})
        dexes = hip3.get("dexes")
        if not dexes:
            return

        def _disable_hip3() -> None:
            fetch_markets["types"] = [t for t in types if t != "hip3"]
            hip3.pop("dexes", None)

        try:
            perpDexs = await self.ccxt_exchange.publicPostInfo({"type": "perpDexs"})
        except Exception as exc:
            # If we can't even read perpDexs, load_markets -> fetch_hip3_markets
            # would re-hit the same endpoint and fail again (and the missing
            # dex would KeyError).  Safest is to disable HIP-3 rather than fail.
            self.logger.warning("无法校验 HIP-3 dex 列表(%s)，关闭 hip3 加载", exc)
            _disable_hip3()
            return

        # perpDexs[0] is null (Hyperliquid returns a sentinel at index 0); skip
        # it by only collecting real dict entries with a non-empty name.
        live = {d.get("name") for d in perpDexs if isinstance(d, dict) and d.get("name")}
        kept = [d for d in dexes if d in live]
        dropped = [d for d in dexes if d not in live]
        if dropped:
            self.logger.warning("HIP-3 dex %s 在 %s 不存在，已跳过", dropped, self.network_type.value)
        if kept:
            hip3["dexes"] = kept
        else:
            self.logger.info("%s 上无任何配置的 HIP-3 dex，已关闭 hip3 加载", self.network_type.value)
            _disable_hip3()

    async def connect_websocket(self) -> bool:
        if self.name == "hyperliquid":
            await self._get_ws_client()
            return True
        self.logger.info(f"{self.name} CCXT适配器使用REST API，WebSocket需要单独实现")
        return False

    async def _get_ws_client(self):
        """Lazily create a separate, network-pinned client for subscriptions only."""
        if self.name != "hyperliquid":
            return self.ccxt_exchange
        async with self._ws_client_lock:
            if self._ws_client is None:
                import ccxt.pro as ccxtpro

                class _HyperliquidSubscriptionClient(ccxtpro.hyperliquid):
                    def watch(self, url, message_hash, message=None, subscribe_hash=None, subscription=None):
                        account = (message or {}).get("subscription") or {}
                        if (message or {}).get("method") == "subscribe" and account.get("type") in {
                            "orderUpdates",
                            "userFills",
                        }:
                            # The wire subscription covers the whole account.
                            # Keep symbol-specific futures, but subscribe once
                            # per channel/user on each underlying connection.
                            subscribe_hash = account["type"] + ":" + account["user"]
                        return super().watch(url, message_hash, message, subscribe_hash, subscription)

                    def handle_error_message(self, client, message):
                        if message.get("channel") == "error":
                            # Locked CCXT rejects with a string, which raises
                            # TypeError and stops its receive loop. Never put
                            # the venue's echoed account request in the error.
                            client.reject(ccxt.ExchangeError("hyperliquid: WebSocket subscription rejected"))
                            return True
                        return super().handle_error_message(client, message)

                if self.ccxt_exchange is None or not self.ccxt_exchange.markets:
                    raise RuntimeError("hyperliquid: connect REST markets before WebSocket subscriptions")
                client = _HyperliquidSubscriptionClient(self._build_ccxt_config())
                try:
                    client.set_sandbox_mode(self.network_type is NetworkType.TESTNET)
                    # CCXT lists HIP-3 helper keys even when HIP-3 loading is
                    # disabled, but set_markets_from_exchange indexes them.
                    for key in self.ccxt_exchange.options.get("marketHelperProps", []):
                        self.ccxt_exchange.options.setdefault(key, None)
                    client.set_markets_from_exchange(self.ccxt_exchange)
                except Exception:
                    await client.close()
                    raise
                self._ws_client = client
            return self._ws_client

    async def subscribe_orderbook(self, symbol: str):
        """CCXT通常不直接处理WebSocket订阅"""
        pass

    async def _fetch_balance_impl(self, params: dict[str, Any] | None = None) -> dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_balance(params)

    def _hyperliquid_account_user(self) -> str:
        master, _, _ = self._hyperliquid_credentials()
        if not master:
            raise ValueError("hyperliquid: master_wallet_address is required for account queries")
        options = self._build_ccxt_config()["options"]
        if options.get("vaultAddress") or options.get("subAccountAddress"):
            raise ValueError("hyperliquid: protected account queries do not support vault or subaccount routing")
        return master

    def _hyperliquid_info_params(self, params: dict | None = None) -> dict:
        result = dict(params or {})
        if {"method", "subscription"} & result.keys():
            raise ValueError("hyperliquid: account subscription payload overrides are unsupported")
        for key in ("type", "subType", "account_family"):
            result.pop(key, None)
        master = self._hyperliquid_account_user()
        for key in ("user", "address", "subAccountAddress", "vaultAddress"):
            value = result.pop(key, None)
            if value and str(value).lower() != master:
                raise ValueError("hyperliquid: account query must use the configured master_wallet_address")
        result["user"] = master
        return result

    def _hyperliquid_market(self, instrument: Instrument) -> dict:
        if (
            instrument.venue != self.name
            or instrument.network is not self.network_type
            or instrument.market_type not in {"spot", "perp"}
        ):
            raise ValueError("hyperliquid: account instrument does not match adapter network or venue")
        market = self.ccxt_exchange.market(instrument.venue_symbol)
        if bool(market.get("swap")) != (instrument.market_type == "perp"):
            raise ValueError("hyperliquid: account instrument product does not match market")
        return market

    async def fetch_order_account(self, instrument: Instrument) -> OrderAccountSnapshot:
        if self.name != "hyperliquid":
            return await super().fetch_order_account(instrument)
        market = self._hyperliquid_market(instrument)
        user = self._hyperliquid_account_user()
        abstraction = await self.ccxt_exchange.publicPostInfo({"type": "userAbstraction", "user": user})
        if isinstance(abstraction, str) and abstraction.startswith('"'):
            abstraction = json.loads(abstraction)
        modes = {
            "disabled": "single_asset",
            "default": "single_asset",
            "dexAbstraction": "dex_abstraction",
            "unifiedAccount": "unified",
            "portfolioMargin": "portfolio",
        }
        if not isinstance(abstraction, str) or abstraction not in modes:
            raise ValueError("hyperliquid: unknown account abstraction state")
        params = {
            "user": user,
            "type": "spot" if market["spot"] else "swap",
            "enableUnifiedMargin": abstraction in {"unifiedAccount", "portfolioMargin"},
        }
        dex = self.ccxt_exchange.get_dex_from_hip3_symbol(market)
        if dex:
            params["dex"] = dex
        balance = await self.ccxt_exchange.fetch_balance(params)
        available = {}
        for asset, value in (balance.get("free") or {}).items():
            if value is None or not isfinite(float(value)):
                raise ValueError("hyperliquid: invalid available account balance")
            available[str(asset)] = float(value)
        if not isinstance(balance.get("free"), dict):
            raise ValueError("hyperliquid: account response contains no available balances")
        return OrderAccountSnapshot(
            instrument.market_type,
            available,
            margin_mode=modes[abstraction],
            is_portfolio_margin=abstraction == "portfolioMargin",
            timestamp=time.time(),
        )

    def _hyperliquid_position_snapshot(self, row: dict, timestamp: float) -> OrderPositionSnapshot:
        raw = row.get("position") or {}
        parsed = self.ccxt_exchange.parse_position(row)
        symbol = parsed.get("symbol")
        if not symbol or symbol not in self.ccxt_exchange.markets:
            raise ValueError("hyperliquid: held position has no loaded instrument metadata")
        if row.get("type") != "oneWay":
            raise ValueError(f"hyperliquid:{symbol}: unsupported position mode")
        qty = Decimal(str(raw["szi"]))
        value = Decimal(str(raw["positionValue"]))
        leverage = raw.get("leverage") or {}
        entry = float(raw["entryPx"]) if raw.get("entryPx") is not None else None
        mark = float(abs(value / qty)) if qty else None
        multiplier = float(leverage["value"])
        mode = leverage.get("type")
        if (
            not qty.is_finite()
            or not value.is_finite()
            or not isfinite(multiplier)
            or multiplier <= 0
            or mode not in {"cross", "isolated"}
            or (
                qty
                and (
                    entry is None
                    or not isfinite(entry)
                    or entry <= 0
                    or mark is None
                    or not isfinite(mark)
                    or mark <= 0
                )
            )
        ):
            raise ValueError(f"hyperliquid:{symbol}: invalid position response")
        return OrderPositionSnapshot(symbol, float(qty), entry, mark, multiplier, mode, timestamp)

    async def _hyperliquid_position_state(self, dex: str | None = None) -> tuple[list[dict], float]:
        params = {"type": "clearinghouseState", "user": self._hyperliquid_account_user()}
        if dex:
            params["dex"] = dex
        state = await self.ccxt_exchange.publicPostInfo(params)
        if not isinstance(state, dict) or not isinstance(state.get("assetPositions"), list):
            raise ValueError("hyperliquid: invalid clearinghouse position response")
        timestamp = float(state["time"]) / 1000
        if not isfinite(timestamp) or abs(time.time() - timestamp) > 10:
            raise ValueError("hyperliquid: invalid or stale clearinghouse timestamp")
        return state["assetPositions"], timestamp

    async def fetch_order_position(self, instrument: Instrument) -> OrderPositionSnapshot:
        if self.name != "hyperliquid":
            return await super().fetch_order_position(instrument)
        market = self._hyperliquid_market(instrument)
        if not market["swap"]:
            raise ValueError("hyperliquid: contract position queries require a perpetual instrument")
        rows, timestamp = await self._hyperliquid_position_state(self.ccxt_exchange.get_dex_from_hip3_symbol(market))
        for row in rows:
            position = self._hyperliquid_position_snapshot(row, timestamp)
            if position.symbol == instrument.venue_symbol and position.qty_native:
                return position
        active = await self.ccxt_exchange.publicPostInfo(
            {"type": "activeAssetData", "user": self._hyperliquid_account_user(), "coin": market["baseName"]}
        )
        if active.get("coin") != market["baseName"]:
            raise ValueError("hyperliquid: active asset response does not match requested market")
        leverage = active.get("leverage") or {}
        mark, multiplier = float(active["markPx"]), float(leverage["value"])
        if not all(isfinite(value) and value > 0 for value in (mark, multiplier)) or leverage.get("type") not in {
            "cross",
            "isolated",
        }:
            raise ValueError("hyperliquid: invalid active asset leverage or mark price")
        return OrderPositionSnapshot(instrument.venue_symbol, 0.0, None, mark, multiplier, leverage["type"], timestamp)

    async def fetch_order_positions(self) -> list[OrderPositionSnapshot]:
        if self.name != "hyperliquid":
            return await super().fetch_order_positions()
        self._hyperliquid_account_user()
        dexes = await self.ccxt_exchange.publicPostInfo({"type": "perpDexs"})
        if not isinstance(dexes, list) or not dexes or dexes[0] is not None:
            raise ValueError("hyperliquid: invalid perpetual DEX catalog")
        result = []
        for dex in dexes:
            name = None if dex is None else dex["name"]
            rows, timestamp = await self._hyperliquid_position_state(name)
            for row in rows:
                snapshot = self._hyperliquid_position_snapshot(row, timestamp)
                if snapshot.qty_native:
                    result.append(snapshot)
        return result

    async def fetch_orderbook(self, symbol: str, limit: int = 10, params: dict[str, Any] | None = None) -> dict:
        params = params or {}
        return await self.ccxt_exchange.fetch_order_book(symbol, limit, params)

    def order_capabilities(self, instrument: Instrument) -> OrderCapabilities:
        adapter_network = getattr(self, "network_type", None)
        if (
            instrument.venue != self.name
            or (adapter_network is not None and instrument.network is not adapter_network)
            or instrument.listing_status != "trading"
            or instrument.is_inverse
            or instrument.contract_size != 1
            or instrument.market_type not in {"spot", "perp"}
        ):
            return OrderCapabilities()
        if self.name == "binance":
            return OrderCapabilities(True, ("GTC", "IOC", "FOK"))
        if self.name == "hyperliquid":
            return OrderCapabilities(True, ("GTC", "IOC"), has_position_validation=instrument.market_type == "perp")
        return OrderCapabilities()

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
        result = await self.ccxt_exchange.create_order(symbol, order_type, side, amount, price, params)
        self.invalidate_balance_cache()
        return result

    async def cancel_order(self, order_id: str, symbol: str, params: dict[str, Any] | None = None) -> bool:
        params = params or {}
        result = await self.ccxt_exchange.cancel_order(order_id, symbol, params)
        if self.name == "hyperliquid" and result.get("status") == "success":
            # Hyperliquid returns an ACK; the caller still queries final state.
            return True
        return result.get("status") in ["canceled", "closed"]

    async def fetch_order(self, order_id: str, symbol: str, params: dict[str, Any] | None = None) -> dict:
        params = dict(params or {})
        if self.name == "hyperliquid":
            params.pop("type", None)
        return await self.ccxt_exchange.fetch_order(order_id, symbol, params)

    async def fetch_order_snapshot(
        self, request: OrderRequest, instrument: Instrument, order_id: str | None = None
    ) -> OrderSnapshot:
        from .account_type import account_type_params
        from .order import parse_order_snapshot

        params = account_type_params(request.product)
        if order_id is None:
            params["clientOrderId"] = request.client_order_id
        order = await self.fetch_order(order_id or request.client_order_id, request.symbol, params)
        snapshot = parse_order_snapshot(order, instrument)
        if self.name == "hyperliquid" and snapshot.filled_qty_native:
            unique = {}
            since = order.get("timestamp")
            for _ in range(10):
                trades = await self.fetch_my_trades(None, since, None)
                for trade in trades:
                    if (
                        str(trade.get("order")) == snapshot.order_id
                        and trade.get("symbol") == request.symbol
                        and trade.get("id") is not None
                    ):
                        unique[str(trade["id"])] = trade
                total = sum(Decimal(str(trade["amount"])) for trade in unique.values())
                if total >= Decimal(str(snapshot.filled_qty_native)) - Decimal("1e-12") or len(trades) < 2000:
                    break
                timestamps = [trade["timestamp"] for trade in trades if trade.get("timestamp") is not None]
                cursor = max(timestamps) if timestamps else None
                if since is None or cursor is None or cursor <= since:
                    break
                since = cursor  # Inclusive boundary; IDs deduplicate fills sharing a millisecond.
            order = dict(order, trades=list(unique.values()))
            snapshot = parse_order_snapshot(order, instrument)
            if abs(sum(fill["amount"] for fill in snapshot.fills) - snapshot.filled_qty_native) > 1e-12 or any(
                fill["timestamp"] is None for fill in snapshot.fills
            ):
                snapshot = replace(snapshot, status="unknown")
            return snapshot
        if snapshot.is_terminal and snapshot.filled_qty_base and not snapshot.fills:
            trade_params = {} if self.name == "hyperliquid" else account_type_params(request.product)
            if self.name == "binance":
                trade_params["orderId"] = snapshot.order_id
            trades = await self.ccxt_exchange.fetch_my_trades(request.symbol, None, None, trade_params)
            matches = [trade for trade in trades if str(trade.get("order")) == snapshot.order_id]
            # Never infer full execution costs from an incomplete page of fills.
            unique = {str(trade["id"]): trade for trade in matches if trade.get("id") is not None}
            matches = list(unique.values())
            if matches and abs(sum(float(t["amount"]) for t in matches) - snapshot.filled_qty_base) <= 1e-12:
                order["trades"] = matches
                order["cost"] = sum(float(t["price"]) * float(t["amount"]) for t in matches)
                order["average"] = order["cost"] / snapshot.filled_qty_base
                fees = [
                    fee
                    for trade in matches
                    for fee in (trade.get("fees") or ([trade["fee"]] if trade.get("fee") else []))
                ]
                if all(trade.get("fees") or trade.get("fee") for trade in matches):
                    order["fees"] = fees
            snapshot = parse_order_snapshot(order, instrument)
        return snapshot

    async def submit_order(self, request: OrderRequest, instrument: Instrument) -> OrderSnapshot:
        formatted_price = (
            float(self.ccxt_exchange.price_to_precision(request.symbol, request.price))
            if request.price is not None
            else None
        )
        formatted_amount = float(self.ccxt_exchange.amount_to_precision(request.symbol, request.amount))
        if (
            (request.price is not None and request.side == "buy" and formatted_price > request.price)
            or (request.price is not None and request.side == "sell" and formatted_price < request.price)
            or formatted_amount != request.amount
        ):
            raise ccxt.InvalidOrder(f"{self.name}:{request.symbol}: venue precision changes protected request")
        snapshot = await super().submit_order(request, instrument)
        if snapshot.is_terminal and snapshot.filled_qty_base and not snapshot.fills:
            try:
                return await self.fetch_order_snapshot(request, instrument, snapshot.order_id)
            except Exception as exc:
                self.logger.warning(
                    "%s:%s: accepted order fill lookup failed (%s)", self.name, request.symbol, type(exc).__name__
                )
                return replace(snapshot, status="unknown")
        return snapshot

    async def watch_orders(self, symbol: str | None = None, params: dict[str, Any] | None = None) -> list[dict]:
        """Watch for order updates via ccxt WebSocket. Blocks until next update."""
        params = self._hyperliquid_info_params(params) if self.name == "hyperliquid" else params or {}
        client = await self._get_ws_client()
        return await client.watch_orders(symbol, None, None, params)

    async def watch_user_fills(self, symbol: str | None = None, params: dict | None = None) -> list[dict]:
        if self.name != "hyperliquid":
            raise NotImplementedError(f"{self.name}: user fills subscription is unsupported")
        return await self.watch_my_trades(symbol, params=params)

    def _hyperliquid_fills(self, trades: list[dict]) -> list[dict]:
        if self.name != "hyperliquid":
            return trades
        result = []
        for trade in trades:
            item = dict(trade)
            info = dict(item.get("info") or {})
            # The API's fee already includes builderFee; CCXT 4.5.54 adds it
            # twice. Preserve the venue total, including explicit zero/rebates.
            if info.get("fee") is not None:
                item["fee"] = {"cost": float(info["fee"]), "currency": info.get("feeToken")}
                item["fees"] = [item["fee"]]
            if info.get("closedPnl") is not None:
                info["realizedPnl"] = info["closedPnl"]
                market = self.ccxt_exchange.markets.get(item.get("symbol"), {})
                info["marginAsset"] = market.get("settle") or market.get("quote")
            item["info"] = info
            result.append(item)
        return result

    async def list_markets(self) -> list[Instrument]:
        instruments = []
        if not self.ccxt_exchange or not getattr(self.ccxt_exchange, "markets", None):
            return instruments
        for symbol, market in self.ccxt_exchange.markets.items():
            try:
                instrument = _instrument_from_ccxt_market(
                    self.name,
                    self.network_type,
                    market,
                    self.fees,
                    getattr(self.ccxt_exchange, "precisionMode", None),
                )
                if instrument is not None:
                    instruments.append(instrument)
            except (ValueError, KeyError, TypeError):
                self.logger.warning(
                    "%s:%s: invalid market specification; market excluded", self.name, symbol, exc_info=True
                )
        return instruments

    async def close(self):
        ws_client = getattr(self, "_ws_client", None)
        self._ws_client = None
        if ws_client is not None:
            try:
                await ws_client.close()
            except Exception as exc:
                self.logger.warning("%s: WebSocket client close failed (%s)", self.name, type(exc).__name__)
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
        if self.name == "hyperliquid":
            raise ccxt.NotSupported("hyperliquid: WebSocket client supports read subscriptions only")
        return await self.ccxt_exchange.cancel_order_ws(id, symbol, params=params)

    async def cancel_orders(self, ids, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_orders(ids, symbol, params=params)

    async def cancel_orders_for_symbols(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.cancel_orders_for_symbols(orders, params=params)

    async def cancel_orders_with_client_order_ids(self, clientOrderIds, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_orders_with_client_order_ids(clientOrderIds, symbol, params=params)

    async def cancel_orders_ws(self, ids, symbol=None, params=None) -> dict:
        if self.name == "hyperliquid":
            raise ccxt.NotSupported("hyperliquid: WebSocket client supports read subscriptions only")
        return await self.ccxt_exchange.cancel_orders_ws(ids, symbol, params=params)

    async def cancel_spot_order(self, id, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.cancel_spot_order(id, symbol, params=params)

    async def cancel_unified_order(self, order, params=None) -> dict:
        return await self.ccxt_exchange.cancel_unified_order(order, params=params)

    async def close_all_positions(self, params=None) -> dict:
        return await self.ccxt_exchange.close_all_positions(params=params)

    async def close_position(self, symbol, side=None, params=None) -> dict:
        return await self.ccxt_exchange.close_position(symbol, side, params=params)

    async def close_proxy_sessions(
        self,
    ) -> dict:
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

    async def create_order_with_take_profit_and_stop_loss(
        self, symbol, type, side, amount, price=None, takeProfit=None, stopLoss=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_order_with_take_profit_and_stop_loss(
            symbol, type, side, amount, price, takeProfit, stopLoss, params=params
        )

    async def create_order_with_take_profit_and_stop_loss_ws(
        self, symbol, type, side, amount, price=None, takeProfit=None, stopLoss=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_order_with_take_profit_and_stop_loss_ws(
            symbol, type, side, amount, price, takeProfit, stopLoss, params=params
        )

    async def create_order_ws(self, symbol, type, side, amount, price=None, params=None) -> dict:
        if self.name == "hyperliquid":
            raise ccxt.NotSupported("hyperliquid: WebSocket client supports read subscriptions only")
        return await self.ccxt_exchange.create_order_ws(symbol, type, side, amount, price, params=params)

    async def create_orders(self, orders, params=None) -> dict:
        return await self.ccxt_exchange.create_orders(orders, params=params)

    async def create_orders_ws(self, orders, params=None) -> dict:
        if self.name == "hyperliquid":
            raise ccxt.NotSupported("hyperliquid: WebSocket client supports read subscriptions only")
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
        return await self.ccxt_exchange.create_stop_limit_order(
            symbol, side, amount, price, triggerPrice, params=params
        )

    async def create_stop_limit_order_ws(self, symbol, side, amount, price, triggerPrice, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_limit_order_ws(
            symbol, side, amount, price, triggerPrice, params=params
        )

    async def create_stop_loss_order(
        self, symbol, type, side, amount, price=None, stopLossPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_stop_loss_order(
            symbol, type, side, amount, price, stopLossPrice, params=params
        )

    async def create_stop_loss_order_ws(
        self, symbol, type, side, amount, price=None, stopLossPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_stop_loss_order_ws(
            symbol, type, side, amount, price, stopLossPrice, params=params
        )

    async def create_stop_market_order(self, symbol, side, amount, triggerPrice, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_market_order(symbol, side, amount, triggerPrice, params=params)

    async def create_stop_market_order_ws(self, symbol, side, amount, triggerPrice, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_market_order_ws(symbol, side, amount, triggerPrice, params=params)

    async def create_stop_order(self, symbol, type, side, amount, price=None, triggerPrice=None, params=None) -> dict:
        return await self.ccxt_exchange.create_stop_order(
            symbol, type, side, amount, price, triggerPrice, params=params
        )

    async def create_stop_order_ws(
        self, symbol, type, side, amount, price=None, triggerPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_stop_order_ws(
            symbol, type, side, amount, price, triggerPrice, params=params
        )

    async def create_sub_account(self, name, params=None) -> dict:
        return await self.ccxt_exchange.create_sub_account(name, params=params)

    async def create_take_profit_order(
        self, symbol, type, side, amount, price=None, takeProfitPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_take_profit_order(
            symbol, type, side, amount, price, takeProfitPrice, params=params
        )

    async def create_take_profit_order_ws(
        self, symbol, type, side, amount, price=None, takeProfitPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_take_profit_order_ws(
            symbol, type, side, amount, price, takeProfitPrice, params=params
        )

    async def create_trailing_amount_order(
        self, symbol, type, side, amount, price=None, trailingAmount=None, trailingTriggerPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_trailing_amount_order(
            symbol, type, side, amount, price, trailingAmount, trailingTriggerPrice, params=params
        )

    async def create_trailing_amount_order_ws(
        self, symbol, type, side, amount, price=None, trailingAmount=None, trailingTriggerPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_trailing_amount_order_ws(
            symbol, type, side, amount, price, trailingAmount, trailingTriggerPrice, params=params
        )

    async def create_trailing_percent_order(
        self, symbol, type, side, amount, price=None, trailingPercent=None, trailingTriggerPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_trailing_percent_order(
            symbol, type, side, amount, price, trailingPercent, trailingTriggerPrice, params=params
        )

    async def create_trailing_percent_order_ws(
        self, symbol, type, side, amount, price=None, trailingPercent=None, trailingTriggerPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_trailing_percent_order_ws(
            symbol, type, side, amount, price, trailingPercent, trailingTriggerPrice, params=params
        )

    async def create_trigger_order(
        self, symbol, type, side, amount, price=None, triggerPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_trigger_order(
            symbol, type, side, amount, price, triggerPrice, params=params
        )

    async def create_trigger_order_ws(
        self, symbol, type, side, amount, price=None, triggerPrice=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.create_trigger_order_ws(
            symbol, type, side, amount, price, triggerPrice, params=params
        )

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

    async def edit_order_with_client_order_id(
        self, clientOrderId, symbol, type, side, amount=None, price=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.edit_order_with_client_order_id(
            clientOrderId, symbol, type, side, amount, price, params=params
        )

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

    async def fetch_contract_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
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

    async def fetch_free_margin(self, params=None) -> dict:
        """Fetch free margin for the account (perp/swap accounts).

        Not all ccxt exchanges support fetch_free_balance natively for swap
        accounts. Falls back to computing free balance from fetch_balance
        when the native method is unavailable.
        """
        try:
            return await self.ccxt_exchange.fetch_free_balance(params=params)
        except Exception:
            # Fall back to regular balance — for perp accounts the "free"
            # field is typically the available margin.
            balance = await self.ccxt_exchange.fetch_balance(params=params)
            free = balance.get("free", {})
            return {"free": dict(free), "info": balance.get("info", {})}

    async def fetch_funding_history(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_history(symbol, since, limit, params=params)

    async def fetch_funding_interval(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_interval(symbol, params=params)

    async def fetch_funding_intervals(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_intervals(symbols, params=params)

    async def fetch_funding_rate(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_rate(symbol, params=params or {})

    async def fetch_funding_rate_history(self, symbol=None, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_rate_history(symbol, since, limit, params=params or {})

    async def fetch_funding_rates(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_funding_rates(symbols, params=params or {})

    async def fetch_greeks(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_greeks(symbol, params=params)

    async def fetch_index_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
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

    async def fetch_long_short_ratio_history(
        self, symbol=None, timeframe=None, since=None, limit=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.fetch_long_short_ratio_history(symbol, timeframe, since, limit, params=params)

    async def fetch_margin_adjustment_history(
        self, symbol=None, type=None, since=None, limit=None, params=None
    ) -> dict:
        return await self.ccxt_exchange.fetch_margin_adjustment_history(symbol, type, since, limit, params=params)

    async def fetch_margin_mode(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_margin_mode(symbol, params=params)

    async def fetch_margin_modes(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_margin_modes(symbols, params=params)

    async def fetch_mark_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
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

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> list[dict]:
        if self.name == "hyperliquid":
            params = self._hyperliquid_info_params(params)
        trades = await self.ccxt_exchange.fetch_my_trades(symbol, since, limit, params=params or {})
        return self._hyperliquid_fills(trades)

    async def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
        # ccxt expects params to be a dict; a bare None breaks extend(request, None).
        return await self.ccxt_exchange.fetch_ohlcv(symbol, timeframe, since, limit, params=params or {})

    async def fetch_ohlcv_ws(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_ohlcv_ws(symbol, timeframe, since, limit, params=params)

    async def fetch_open_interest(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_open_interest(symbol, params=params)

    async def fetch_open_interest_history(self, symbol, timeframe="1h", since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_open_interest_history(symbol, timeframe, since, limit, params=params)

    async def fetch_open_interests(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_open_interests(symbols, params=params)

    async def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None) -> dict:
        if self.name == "hyperliquid":
            params = self._hyperliquid_info_params(params)
        return await self.ccxt_exchange.fetch_open_orders(symbol, since, limit, params=params)

    async def fetch_option(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_option(symbol, params=params)

    async def fetch_option_chain(self, code, params=None) -> dict:
        return await self.ccxt_exchange.fetch_option_chain(code, params=params)

    async def fetch_order_book(self, symbol, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_order_book(symbol, limit, params=params or {})

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
        if self.name == "hyperliquid":
            params = self._hyperliquid_info_params(params)
        return await self.ccxt_exchange.fetch_position(symbol, params=params or {})

    async def fetch_position_adl_rank(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_position_adl_rank(symbol, params=params)

    async def fetch_position_history(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_position_history(symbol, since, limit, params=params)

    async def fetch_position_mode(self, symbol=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_position_mode(symbol, params=params)

    async def fetch_positions(self, symbols=None, params=None) -> dict:
        if self.name == "hyperliquid":
            params = self._hyperliquid_info_params(params)
        return await self.ccxt_exchange.fetch_positions(symbols, params=params or {})

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

    async def fetch_premium_index_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_premium_index_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_spot_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_spot_ohlcv(symbol, timeframe, since, limit, params=params)

    async def fetch_spot_tickers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_spot_tickers(symbols, params=params)

    async def fetch_status(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_status(params=params)

    async def fetch_ticker(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_ticker(symbol, params=params or {})

    async def fetch_tickers(self, symbols=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_tickers(symbols, params=params or {})

    async def fetch_market_statistics(self, symbols: list[str]) -> dict[str, dict]:
        """Return per-symbol funding/volume/open-interest stats.

        Hyperliquid HIP-3 perp markets are not reachable through ccxt's unified
        funding/ticker methods: ``fetch_funding_rates`` issues ``metaAndAssetCtxs``
        without a ``dex`` (native perps only), while ``fetch_ticker`` /
        ``fetch_tickers`` raise ``TypeError`` on HIP-3 symbols (ccxt 4.5.54).
        So for Hyperliquid we read the raw ``/info`` endpoint directly — once
        without a dex for native perps, then once per HIP-3 dex — and map the
        coin name back to the unified symbol. Other venues use the generic
        funding+ticker implementation.
        """
        if self.name == "hyperliquid":
            return await self._fetch_hl_statistics(symbols)
        return await super().fetch_market_statistics(symbols)

    async def _fetch_hl_statistics(self, symbols: list[str]) -> dict[str, dict]:
        """Hyperliquid raw /info statistics for native perp + HIP-3 dex markets."""
        if not self.ccxt_exchange or not getattr(self.ccxt_exchange, "markets", None):
            return {}
        markets = self.ccxt_exchange.markets
        requested = [s for s in symbols if s in markets]
        if not requested:
            return {}

        # Group requested symbols by their HIP-3 dex (None = native perp).
        by_dex: dict[str | None, list[str]] = {}
        for sym in requested:
            dex = (markets[sym].get("info") or {}).get("dex")
            by_dex.setdefault(dex, []).append(sym)

        # Coin name (e.g. "BTC" / "xyz:TSLA") -> unified symbol lookup.
        coin_to_symbol: dict[str, str] = {}
        for sym, m in markets.items():
            name = (m.get("info") or {}).get("name")
            if name:
                coin_to_symbol[name] = sym

        result: dict[str, dict] = {}
        for dex in by_dex:
            request: dict[str, object] = {"type": "metaAndAssetCtxs"}
            if dex is not None:
                request["dex"] = dex
            try:
                response = await self.ccxt_exchange.publicPostInfo(request)
                meta, asset_ctxs = response[0], response[1]
            except Exception as exc:
                self.logger.warning("metaAndAssetCtxs failed (dex=%s): %s", dex, exc)
                continue
            universe = meta.get("universe", []) if isinstance(meta, dict) else []
            asset_ctxs = asset_ctxs or []
            for u, ctx in zip(universe, asset_ctxs, strict=False):
                name = u.get("name")
                sym = coin_to_symbol.get(name)
                if sym is None or sym not in requested:
                    continue
                funding = ctx.get("funding")
                volume = ctx.get("dayNtlVlm")
                open_interest = ctx.get("openInterest")
                result[sym] = {
                    "funding_rate": float(funding) if funding is not None else None,
                    "next_funding_time": None,
                    "quote_volume_24h": float(volume) if volume is not None else None,
                    "open_interest": float(open_interest) if open_interest is not None else None,
                }
        return result

    async def fetch_time(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_time(params=params)

    async def fetch_total_balance(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_total_balance(params=params)

    async def fetch_trades(self, symbol, since=None, limit=None, params=None) -> dict:
        if self.name == "hyperliquid":
            # CCXT 4.5.54 incorrectly delegates public trades to user fills.
            # recentTrades is a recent public snapshot, not paginated history.
            options = dict(params or {})
            for key in ("type", "subType", "account_family"):
                options.pop(key, None)
            if options:
                raise ValueError("hyperliquid: recent public trades do not support extra request parameters")
            market = self.ccxt_exchange.market(symbol)
            response = await self.ccxt_exchange.publicPostInfo(
                {"type": "recentTrades", "coin": market["baseName"] if market["swap"] else market["id"]}
            )
            if not isinstance(response, list):
                raise ValueError("hyperliquid: invalid recent public trades response")
            trades = self.ccxt_exchange.parse_trades(response, market, since)
            return trades[-limit:] if limit is not None and limit > 0 else trades
        return await self.ccxt_exchange.fetch_trades(symbol, since, limit, params=params or {})

    async def fetch_trades_ws(self, symbol, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trades_ws(symbol, since, limit, params=params)

    async def fetch_trading_fee(self, symbol, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trading_fee(symbol, params=params or {})

    async def fetch_trading_fees(self, params=None) -> dict:
        return await self.ccxt_exchange.fetch_trading_fees(params=params or {})

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
        return await self.ccxt_exchange.set_leverage(leverage, symbol, params=params or {})

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

    async def un_watch_ohlcv(self, symbol, timeframe="1m", params=None) -> dict:
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

    async def watch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> list[dict]:
        if self.name == "hyperliquid":
            params = self._hyperliquid_info_params(params)
        client = await self._get_ws_client()
        trades = await client.watch_my_trades(symbol, since, limit, params=params or {})
        return self._hyperliquid_fills(trades)

    async def watch_my_trades_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_my_trades_for_symbols(symbols, since, limit, params=params)

    async def watch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_ohlcv(symbol, timeframe, since, limit, params=params)

    async def watch_ohlcv_for_symbols(self, symbolsAndTimeframes, since=None, limit=None, params=None) -> dict:
        return await self.ccxt_exchange.watch_ohlcv_for_symbols(symbolsAndTimeframes, since, limit, params=params)

    async def watch_order_book(self, symbol, limit=None, params=None) -> dict:
        params = params or {}
        if self.name == "hyperliquid":
            if {"method", "subscription"} & params.keys():
                raise ValueError("hyperliquid: order book subscription payload overrides are unsupported")
            params = {key: value for key, value in params.items() if key not in {"type", "subType", "account_family"}}
            client = await self._get_ws_client()
            return await client.watch_order_book(symbol, limit, params)
        return await self._watch_with_type_override(self.ccxt_exchange.watch_order_book, symbol, limit, params)

    async def watch_order_book_for_symbols(self, symbols, limit=None, params=None) -> dict:
        params = params or {}
        return await self._watch_with_type_override(
            self.ccxt_exchange.watch_order_book_for_symbols, symbols, limit, params
        )

    async def _watch_with_type_override(self, method, *args) -> dict:
        """Temporarily override defaultType from params so ccxt.pro's
        internal fetch_order_book_snapshot resolves the correct market."""
        params = args[-1] if args else {}
        market_type = params.get("type") if isinstance(params, dict) else None
        if market_type and hasattr(self.ccxt_exchange, "options"):
            saved = self.ccxt_exchange.options.get("defaultType")
            self.ccxt_exchange.options["defaultType"] = market_type
            try:
                return await method(*args)
            finally:
                if saved is not None:
                    self.ccxt_exchange.options["defaultType"] = saved
                else:
                    self.ccxt_exchange.options.pop("defaultType", None)
        return await method(*args)

    async def watch_orders_for_symbols(self, symbols, since=None, limit=None, params=None) -> dict:
        params = params or {}
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
