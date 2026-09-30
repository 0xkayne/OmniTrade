"""Binance spot and perpetual products behind one project-level venue."""

from __future__ import annotations

import asyncio
import math
import time
import warnings
from collections import deque
from dataclasses import replace

from ccxt.base.errors import BadSymbol, InvalidOrder

from src.market.instrument import Instrument, NetworkType

from .base import BaseExchange
from .binance_clients import _BinanceRateBudget, _create_binance_client
from .ccxt import _instrument_from_ccxt_market
from .order import (
    OrderAccountSnapshot,
    OrderCapabilities,
    OrderPositionSnapshot,
    OrderRequest,
    OrderSnapshot,
    parse_order_snapshot,
)


class BinanceExchange(BaseExchange):
    """Own fixed spot/USDM/COINM clients; never mutate a shared defaultType."""

    supports_user_fills = True

    def __init__(self, name: str, config: dict, secrets: dict):
        super().__init__(name, config, secrets)
        families = config.get("market_families", ["spot", "usdm"])
        if not isinstance(families, list) or not families or any(f not in {"spot", "usdm", "coinm"} for f in families):
            raise ValueError("binance: market_families must contain spot, usdm or coinm")
        if (config.get("options") or {}).get("portfolioMargin") or (config.get("options") or {}).get("papi"):
            raise ValueError("binance: Portfolio Margin is not supported")
        self.market_families = tuple(dict.fromkeys(families))
        self.clients: dict[str, object] = {}
        self.ws_clients: dict[str, object] = {}
        self.family_errors: dict[str, str] = {}
        self.rate_budget = _BinanceRateBudget()
        self._instruments: dict[str, Instrument] = {}
        self._families_by_symbol: dict[str, str] = {}
        self._ws_locks = {family: asyncio.Lock() for family in self.market_families}
        self._fill_queues: dict[str, deque] = {}
        self._seen_fills: set[tuple[str, str, str]] = set()

    async def connect(self):
        async def initialize(family: str):
            client = None
            try:
                client = _create_binance_client(
                    family, self.network_type, self.secrets, self.config.get("options"), rate_budget=self.rate_budget
                )
                await asyncio.wait_for(client.load_markets(), timeout=10.0)
                self.clients[family] = client
            except asyncio.CancelledError:
                if client is not None:
                    await client.close()
                raise
            except Exception as exc:
                if client is not None:
                    await client.close()
                self.family_errors[family] = f"{type(exc).__name__}: product initialization failed"
                self.logger.warning("binance:%s: initialization failed (%s)", family, type(exc).__name__)

        try:
            await asyncio.gather(*(initialize(family) for family in self.market_families))
        except BaseException:
            await self.close()
            raise
        if not self.clients:
            raise RuntimeError(f"binance:{self.network_type.value}: all enabled products failed: {self.family_errors}")
        await self.list_markets()

    def switch_network(self, network: NetworkType) -> bool:
        raise ValueError("binance: changing network requires a newly configured adapter and matching credentials")

    def get_network_info(self) -> dict:
        return {
            "network": self.network_type.value,
            "is_testnet": self.network_type is NetworkType.TESTNET,
            "market_families": list(self.market_families),
            "ready_families": list(self.clients),
            "family_errors": dict(self.family_errors),
        }

    async def list_markets(self) -> list[Instrument]:
        instruments = {}
        families = {}
        for family, client in self.clients.items():
            for market in (getattr(client, "markets", None) or {}).values():
                if not self._market_matches_family(market, family):
                    continue
                fees = (self.config.get("product_fees") or {}).get(family, self.fees)
                try:
                    instrument = _instrument_from_ccxt_market(
                        self.name, self.network_type, market, fees, getattr(client, "precisionMode", None)
                    )
                except (ValueError, KeyError, TypeError):
                    self.logger.warning(
                        "binance:%s:%s: invalid market metadata; excluded", family, market.get("symbol")
                    )
                    continue
                if instrument is not None:
                    instruments[instrument.venue_symbol] = instrument
                    families[instrument.venue_symbol] = family
        self._instruments, self._families_by_symbol = instruments, families
        return list(instruments.values())

    @staticmethod
    def _market_matches_family(market: dict, family: str) -> bool:
        if market.get("expiry") or market.get("type") == "future":
            return False
        if family == "spot":
            return market.get("type") == "spot"
        return (
            market.get("type") == "swap"
            and market.get("inverse") is (family == "coinm")
            and (family != "usdm" or market.get("linear") is True)
        )

    def _instrument(self, symbol: str | None) -> Instrument:
        instrument = self._instruments.get(symbol)
        if instrument is None:
            raise BadSymbol(f"binance:{symbol}: active enabled spot/perpetual instrument is required")
        return instrument

    def _family(self, symbol: str | None, params: dict | None = None) -> str:
        params = params or {}
        requested = params.get("account_family")
        if symbol is not None:
            instrument = self._instrument(symbol)
            family = "spot" if instrument.market_type == "spot" else "coinm" if instrument.is_inverse else "usdm"
            if requested is not None and requested != family:
                raise ValueError(f"binance:{symbol}: account family does not match instrument")
        elif requested is not None:
            family = requested
        elif params.get("type") == "spot":
            family = "spot"
        elif params.get("subType") == "inverse" or params.get("type") == "delivery":
            family = "coinm"
        elif params.get("type") in {"swap", "future"} or params.get("subType") == "linear":
            family = "usdm"
        else:
            warnings.warn(
                "binance: implicit USDM account is deprecated; provide account_family", DeprecationWarning, stacklevel=2
            )
            family = "usdm"
        if family not in self.clients:
            raise ValueError(f"binance:{family}: product is disabled or unavailable")
        return family

    def _params(self, family: str, params: dict | None = None) -> dict:
        result = dict(params or {})
        result.pop("account_family", None)
        if result.get("positionSide", "BOTH") != "BOTH" or (family == "spot" and result.get("marginMode")):
            raise ValueError(f"binance:{family}: margin spot and hedge positions are unsupported")
        if any(result.get(key) for key in ("portfolioMargin", "papi", "hedged")):
            raise ValueError(f"binance:{family}: portfolio margin and hedge mode are unsupported")
        # Every client already has a fixed product profile. Some CCXT methods
        # forward arbitrary params verbatim (e.g. spot depth/ticker/klines), so
        # adapter routing metadata must never reach them or a WS subscription.
        result.pop("type", None)
        result.pop("subType", None)
        if result.pop("maxRetriesOnFailure", 0) != 0:
            raise ValueError(f"binance:{family}: automatic request retries are unsupported")
        return result

    def account_params(self, instrument: Instrument) -> dict:
        family = "spot" if instrument.market_type == "spot" else "coinm" if instrument.is_inverse else "usdm"
        params = {"account_family": family, "type": "spot" if family == "spot" else "swap"}
        if family != "spot":
            params["subType"] = "inverse" if family == "coinm" else "linear"
        return params

    def order_capabilities(self, instrument: Instrument) -> OrderCapabilities:
        actual = self._instruments.get(instrument.venue_symbol)
        if (
            instrument.venue != self.name
            or instrument.network is not self.network_type
            or actual is None
            or any(
                getattr(actual, field) != getattr(instrument, field)
                for field in (
                    "market_type",
                    "base",
                    "quote",
                    "settlement_asset",
                    "quantity_unit",
                    "contract_size",
                    "is_inverse",
                    "qty_step",
                    "price_step",
                )
            )
            or instrument.listing_status != "trading"
            or instrument.quote.symbol not in {"USD", "USDT", "USDC"}
        ):
            return OrderCapabilities()
        if instrument.is_inverse and (
            instrument.quote.symbol != "USD" or instrument.settlement_asset != instrument.base
        ):
            return OrderCapabilities()
        family = self._families_by_symbol[instrument.venue_symbol]
        raw = self.clients[family].markets[instrument.venue_symbol].get("info") or {}
        if raw.get("orderTypes") and "LIMIT" not in raw["orderTypes"]:
            return OrderCapabilities()
        allowed = tuple(tif for tif in ("GTC", "IOC", "FOK") if not raw.get("timeInForce") or tif in raw["timeInForce"])
        return OrderCapabilities(True, allowed, instrument.market_type == "perp")

    async def _fetch_balance_impl(self, params=None) -> dict:
        family = self._family(None, params)
        return await self.clients[family].fetch_balance(self._params(family, params))

    async def fetch_free_margin(self, params=None) -> dict:
        balance = await self.fetch_balance(params)
        return {"free": dict(balance.get("free") or {}), "info": balance.get("info")}

    async def fetch_order_account(self, instrument: Instrument) -> OrderAccountSnapshot:
        family = self._family(instrument.venue_symbol)
        params = self._params(family)
        balance = await self.fetch_balance(self.account_params(instrument))
        available = {str(asset): float(value or 0) for asset, value in (balance.get("free") or {}).items()}
        if any(not math.isfinite(value) for value in available.values()):
            raise ValueError(f"binance:{family}: invalid available balance")
        info = balance.get("info") or {}
        position_mode, margin_mode = "oneway", "single_asset"
        if family != "spot":
            mode = await self.clients[family].fetch_position_mode(instrument.venue_symbol, params)
            if mode.get("hedged") is not False:
                raise ValueError(f"binance:{family}: verified one-way position mode is required")
            if family == "usdm":
                margin = await self.clients[family].fapiPrivateGetMultiAssetsMargin({})
                if margin.get("multiAssetsMargin") not in (False, "false"):
                    raise ValueError("binance:usdm: verified single-asset margin mode is required")
        if isinstance(info, dict) and (
            info.get("portfolioMargin") or str(info.get("accountType", "")).upper() in {"PORTFOLIO_MARGIN", "PORTFOLIO"}
        ):
            raise ValueError(f"binance:{family}: Portfolio Margin account is unsupported")
        return OrderAccountSnapshot(family, available, position_mode, margin_mode, timestamp=time.time())

    async def fetch_order_position(self, instrument: Instrument) -> OrderPositionSnapshot:
        family = self._family(instrument.venue_symbol)
        if family == "spot":
            raise ValueError(f"binance:{instrument.venue_symbol}: spot has no contract position")
        positions = await self.clients[family].fetch_positions([instrument.venue_symbol], self._params(family))
        matches = [position for position in positions if position.get("symbol") == instrument.venue_symbol]
        if len(matches) != 1:
            raise ValueError(f"binance:{instrument.venue_symbol}: missing or ambiguous one-way position")
        return self._position_snapshot(matches[0], instrument)

    def _position_snapshot(self, position: dict, instrument: Instrument | None) -> OrderPositionSnapshot:
        symbol = position.get("symbol")
        if not symbol:
            raise ValueError("binance: position response is missing market identity")
        info = position.get("info") or {}
        if position.get("hedged") or info.get("positionSide", "BOTH") != "BOTH":
            raise ValueError(f"binance:{symbol}: hedge-mode position is unsupported")
        if info.get("positionAmt") is None and position.get("contracts") is None:
            raise ValueError(f"binance:{symbol}: missing position quantity")
        native = float(info.get("positionAmt", position.get("contracts")))
        if not math.isfinite(native):
            raise ValueError(f"binance:{symbol}: invalid position quantity")
        if "positionAmt" not in info and position.get("side") == "short":
            native = -abs(native)
        maximum = info.get("maxNotionalValue")
        if maximum is None and info.get("maxQty") is not None and instrument is not None:
            maximum = float(info["maxQty"]) * instrument.contract_size
        for field in ("entryPrice", "markPrice", "leverage"):
            value = position.get(field)
            if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                raise ValueError(f"binance:{symbol}: invalid position {field}")
        return OrderPositionSnapshot(
            symbol,
            native,
            float(position["entryPrice"]) if position.get("entryPrice") else None,
            float(position["markPrice"]) if position.get("markPrice") else None,
            float(position["leverage"]) if position.get("leverage") else None,
            position.get("marginMode") or ("isolated" if position.get("isolated") else "cross"),
            time.time(),
            float(maximum) if maximum is not None and math.isfinite(float(maximum)) else None,
        )

    async def fetch_order_positions(self) -> list[OrderPositionSnapshot]:
        result = []
        seen = set()
        for family in self.market_families:
            if family == "spot":
                continue
            if family not in self.clients:
                raise ValueError(f"binance:{family}: complete venue position query unavailable")
            rows = await self.clients[family].fetch_positions(None, self._params(family))
            for row in rows:
                symbol = row.get("symbol")
                instrument = self._instruments.get(symbol)
                # Mixed UM/CM replies can repeat an identifiable position. An
                # unknown market remains visible so risk cannot ignore it.
                if instrument is not None and self._families_by_symbol[symbol] != family:
                    continue
                snapshot = self._position_snapshot(row, instrument)
                if snapshot.qty_native and symbol not in seen:
                    result.append(snapshot)
                    seen.add(symbol)
        return result

    async def fetch_orderbook(self, symbol, limit=10, params=None) -> dict:
        family = self._family(symbol, params)
        return await self.clients[family].fetch_order_book(symbol, limit, self._params(family, params))

    async def create_order(self, symbol, order_type, side, amount, price=None, params=None) -> dict:
        family = self._family(symbol, params)
        instrument = self._instrument(symbol)
        if not self.order_capabilities(instrument).has_client_order_id:
            raise InvalidOrder(f"binance:{symbol}: instrument is not supported for execution")
        if (
            not math.isfinite(amount)
            or amount <= 0
            or side not in {"buy", "sell"}
            or order_type not in {"market", "limit"}
        ):
            raise InvalidOrder(f"binance:{symbol}: invalid order request")
        routed = self._params(family, params)
        if family != "spot":
            routed["positionSide"] = "BOTH"
        try:
            return await self.clients[family].create_order(symbol, order_type, side, amount, price, routed)
        finally:
            self.invalidate_balance_cache()

    async def submit_order(self, request: OrderRequest, instrument: Instrument) -> OrderSnapshot:
        if request.symbol != instrument.venue_symbol or request.product != instrument.market_type:
            raise InvalidOrder(f"binance:{request.symbol}: request does not match instrument")
        family = self._family(request.symbol)
        if request.account_family is not None and request.account_family != family:
            raise InvalidOrder(f"binance:{request.symbol}: saved order account does not match instrument")
        if request.quantity_unit != instrument.quantity_unit and (
            instrument.is_inverse or instrument.contract_size != 1
        ):
            raise InvalidOrder(f"binance:{request.symbol}: native quantity unit is required")
        client = self.clients[family]
        amount = float(client.amount_to_precision(request.symbol, request.amount))
        price = float(client.price_to_precision(request.symbol, request.price)) if request.price is not None else None
        if amount != request.amount or (
            price is not None
            and (
                (request.side == "buy" and price > request.price) or (request.side == "sell" and price < request.price)
            )
        ):
            raise InvalidOrder(f"binance:{request.symbol}: venue precision changes protected request")
        snapshot = await super().submit_order(request, instrument)
        if snapshot.is_terminal and snapshot.filled_qty_native:
            try:
                return await self.fetch_order_snapshot(request, instrument, snapshot.order_id)
            except Exception as exc:
                # Creation has already succeeded. A lookup error must never be
                # mistaken for a rejected create by the durable order manager.
                self.logger.warning(
                    "binance:%s: accepted order needs reconciliation (%s)", request.symbol, type(exc).__name__
                )
                return replace(snapshot, status="unknown")
        return snapshot

    async def fetch_order(self, id, symbol=None, params=None) -> dict:
        family = self._family(symbol, params)
        return await self.clients[family].fetch_order(id, symbol, self._params(family, params))

    async def fetch_order_by_client_id(self, client_order_id, symbol=None, params=None) -> dict:
        return await self.fetch_order(None, symbol, {**(params or {}), "origClientOrderId": client_order_id})

    async def cancel_order(self, id, symbol=None, params=None) -> bool:
        family = self._family(symbol, params)
        result = await self.clients[family].cancel_order(id, symbol, self._params(family, params))
        self.invalidate_balance_cache()
        return result.get("status") in {"canceled", "cancelled", "closed", "expired"}

    async def fetch_order_snapshot(self, request: OrderRequest, instrument: Instrument, order_id=None) -> OrderSnapshot:
        params = self.account_params(instrument)
        order = (
            await self.fetch_order(order_id, request.symbol, params)
            if order_id is not None
            else await self.fetch_order_by_client_id(request.client_order_id, request.symbol, params)
        )
        snapshot = parse_order_snapshot(order, instrument)
        if snapshot.filled_qty_native:
            family = self._family(request.symbol)
            unique = {str(fill["id"]): fill for fill in order.get("trades") or [] if fill.get("id") is not None}
            since = order.get("timestamp")
            cursor = None
            for _ in range(100):
                trade_params = self._params(family)
                if cursor is not None:
                    trade_params["fromId"] = cursor
                elif snapshot.order_id is not None:
                    trade_params["orderId"] = snapshot.order_id
                page = await self.clients[family].fetch_my_trades(
                    request.symbol, since if cursor is None else None, 1000, trade_params
                )
                for trade in page:
                    if str(trade.get("order")) == snapshot.order_id and trade.get("id") is not None:
                        unique[str(trade["id"])] = trade
                matched_qty = sum(float(trade["amount"]) for trade in unique.values())
                if matched_qty >= snapshot.filled_qty_native - 1e-12 or len(page) < 1000:
                    break
                ids = [int(trade["id"]) for trade in page if str(trade.get("id", "")).isdigit()]
                if not ids or (cursor is not None and max(ids) < cursor):
                    break
                cursor = max(ids) + 1
            order = dict(order, trades=list(unique.values()))
            snapshot = parse_order_snapshot(order, instrument)
            total = sum(fill["amount"] for fill in snapshot.fills)
            if abs(total - snapshot.filled_qty_native) > 1e-12 or any(
                fill.get("id") is None or fill.get("timestamp") is None for fill in snapshot.fills
            ):
                snapshot = replace(snapshot, status="unknown")
        return snapshot

    async def set_leverage(self, leverage, symbol=None, params=None) -> dict:
        family = self._family(symbol, params)
        if family == "spot":
            raise ValueError(f"binance:{symbol}: leverage is unavailable for spot")
        return await self.clients[family].set_leverage(leverage, symbol, self._params(family, params))

    async def fetch_position(self, symbol, params=None) -> dict:
        family = self._family(symbol, params)
        return await self.clients[family].fetch_position(symbol, self._params(family, params))

    async def fetch_positions(self, symbols=None, params=None) -> list:
        selected = symbols or [
            symbol for symbol, instrument in self._instruments.items() if instrument.market_type == "perp"
        ]
        result = []
        for family, group in self._groups(selected, params).items():
            rows = await self.clients[family].fetch_positions(group, self._params(family, params))
            result.extend(row for row in rows if row.get("symbol") in group)
        return result

    async def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None) -> list:
        family = self._family(symbol, params)
        return await self.clients[family].fetch_ohlcv(symbol, timeframe, since, limit, self._params(family, params))

    async def fetch_ticker(self, symbol, params=None) -> dict:
        family = self._family(symbol, params)
        return await self.clients[family].fetch_ticker(symbol, self._params(family, params))

    def _groups(self, symbols, params=None) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for symbol in symbols:
            groups.setdefault(self._family(symbol, params), []).append(symbol)
        return groups

    async def fetch_tickers(self, symbols=None, params=None) -> dict:
        result = {}
        for family, group in self._groups(symbols or list(self._instruments), params).items():
            rows = await self.clients[family].fetch_tickers(group, self._params(family, params))
            result.update(
                {symbol: row for symbol, row in rows.items() if symbol in group and row.get("symbol") == symbol}
            )
        return result

    async def fetch_funding_rate(self, symbol, params=None) -> dict:
        family = self._family(symbol, params)
        if family == "spot":
            raise ValueError(f"binance:{symbol}: funding is unavailable for spot")
        return await self.clients[family].fetch_funding_rate(symbol, self._params(family, params))

    async def fetch_funding_rates(self, symbols=None, params=None) -> dict:
        selected = symbols or [
            symbol for symbol, instrument in self._instruments.items() if instrument.market_type == "perp"
        ]
        result = {}
        for family, group in self._groups(selected, params).items():
            if family == "spot":
                continue
            rows = await self.clients[family].fetch_funding_rates(group, self._params(family, params))
            result.update(
                {symbol: row for symbol, row in rows.items() if symbol in group and row.get("symbol") == symbol}
            )
        return result

    async def fetch_trading_fee(self, symbol, params=None) -> dict:
        family = self._family(symbol, params)
        return await self.clients[family].fetch_trading_fee(symbol, self._params(family, params))

    async def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None) -> list:
        family = self._family(symbol, params)
        rows = await self.clients[family].fetch_open_orders(symbol, since, limit, self._params(family, params))
        return [
            row
            for row in rows
            if row.get("symbol") in self._instruments
            and self._families_by_symbol[row["symbol"]] == family
            and (symbol is None or row["symbol"] == symbol)
        ]

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> list:
        family = self._family(symbol, params)
        return await self.clients[family].fetch_my_trades(symbol, since, limit, self._params(family, params))

    async def _ws_client(self, family: str):
        async with self._ws_locks[family]:
            if family not in self.ws_clients:
                client = _create_binance_client(
                    family,
                    self.network_type,
                    self.secrets,
                    self.config.get("options"),
                    use_websocket=True,
                    rate_budget=self.rate_budget,
                )
                try:
                    await client.load_markets()
                except BaseException:
                    await client.close()
                    raise
                self.ws_clients[family] = client
        return self.ws_clients[family]

    async def connect_websocket(self) -> bool:
        for family in self.clients:
            await self._ws_client(family)
        return True

    async def subscribe_orderbook(self, symbol: str):
        return await self.watch_order_book(symbol)

    async def watch_order_book(self, symbol, limit=None, params=None) -> dict:
        family = self._family(symbol, params)
        client = await self._ws_client(family)
        return await client.watch_order_book(symbol, limit, self._params(family, params))

    async def watch_orders(self, symbol=None, params=None) -> dict:
        family = self._family(symbol, params)
        client = await self._ws_client(family)
        while True:
            updates = await client.watch_orders(symbol, None, None, self._params(family, params))
            updates = updates if isinstance(updates, list) else [updates]
            filtered = [
                row
                for row in updates
                if row.get("symbol") in self._instruments
                and self._families_by_symbol[row["symbol"]] == family
                and (symbol is None or row.get("symbol") == symbol)
            ]
            if filtered:
                return filtered

    async def watch_user_fills(self, symbol=None, params=None) -> dict:
        family = self._family(symbol, params)
        client = await self._ws_client(family)
        queue = self._fill_queues.setdefault(symbol or family, deque())
        while not queue:
            rows = await client.watch_my_trades(symbol, None, None, self._params(family, params))
            for trade in rows if isinstance(rows, list) else [rows]:
                trade_symbol = trade.get("symbol")
                if (
                    trade_symbol not in self._instruments
                    or self._families_by_symbol[trade_symbol] != family
                    or (symbol and symbol != trade_symbol)
                    or trade.get("id") is None
                ):
                    continue
                key = (family, trade_symbol, str(trade["id"]))
                if key not in self._seen_fills:
                    self._seen_fills.add(key)
                    queue.append(trade)
        return queue.popleft()

    async def close(self):
        clients = [*self.ws_clients.values(), *self.clients.values()]
        self.ws_clients.clear()
        self.clients.clear()
        outcomes = await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                self.logger.warning("binance: client shutdown failed (%s)", type(outcome).__name__)
        await super().close()
