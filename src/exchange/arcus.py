"""Native Arcus exchange adapter.

Arcus is not currently supported by ccxt.  This module keeps Arcus-specific
transport, authentication and field mapping at the exchange boundary while
exposing the project's :class:`BaseExchange` contract.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from src.exchange.base import BaseExchange
from src.exchange.order import (
    OrderAccountSnapshot,
    OrderCapabilities,
    OrderPositionSnapshot,
    OrderRequest,
    OrderSnapshot,
    parse_order_snapshot,
)
from src.market.asset import Asset
from src.market.instrument import Instrument

try:  # optional at import time so public market reads still work in minimal envs
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:  # pragma: no cover - exercised only when dependency absent
    Ed25519PrivateKey = None  # type: ignore[assignment,misc]


class ArcusError(RuntimeError):
    """An Arcus API error with endpoint and machine-readable code context."""

    def __init__(self, message: str, *, endpoint: str | None = None, code: str | None = None):
        self.endpoint = endpoint
        self.code = code
        suffix = f" ({code})" if code else ""
        super().__init__(f"Arcus{suffix}: {message}" + (f" [{endpoint}]" if endpoint else ""))


class ArcusSigner:
    """Ed25519 signer for Arcus ordersign and legacy request messages."""

    def __init__(self, api_signing_key: str | bytes):
        if Ed25519PrivateKey is None:
            raise RuntimeError("Arcus signing requires the 'cryptography' package")
        if isinstance(api_signing_key, str):
            value = api_signing_key.removeprefix("0x")
            try:
                api_signing_key = bytes.fromhex(value)
            except ValueError as exc:
                raise ValueError("Arcus api_signing_key (API Signing Key) must be hex encoded") from exc
        if len(api_signing_key) != 32:
            raise ValueError("Arcus api_signing_key must contain 32 bytes of Ed25519 private key material")
        self._key = Ed25519PrivateKey.from_private_bytes(api_signing_key)

    @staticmethod
    def canonical_json(value: dict[str, Any]) -> bytes:
        """Return Arcus canonical JSON (recursive sorted keys, no whitespace)."""
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    def sign_payload(self, payload: dict[str, Any]) -> str:
        return self._key.sign(self.canonical_json(payload)).hex()

    def sign_legacy(self, timestamp_ns: int, action: str, body: dict[str, Any] | None = None) -> str:
        message = str(timestamp_ns).encode() + action.encode() + self.canonical_json(body or {})
        return self._key.sign(message).hex()


def _decimal(value: Any, default: float = 0.0) -> float:
    try:
        return float(Decimal(str(value))) if value is not None else default
    except (InvalidOperation, ValueError, TypeError):
        return default


def _integer_step(value: float, step: float) -> int:
    """Convert a human value to Arcus integer ticks/quantums exactly."""
    if step <= 0:
        raise ValueError("Arcus market step must be positive")
    quotient = Decimal(str(value)) / Decimal(str(step))
    if quotient != quotient.to_integral_value():
        raise ValueError(f"value {value} is not aligned to Arcus step {step}")
    return int(quotient)


def _arcus_number(value: Any, field: str, *, positive: bool = False) -> float:
    """Parse authoritative account/order facts without replacing unknown values with zero."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"Arcus: missing or invalid {field}") from None
    if not number.is_finite() or not math.isfinite(float(number)) or (positive and number <= 0):
        raise ValueError(f"Arcus: missing or invalid {field}")
    return float(number)


def build_place_order_payload(
    *,
    address: str,
    account_index: int,
    market_id: int,
    timestamp_ns: int,
    price_ticks: int,
    quantity_quanta: int,
    side_code: int,
    order_type_code: int,
    expiry: int,
    reduce_only: bool = False,
    client_order_id: str | None = None,
    operation: int = 1,
) -> dict[str, Any]:
    """Build the typed ordersign payload used by ``placeOrder``."""
    payload: dict[str, Any] = {
        "ad": address.lower(),
        "ai": int(account_index),
        "ct": int(timestamp_ns),
        "g": int(expiry),
        "m": int(market_id),
        "op": int(operation),
        "p": int(price_ticks),
        "q": int(quantity_quanta),
        "r": int(bool(reduce_only)),
        "s": int(side_code),
        "t": int(order_type_code),
        "v": 1,
    }
    if client_order_id:
        payload["c"] = str(client_order_id)
    return payload


class ArcusExchange(BaseExchange):
    """Native Arcus REST/WebSocket adapter."""

    supports_user_fills = True

    def __init__(self, name: str, config: dict, secrets: dict):
        super().__init__(name, config, secrets)
        if any(field in secrets for field in ("private_key", "privateKey", "apiKey")):
            raise ValueError(
                "Arcus credential fields have changed: use api_key (API Key) and "
                "api_signing_key (API Signing Key); rename private_key/privateKey to "
                "api_signing_key and apiKey to api_key"
            )
        if any(field in secrets for field in ("address", "wallet_address")):
            raise ValueError(
                "Arcus credential fields have changed: rename address/wallet_address to master_wallet_address"
            )
        self.api_key = secrets.get("api_key")
        self.master_wallet_address = secrets.get("master_wallet_address")
        api_signing_key = secrets.get("api_signing_key")
        self.account_index = int(config.get("options", {}).get("account_index", 0))
        self._signer = ArcusSigner(api_signing_key) if api_signing_key else None
        self._markets_by_symbol: dict[str, Instrument] = {}
        self._markets_by_id: dict[int, Instrument] = {}
        self._market_meta: dict[str, dict[str, Any]] = {}
        self._ws_queue: Any = None
        self._ws_queues: dict[str, asyncio.Queue] = {}
        self._ws_reader_task: asyncio.Task | None = None
        self._ws_reconnect_task: asyncio.Task | None = None
        self._ws_closing = False
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._orderbook_sequences: dict[str, int] = {}
        self._orderbook_books: dict[str, dict[str, list[list[float]]]] = {}
        self._orderbook_needs_snapshot: set[str] = set()
        self._seen_trade_ids: set[str] = set()
        self._pending_user_fills: asyncio.Queue = asyncio.Queue()
        self._pending_orders: asyncio.Queue = asyncio.Queue()

    def has_credentials(self, instrument: Instrument | None = None) -> bool:
        return bool(self.api_key and self._signer is not None)

    async def connect(self) -> None:
        await self._load_markets()

    async def _load_markets(self) -> None:
        response = await self._request("GET", "/v1/markets")
        rows = response if isinstance(response, list) else response.get("markets")
        if not isinstance(rows, list):
            raise ValueError("Arcus: market response is incomplete")
        markets, by_id, metadata = {}, {}, {}
        for row in rows:
            instrument = self._parse_market(row)
            if instrument is None:
                continue
            markets[instrument.venue_symbol] = instrument
            market_id = int(row.get("marketId"))
            by_id[market_id] = instrument
            metadata[instrument.venue_symbol] = row
        self._markets_by_symbol, self._markets_by_id, self._market_meta = markets, by_id, metadata

    def _parse_market(self, row: dict[str, Any]) -> Instrument | None:
        if not isinstance(row, dict):
            return None
        status = str(row.get("status", "ONLINE")).upper()
        base = str(row.get("baseAsset") or "").upper()
        quote = str(row.get("quoteAsset") or "USD").upper()
        symbol = str(row.get("marketDisplayName") or (f"{base}-{quote}" if base else ""))
        if not base or not symbol or str(row.get("type", "PERP")).upper() not in {"PERP", "PERPETUAL"}:
            return None
        tick = _decimal(row.get("tickSize"))
        step = _decimal(row.get("stepSize"))
        return Instrument(
            venue=self.name,
            network=self.network_type,
            market_type="perp",
            base=Asset(base),
            quote=Asset(quote),
            venue_symbol=symbol,
            min_qty=_decimal(row.get("minOrderSize")),
            qty_step=step,
            price_step=tick,
            min_notional=_decimal(row.get("minOrderNotional")),
            taker_fee_rate=self.fees.get("taker", 0.0),
            maker_fee_rate=self.fees.get("maker", 0.0),
            contract_size=1.0,
            is_inverse=False,
            listing_status="trading" if status == "ONLINE" else status.lower(),
            max_leverage=(1.0 / _decimal(row.get("initialMarginFraction")))
            if _decimal(row.get("initialMarginFraction")) > 0
            else None,
            settlement_asset=Asset(quote),
        )

    async def list_markets(self) -> list[Instrument]:
        if not self._markets_by_symbol:
            await self._load_markets()
        return list(self._markets_by_symbol.values())

    def _market(self, symbol: str) -> Instrument:
        market = self._markets_by_symbol.get(symbol)
        if market is None:
            market = self._markets_by_symbol.get(symbol.upper())
        if market is None:
            raise ValueError(f"Arcus market not found: {symbol}")
        return market

    async def fetch_orderbook(self, symbol: str, limit: int = 10, params: dict | None = None) -> dict:
        market = self._market(symbol)
        endpoint = f"/v1/l2OrderBook/{market.venue_symbol}"
        response = await self._request("GET", endpoint, params={"nLevels": limit, **(params or {})})
        return self._normalize_orderbook(response)

    async def fetch_bbo(self, symbol: str) -> dict:
        """Fetch Arcus best bid/offer and normalize it to a ticker-like shape."""
        market = self._market(symbol)
        response = await self._request("GET", f"/v1/bbo/{market.venue_symbol}")
        return self._normalize_bbo(response, market.venue_symbol)

    @staticmethod
    def _normalize_bbo(response: dict[str, Any], symbol: str) -> dict:
        bid = response.get("bid", response.get("bestBid"))
        ask = response.get("ask", response.get("bestAsk"))
        if isinstance(bid, dict):
            bid = bid.get("price", bid.get("p"))
        if isinstance(ask, dict):
            ask = ask.get("price", ask.get("p"))
        return {
            "symbol": symbol,
            "bid": _decimal(bid) if bid is not None else None,
            "ask": _decimal(ask) if ask is not None else None,
            "info": response,
        }

    async def fetch_time(self) -> int:
        response = await self._request("GET", "/v1/time")
        return int(response.get("timeNs", response.get("timestamp", response.get("time", time.time() * 1000))))

    @staticmethod
    def _normalize_orderbook(response: dict[str, Any]) -> dict:
        def levels(key: str) -> list[list[float]]:
            values = response.get(key, [])
            result = []
            for level in values or []:
                if isinstance(level, dict):
                    price = level.get("price", level.get("p"))
                    amount = level.get("size", level.get("quantity", level.get("q")))
                else:
                    price, amount = level[0], level[1]
                result.append([_decimal(price), _decimal(amount)])
            return result

        result = {"bids": levels("bids"), "asks": levels("asks"), "timestamp": response.get("timestamp")}
        for key in ("lastSequenceId", "globalSequenceId"):
            if response.get(key) is not None:
                result[key] = int(response[key])
        return result

    async def _fetch_balance_impl(self, params: dict | None = None) -> dict:
        query = self._account_query(params)
        response = await self._request("GET", "/v1/account", params=query, authenticated=True)
        self._validate_account(response)
        available = _arcus_number(response.get("freeCollateral"), "freeCollateral")
        equity = _arcus_number(response.get("equity"), "equity")
        return {
            "free": {"USD": available},
            "total": {"USD": equity},
            "used": {"USD": equity - available},
            "info": response,
        }

    def _account_query(self, params: dict | None = None) -> dict:
        if not self.master_wallet_address:
            raise ValueError("Arcus master_wallet_address is required for account reads")
        options = dict(params or {})
        for field, expected in (("address", self.master_wallet_address), ("accountIndex", self.account_index)):
            if field in options and str(options.pop(field)).lower() != str(expected).lower():
                raise ValueError(f"Arcus: {field} cannot override the configured account")
        options.pop("type", None)
        options.pop("subType", None)
        return {"address": self.master_wallet_address, "accountIndex": self.account_index, **options}

    def _validate_account(self, response: dict) -> None:
        if (
            not isinstance(response, dict)
            or str(response.get("address", "")).lower() != str(self.master_wallet_address).lower()
            or response.get("accountIndex") != self.account_index
        ):
            raise ValueError("Arcus: account response does not match configured account")

    async def fetch_order_account(self, instrument: Instrument) -> OrderAccountSnapshot:
        response = await self._request("GET", "/v1/account", params=self._account_query())
        self._validate_account(response)
        if not isinstance(response.get("positions"), dict):
            raise ValueError("Arcus: account response lacks authoritative positions")
        if response.get("marginState", "healthy") != "healthy":
            raise ValueError("Arcus: account margin state is not healthy")
        collateral = _arcus_number(response.get("freeCollateral"), "freeCollateral")
        lending = _arcus_number(response.get("loanBalance", 0), "loanBalance")
        spot = response.get("spotPositions", {})
        if not isinstance(spot, dict):
            raise ValueError("Arcus: invalid spot collateral state")
        return OrderAccountSnapshot(
            "perp",
            {instrument.quote.symbol: collateral},
            margin_mode="single_asset" if not spot and lending == 0 else "multi_asset",
            timestamp=time.time(),
        )

    def _position_snapshot(self, key: str, row: dict, observed_at: float) -> OrderPositionSnapshot:
        if not isinstance(row, dict):
            raise ValueError("Arcus: invalid position entry")
        self._validate_account(row)
        instrument = self._markets_by_id.get(int(key))
        if (
            instrument is None
            or row.get("marketId") != int(key)
            or row.get("marketDisplayName") != instrument.venue_symbol
        ):
            raise ValueError("Arcus: position market identity is unknown or inconsistent")
        quantity = _arcus_number(row.get("size"), "position size")
        side = row.get("side")
        if side not in {"LONG", "SHORT"} or (quantity > 0 and side != "LONG") or (quantity < 0 and side != "SHORT"):
            raise ValueError(f"Arcus:{instrument.venue_symbol}: position direction disagrees with signed size")
        mode = str(row.get("marginMode", "")).lower()
        if mode not in {"cross", "isolated"}:
            raise ValueError(f"Arcus:{instrument.venue_symbol}: unknown margin mode")
        return OrderPositionSnapshot(
            instrument.venue_symbol,
            quantity,
            _arcus_number(row.get("averageEntryPrice"), "position entry price", positive=bool(quantity)),
            _arcus_number(row.get("markPx"), "position mark price", positive=True),
            _arcus_number(row.get("leverage"), "position leverage", positive=True),
            mode,
            observed_at,
        )

    async def _read_positions(
        self, symbol: str | None = None, params: dict | None = None
    ) -> list[OrderPositionSnapshot]:
        await self.list_markets()
        query = self._account_query(params)
        if symbol is not None:
            query["market"] = self._market(symbol).venue_symbol
        response = await self._request("GET", "/v1/positions", params=query)
        if not isinstance(response, dict) or not isinstance(response.get("positions"), dict):
            raise ValueError("Arcus: positions response is missing its complete positions object")
        if "total" in response and response["total"] != len(response["positions"]):
            raise ValueError("Arcus: positions response is incomplete")
        observed = time.time()
        positions = [self._position_snapshot(key, row, observed) for key, row in response["positions"].items()]
        if symbol and any(position.symbol != symbol for position in positions):
            raise ValueError(f"Arcus:{symbol}: position query returned another market")
        return positions

    async def fetch_order_positions(self) -> list[OrderPositionSnapshot]:
        return await self._read_positions()

    async def _read_leverage(self, instrument: Instrument) -> dict:
        response = await self._request("GET", "/v1/leverages", params=self._account_query())
        self._validate_account(response)
        rows = response.get("leverages")
        if not isinstance(rows, list):
            raise ValueError("Arcus: leverage response is incomplete")
        matches = [row for row in rows if isinstance(row, dict) and row.get("marketId") == self._market_id(instrument)]
        if len(matches) != 1 or matches[0].get("marketDisplayName") != instrument.venue_symbol:
            raise ValueError(f"Arcus:{instrument.venue_symbol}: effective leverage is unknown")
        row = matches[0]
        _arcus_number(row.get("leverage"), "effective leverage", positive=True)
        if row.get("marginMode") not in {"CROSS", "ISOLATED"} or row.get("isolated") is not (
            row["marginMode"] == "ISOLATED"
        ):
            raise ValueError(f"Arcus:{instrument.venue_symbol}: effective margin mode is unknown")
        return row

    async def fetch_order_position(self, instrument: Instrument) -> OrderPositionSnapshot:
        positions = await self._read_positions(instrument.venue_symbol)
        if len(positions) > 1:
            raise ValueError(f"Arcus:{instrument.venue_symbol}: duplicate position rows")
        if positions:
            return positions[0]
        leverage = await self._read_leverage(instrument)
        await self._load_markets()
        mark = _arcus_number(
            self._market_meta[instrument.venue_symbol].get("markPrice"), "flat position mark price", positive=True
        )
        return OrderPositionSnapshot(
            instrument.venue_symbol,
            0.0,
            None,
            mark,
            float(leverage["leverage"]),
            leverage["marginMode"].lower(),
            time.time(),
        )

    async def fetch_positions(self, symbols=None, params=None) -> list[dict]:
        positions = await self._read_positions(params=params)
        return [
            {
                "symbol": p.symbol,
                "contracts": abs(p.qty_native),
                "side": "long" if p.qty_native >= 0 else "short",
                "entryPrice": p.entry_price,
                "markPrice": p.mark_price,
                "leverage": p.leverage,
                "marginMode": p.margin_mode,
                "timestamp": p.timestamp * 1000,
            }
            for p in positions
            if symbols is None or p.symbol in symbols
        ]

    async def set_leverage(self, leverage: int, symbol: str | None = None, params=None) -> dict:
        if symbol is None:
            raise ValueError("Arcus: symbol is required to set leverage")
        instrument = self._market(symbol)
        if (
            isinstance(leverage, bool)
            or not isinstance(leverage, int)
            or leverage < 1
            or (instrument.max_leverage and leverage > instrument.max_leverage)
        ):
            raise ValueError(f"Arcus:{symbol}: invalid leverage")
        if params:
            raise ValueError(f"Arcus:{symbol}: set_leverage does not change margin mode")
        body = {
            "address": self.master_wallet_address,
            "accountIndex": self.account_index,
            "marketId": self._market_id(instrument),
            "leverage": leverage,
        }
        timestamp = time.time_ns()
        if not self.api_key or not self._signer:
            raise ValueError("Arcus: signing credentials are required to set leverage")
        response = await self._request(
            "POST",
            "/v1/setLeverage",
            params=self._account_query(),
            json=body,
            headers={
                "X-API-Key": self.api_key,
                "X-Timestamp": str(timestamp),
                "X-Signature": self._signer.sign_legacy(timestamp, "setLeverage", body),
            },
        )
        if response.get("status") not in {"APPLIED", "ACK"}:
            raise ValueError(f"Arcus:{symbol}: leverage change was rejected or unknown")
        for _ in range(3):
            actual = await self._read_leverage(instrument)
            if float(actual["leverage"]) == leverage:
                return response
            await asyncio.sleep(0.1)
        raise ValueError(f"Arcus:{symbol}: requested leverage has not been confirmed")

    def order_capabilities(self, instrument: Instrument) -> OrderCapabilities:
        if (
            instrument.venue != self.name
            or instrument.network is not self.network_type
            or instrument.market_type != "perp"
            or instrument.is_inverse
            or instrument.contract_size != 1
            or instrument.listing_status != "trading"
        ):
            return OrderCapabilities()
        return OrderCapabilities(True, ("GTC", "IOC", "FOK"), True)

    def _signed_headers(self, payload: dict[str, Any], action: str | None = None) -> dict[str, str]:
        if not self.api_key or not self._signer:
            raise ValueError("Arcus api_key and api_signing_key are required for authenticated operations")
        timestamp = int(payload.get("ct", time.time_ns()))
        signature = (
            self._signer.sign_payload(payload)
            if action is None
            else self._signer.sign_legacy(timestamp, action, payload)
        )
        return {"X-API-Key": self.api_key, "X-Timestamp": str(timestamp), "X-Signature": signature}

    def _sign_order_payload(self, payload: dict[str, Any]) -> str:
        if self._signer is None:
            raise ValueError("Arcus api_signing_key is required for signing")
        return self._signer.sign_payload(payload)

    def _build_place_order_payload(self, **kwargs: Any) -> dict[str, Any]:
        return build_place_order_payload(**kwargs)

    def price_tick(self, symbol: str, price: float) -> float:
        """Return the accepted price grid; ordersign still uses the base tickSize."""
        market = self._market(symbol)
        tiers = self._market_meta[symbol].get("tickTiers")
        if not tiers:
            return market.price_step
        for tier in tiers:
            ceiling = tier.get("upToPrice")
            if ceiling is None or Decimal(str(price)) < Decimal(str(ceiling)):
                return _arcus_number(tier.get("tick"), "price tier tick", positive=True)
        raise ValueError(f"Arcus:{symbol}: price lies outside known tick tiers")

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        market = self._market(symbol)
        options = params or {}
        timestamp = time.time_ns()
        client_id = options.get("clientOrderId") or options.get("clientId")
        order_type_upper = order_type.upper()
        side_upper = side.upper()
        self._account_query()
        if order_type_upper not in {"LIMIT", "MARKET"} or side_upper not in {"BUY", "SELL"}:
            raise ValueError("Arcus: unsupported order type or side")
        _arcus_number(amount, "order quantity", positive=True)
        _arcus_number(price, "protective order price", positive=True)
        _integer_step(price, self.price_tick(symbol, price))
        if client_id is not None and (
            not isinstance(client_id, str) or not client_id.isascii() or not 1 <= len(client_id) <= 36
        ):
            raise ValueError("Arcus: client order ID must contain 1 to 36 ASCII characters")
        tif = str(options.get("timeInForce", "GTC")).upper()
        tif_code = {"GTC": 0, "GTT": 0, "FOK": 1, "IOC": 2, "ALO": 3}.get(tif)
        if tif_code is None:
            raise ValueError(f"Arcus time-in-force is unsupported: {tif}")
        if order_type_upper == "MARKET" and tif != "IOC":
            raise ValueError("Arcus: market orders require IOC and a protective price")
        wire_tif = "GTT" if tif in ("GTC", "GTT") else tif
        # Arcus requires goodTilTime even for IOC/FOK. The signed g field is
        # nanoseconds; the REST body carries goodTilTime in microseconds.
        good_til_us = int(options.get("goodTilTime", (timestamp + int(31 * 24 * 60 * 60 * 1e9)) // 1000))
        payload = self._build_place_order_payload(
            address=str(self.master_wallet_address),
            account_index=self.account_index,
            timestamp_ns=timestamp,
            expiry=good_til_us * 1000,
            market_id=self._market_id(market),
            operation=1,
            price_ticks=_integer_step(price, market.price_step) if price is not None else 0,
            quantity_quanta=_integer_step(amount, market.qty_step),
            reduce_only=bool(options.get("reduceOnly")),
            side_code=0 if side_upper == "BUY" else 1,
            order_type_code=tif_code,
            client_order_id=client_id,
        )
        body = {
            "address": self.master_wallet_address,
            "accountIndex": self.account_index,
            "marketId": payload["m"],
            "quantity": format(Decimal(str(amount)), "f"),
            "orderSide": side_upper,
            "orderType": order_type_upper,
            "price": format(Decimal(str(price)), "f"),
            "clientId": client_id,
            "reduceOnly": bool(options.get("reduceOnly")),
            "timeInForce": wire_tif,
            "goodTilTime": str(good_til_us),
            "timestamp": timestamp,
            "signature": self._sign_order_payload(payload) if self._signer else None,
        }
        response = await self._request(
            "POST",
            "/v1/placeOrder",
            params={"address": self.master_wallet_address},
            json=body,
            headers=self._signed_headers(payload),
        )
        self.invalidate_balance_cache()
        return self._normalize_order(response, symbol)

    async def cancel_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> bool:
        market = self._market(symbol) if symbol else None
        options = params or {}
        self._account_query()
        if market is None and "marketId" not in options:
            raise ValueError("Arcus: cancel requires a market identity")
        timestamp = time.time_ns()
        payload = {
            "ad": str(self.master_wallet_address).lower(),
            "ai": self.account_index,
            "ct": timestamp,
            "m": self._market_id(market) if market else int(options.get("marketId", 0)),
            "op": 2,
            "v": 1,
        }
        client_id = options.get("clientOrderId", options.get("clientId"))
        if client_id:
            payload["c"] = client_id
            target = {"kind": "clientId", "clientId": client_id}
        else:
            payload["id"] = str(id)
            target = {"kind": "orderId", "orderId": str(id)}
        await self._request(
            "POST",
            "/v1/cancelOrder",
            params={"address": self.master_wallet_address},
            json={
                "address": self.master_wallet_address,
                "marketId": payload["m"],
                **target,
                "accountIndex": self.account_index,
            },
            headers=self._signed_headers(payload),
        )
        # Arcus cancellation is asynchronous; a successful HTTP response is
        # an acknowledgement, while the terminal state arrives via WS/query.
        return True

    async def fetch_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> dict:
        query = self._account_query(params)
        response = await self._request("GET", f"/v1/order/{id}", params=query, authenticated=True)
        order = self._normalize_order(response, symbol)
        if str(order.get("id")) != str(id):
            raise ValueError("Arcus: order query returned a different order")
        if ("address" in order and str(order["address"]).lower() != str(self.master_wallet_address).lower()) or (
            "accountIndex" in order and order["accountIndex"] != self.account_index
        ):
            raise ValueError("Arcus: order response belongs to another account")
        return order

    async def _account_rows(
        self, endpoint: str, key: str, symbol=None, since=None, limit=None, params=None
    ) -> list[dict]:
        query = self._account_query(params)
        query.pop("clientOrderId", None)
        query.pop("clientId", None)
        if symbol:
            query["market"] = self._market(symbol).venue_symbol
        if since is not None:
            query["from"] = int(Decimal(str(since)) * 1000)
        query["limit"] = limit or query.get("limit", 1000)
        if not isinstance(query["limit"], int) or not 1 <= query["limit"] <= 1000:
            raise ValueError("Arcus: history limit must be between 1 and 1000")
        response = await self._request("GET", endpoint, params=query)
        rows = response.get(key) if isinstance(response, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Arcus: {key} response is incomplete")
        total = response.get("total")
        if (total is not None and (not isinstance(total, int) or total != len(rows))) or (
            total is None and len(rows) >= query["limit"]
        ):
            raise ValueError(f"Arcus: {key} history is truncated; use a narrower time window")
        for row in rows:
            if ("address" in row and str(row["address"]).lower() != str(self.master_wallet_address).lower()) or (
                "accountIndex" in row and row["accountIndex"] != self.account_index
            ):
                raise ValueError(f"Arcus: {key} response belongs to another account")
            if symbol and row.get("marketDisplayName", symbol) != symbol:
                raise ValueError(f"Arcus: {key} response belongs to another market")
        return rows

    async def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None) -> list[dict]:
        rows = await self._account_rows("/v1/openOrders", "orders", symbol, since, limit, params)
        return [self._normalize_order(row, symbol) for row in rows]

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None) -> list[dict]:
        rows = await self._account_rows("/v1/fills", "fills", symbol, since, limit, params)
        fills = {}
        for row in rows:
            fill = self._normalize_fill(row, symbol)
            if fill["id"] is None:
                raise ValueError("Arcus: fill is missing its trade ID")
            if fill["id"] in fills and fills[fill["id"]] != fill:
                raise ValueError("Arcus: conflicting duplicate trade ID")
            fills[fill["id"]] = fill
        return list(fills.values())

    async def submit_order(self, request: OrderRequest, instrument: Instrument) -> OrderSnapshot:
        snapshot = await super().submit_order(request, instrument)
        if snapshot.order_id and snapshot.filled_qty_native is not None and snapshot.filled_qty_native > 0:
            return await self.fetch_order_snapshot(request, instrument, snapshot.order_id)
        return snapshot

    async def fetch_order_snapshot(
        self, request: OrderRequest, instrument: Instrument, order_id: str | None = None
    ) -> OrderSnapshot:
        order = (
            await self.fetch_order(order_id, request.symbol)
            if order_id is not None
            else await self.fetch_order_by_client_id(request.client_order_id, request.symbol)
        )
        if order.get("id") and order.get("filled") is not None and order["filled"] > 0:
            trades = await self.fetch_my_trades(request.symbol)
            order["trades"] = [trade for trade in trades if str(trade.get("orderId")) == str(order["id"])]
        return parse_order_snapshot(order, instrument)

    async def fetch_order_by_client_id(
        self, client_order_id: str, symbol: str | None = None, params: dict | None = None
    ) -> dict:
        """Resolve Arcus' client ID through the account order history endpoint."""
        rows = await self._account_rows("/v1/orders", "orders", symbol, params=params)
        matches = [row for row in rows if str(row.get("clientId", row.get("clientOrderId"))) == str(client_order_id)]
        if len(matches) > 1:
            raise ValueError("Arcus: client order ID matches multiple historical orders")
        if matches:
            return self._normalize_order(matches[0], symbol)
        # Keep the ambiguity explicit.  Callers must not interpret a missing
        # asynchronous order as a cancellation.
        return {
            "id": None,
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "status": "unknown",
            "filled": None,
            "average": None,
        }

    async def connect_websocket(self) -> bool:
        if self._websocket is not None:
            return True
        try:
            import websockets

            self._ws_closing = False
            self._websocket = await websockets.connect(self.websocket_url)
            if self._ws_queue is None:
                self._ws_queue = asyncio.Queue()
            self._ws_reader_task = asyncio.create_task(self._read_ws())
            return True
        except Exception as exc:  # pragma: no cover - network dependent
            self.logger.warning("Arcus WebSocket connection failed: %s", exc)
            return False

    async def subscribe_orderbook(self, symbol: str, params: dict | None = None):
        if self._websocket is None:
            await self.connect_websocket()
        if self._websocket is None:
            raise RuntimeError("Arcus WebSocket is not connected")
        market = self._market(symbol).venue_symbol
        message = {"type": "subscribe", "channel": "l2Orderbook", "id": market}
        message.update(
            {k: v for k, v in (params or {}).items() if k in ("nLevels", "sigFigs", "roundStep", "snapshot")}
        )
        self._subscriptions[f"l2Orderbook:{market}"] = message
        await self._websocket.send(json.dumps(message, separators=(",", ":")))

    async def subscribe_orderbook_updates(self, symbol: str, params: dict | None = None) -> None:
        if self._websocket is None and not await self.connect_websocket():
            raise RuntimeError("Arcus WebSocket is not connected")
        market = self._market(symbol).venue_symbol
        key = f"l2OrderbookUpdates:{market}"
        force = bool((params or {}).get("_force_resubscribe"))
        if key in self._subscriptions and not force:
            return
        message = {"type": "subscribe", "channel": "l2OrderbookUpdates", "id": market}
        message.update({k: v for k, v in (params or {}).items() if k in ("nLevels", "snapshot")})
        self._subscriptions[key] = message
        self._orderbook_needs_snapshot.add(market)
        await self._websocket.send(json.dumps(message, separators=(",", ":")))

    async def subscribe_orders(self, symbol: str | None = None) -> None:
        """Subscribe to Arcus account order updates on the shared socket."""
        if not self.master_wallet_address:
            raise ValueError("Arcus master_wallet_address is required for order updates")
        if self._websocket is None and not await self.connect_websocket():
            raise RuntimeError("Arcus WebSocket is not connected")
        key = f"orders:{symbol or '*'}"
        if key in self._subscriptions:
            return
        message = {
            "type": "subscribe",
            "channel": "orders",
            "id": self.master_wallet_address,
            "accountIndex": self.account_index,
        }
        if symbol:
            message["market"] = self._markets_by_symbol.get(symbol, self._markets_by_symbol.get(symbol.upper()))
            message["market"] = message["market"].venue_symbol if isinstance(message["market"], Instrument) else symbol
        self._subscriptions[key] = message
        await self._websocket.send(json.dumps(message, separators=(",", ":")))

    async def subscribe_user_fills(self, symbol: str | None = None, params: dict | None = None) -> None:
        if not self.master_wallet_address:
            raise ValueError("Arcus master_wallet_address is required for user fills")
        if self._websocket is None and not await self.connect_websocket():
            raise RuntimeError("Arcus WebSocket is not connected")
        key = f"userFills:{symbol or '*'}"
        if key in self._subscriptions:
            return
        message = {
            "type": "subscribe",
            "channel": "userFills",
            "id": self.master_wallet_address,
            "accountIndex": self.account_index,
            "snapshot": True,
        }
        if symbol:
            message["market"] = self._market(symbol).venue_symbol
        message.update({k: v for k, v in (params or {}).items() if k in ("snapshot", "nFills", "market")})
        self._subscriptions[key] = message
        await self._websocket.send(json.dumps(message, separators=(",", ":")))

    async def _next_ws_event(self, channel: str) -> dict:
        queue = self._ws_queues.get(channel)
        if queue is None:
            queue = self._ws_queues.setdefault(channel, asyncio.Queue())
        # Tests and older callers may seed the compatibility queue directly.
        while True:
            if self._ws_queue is not None and not self._ws_queue.empty():
                event = await self._ws_queue.get()
            else:
                event = await queue.get()
            if event.get("channel") in (None, channel):
                if channel in {"orders", "userFills"} and (
                    ("id" in event and str(event["id"]).lower() != str(self.master_wallet_address).lower())
                    or ("accountIndex" in event and event["accountIndex"] != self.account_index)
                ):
                    raise ValueError("Arcus: account stream belongs to another account")
                return event

    async def watch_orders(self, symbol: str | None = None, params: dict | None = None) -> dict:
        await self.subscribe_orders(symbol)
        while True:
            if not self._pending_orders.empty():
                return self._normalize_order(await self._pending_orders.get(), symbol)
            update = await self._next_ws_event("orders")
            contents = update.get("contents", update)
            if isinstance(contents, dict) and "orders" in contents:
                contents = contents["orders"]
            for order in contents if isinstance(contents, list) else [contents]:
                await self._pending_orders.put(order)

    async def watch_user_fills(self, symbol: str | None = None, params: dict | None = None) -> dict:
        await self.subscribe_user_fills(symbol, params)
        while True:
            if not self._pending_user_fills.empty():
                fill = await self._pending_user_fills.get()
                trade_id = fill.get("tradeId")
                if trade_id is not None:
                    trade_id = str(trade_id)
                    if trade_id in self._seen_trade_ids:
                        continue
                    self._seen_trade_ids.add(trade_id)
                return fill
            update = await self._next_ws_event("userFills")
            contents = update.get("contents", update)
            if isinstance(contents, dict) and "fills" in contents:
                fills = contents.get("fills") or []
                if contents.get("isSnapshot") and not fills:
                    continue
                contents = fills
            for row in contents if isinstance(contents, list) else [contents]:
                await self._pending_user_fills.put(self._normalize_fill(row, symbol or update.get("market")))

    async def watch_order_book(self, symbol: str, limit: int | None = None, params: dict | None = None) -> dict:
        options = dict(params or {})
        if limit is not None:
            options.setdefault("nLevels", limit)
        await self.subscribe_orderbook_updates(symbol, options)
        market = self._market(symbol).venue_symbol
        while True:
            event = await self._next_ws_event("l2OrderbookUpdates")
            contents = event.get("contents", event)
            if not isinstance(contents, dict):
                continue
            is_snapshot = event.get("type") == "subscribed" or contents.get("isSnapshot") is True
            if market in self._orderbook_needs_snapshot and not is_snapshot:
                continue
            if is_snapshot:
                self._orderbook_needs_snapshot.discard(market)
            sequence = contents.get("lastSequenceId")
            if sequence is not None:
                sequence = int(sequence)
                previous = self._orderbook_sequences.get(market)
                if event.get("type") == "subscribed" or previous is None:
                    self._orderbook_sequences[market] = sequence
                    self._orderbook_books[market] = self._normalize_orderbook(contents)
                elif sequence <= previous:
                    continue
                elif sequence != previous + 1:
                    self._orderbook_sequences.pop(market, None)
                    self._orderbook_books.pop(market, None)
                    await self.subscribe_orderbook_updates(market, {**options, "_force_resubscribe": True})
                    continue
                else:
                    self._orderbook_sequences[market] = sequence
                    book = self._orderbook_books.setdefault(market, {"bids": [], "asks": []})
                    self._apply_orderbook_delta(book, contents)
            else:
                self._orderbook_books[market] = self._normalize_orderbook(contents)
            result = dict(self._orderbook_books.get(market, self._normalize_orderbook(contents)))
            result["symbol"] = market
            if sequence is not None:
                result["lastSequenceId"] = int(sequence)
            return result

    async def _read_ws(self) -> None:
        try:
            while self._websocket is not None:
                raw = await self._websocket.recv()
                message = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                channel = message.get("channel")
                contents = message.get("contents", message.get("data", message))
                rows = contents if isinstance(contents, list) else [contents]
                queue = self._ws_queues.setdefault(channel or "_unknown", asyncio.Queue())
                for row in rows:
                    if isinstance(row, dict):
                        event = {
                            "channel": channel,
                            "type": message.get("type"),
                            "id": message.get("id"),
                            "market": message.get("market"),
                            "accountIndex": message.get("accountIndex"),
                            "contents": row,
                        }
                        await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("Arcus WebSocket reader stopped: %s", exc)
            if not self._ws_closing and self._subscriptions and self._ws_reconnect_task is None:
                self._ws_reconnect_task = asyncio.create_task(self._reconnect_ws())

    async def _reconnect_ws(self) -> None:
        delay = 0.25
        # Preserve queue objects: active consumers may already be awaiting them.
        # Only order-book data expires on disconnect; account events remain facts.
        self._orderbook_sequences.clear()
        self._orderbook_books.clear()
        for message in self._subscriptions.values():
            if message.get("channel") == "l2OrderbookUpdates":
                self._orderbook_needs_snapshot.add(message["id"])
        for channel in ("l2Orderbook", "l2OrderbookUpdates"):
            queue = self._ws_queues.get(channel)
            if queue is not None:
                while not queue.empty():
                    queue.get_nowait()
        try:
            while not self._ws_closing:
                try:
                    if self._websocket is not None:
                        await self._websocket.close()
                    import websockets

                    self._websocket = await websockets.connect(self.websocket_url)
                    for message in self._subscriptions.values():
                        await self._websocket.send(json.dumps(message, separators=(",", ":")))
                    self._ws_reader_task = asyncio.create_task(self._read_ws())
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.logger.warning("Arcus WebSocket reconnect failed: %s", exc)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 5.0)
        finally:
            self._ws_reconnect_task = None

    async def close(self) -> None:
        self._ws_closing = True
        if self._ws_reconnect_task is not None:
            self._ws_reconnect_task.cancel()
            await asyncio.gather(self._ws_reconnect_task, return_exceptions=True)
            self._ws_reconnect_task = None
        if self._ws_reader_task is not None:
            self._ws_reader_task.cancel()
            await asyncio.gather(self._ws_reader_task, return_exceptions=True)
            self._ws_reader_task = None
        await super().close()

    async def _request(self, method: str, endpoint: str, *, authenticated: bool = False, **kwargs) -> dict:
        session = await self._ensure_session()
        headers = dict(kwargs.pop("headers", {}) or {})
        if authenticated:
            headers.setdefault("X-API-Key", self.api_key or "")
        headers.setdefault("Accept", "application/json")
        url = f"{self.rest_base_url}{endpoint}"
        try:
            async with session.request(method, url, headers=headers, **kwargs) as response:
                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError):
                    data = {"error": await response.text()}
                if response.status >= 400:
                    raise ArcusError(
                        str(data.get("error", data)), endpoint=endpoint, code=str(data.get("code", response.status))
                    )
                return data if isinstance(data, dict) else {"data": data}
        except aiohttp.ClientError as exc:
            raise ArcusError(str(exc), endpoint=endpoint) from exc

    def _market_id(self, market: Instrument | None) -> int:
        if market is None:
            raise ValueError("Arcus market is required")
        for market_id, candidate in self._markets_by_id.items():
            if candidate == market:
                return market_id
        row = self._market_meta.get(market.venue_symbol, {})
        if row.get("marketId") is None:
            raise ValueError(f"Arcus market ID unavailable: {market.venue_symbol}")
        return int(row["marketId"])

    @staticmethod
    def _normalize_order(response: dict[str, Any], symbol: str | None = None) -> dict:
        order = response.get("order", response)
        if not isinstance(order, dict):
            raise ValueError("Arcus: invalid order response")
        if symbol and order.get("marketDisplayName", symbol) != symbol:
            raise ValueError("Arcus: order belongs to another market")
        status_map = {
            "OPEN": "open",
            "PLACED": "open",
            "PARTIALLY_FILLED": "open",
            "FILLED": "closed",
            "CANCELED": "canceled",
            "CANCELLED": "canceled",
            "REJECTED": "rejected",
            "EXPIRED": "expired",
        }
        status = str(order.get("state", order.get("status", "unknown"))).upper()
        wire_status = str(order.get("status", "")).upper()
        # IOC's PARTIALLY_FILLED state is terminal after the remainder is canceled.
        if wire_status in {"CANCELED", "CANCELLED", "MARGIN_CANCELED", "EXPIRED", "REJECTED"}:
            status = "CANCELED" if wire_status == "MARGIN_CANCELED" else wire_status
        elif status == "PARTIALLY_FILLED" and order.get("timeInForce") == "IOC":
            status = "CANCELED"
        if order.get("cancelReason") == "MODIFY_CANCELED":
            status = "UNKNOWN"
        filled = order.get("filled", order.get("filledSize", order.get("filledQuantity", order.get("executedQty"))))
        average = order.get("average", order.get("avgFillPrice", order.get("avgPrice", order.get("averagePrice"))))
        if filled is None and order.get("originalSize") is not None and order.get("remainingSize") is not None:
            original = Decimal(str(_arcus_number(order["originalSize"], "original size")))
            remaining = Decimal(str(_arcus_number(order["remainingSize"], "remaining size")))
            if remaining < 0 or original < remaining:
                raise ValueError("Arcus: inconsistent order remaining quantity")
            filled = original - remaining
        filled = _arcus_number(filled, "filled quantity") if filled is not None else None
        if filled is not None and filled < 0:
            raise ValueError("Arcus: negative filled quantity")
        result = dict(order)
        result.update(
            {
                "id": order.get("id", order.get("orderId")),
                "clientOrderId": order.get("clientId", order.get("clientOrderId")),
                "symbol": symbol or order.get("marketDisplayName", order.get("symbol")),
                "status": status_map.get(status, "unknown"),
                "filled": filled,
                "average": _arcus_number(average, "average fill price", positive=True)
                if average is not None and filled
                else None,
            }
        )
        return result

    @staticmethod
    def _normalize_fill(fill: dict[str, Any], symbol: str | None = None) -> dict:
        if not isinstance(fill, dict):
            raise ValueError("Arcus: invalid fill response")
        if symbol and fill.get("marketDisplayName", symbol) != symbol:
            raise ValueError("Arcus: fill belongs to another market")
        fee = fill.get("fee")
        fee = {"cost": _arcus_number(fee, "fill fee"), "currency": "USD"} if fee is not None else None
        realized = fill.get("closedPnl")
        if realized is None and fill.get("positionEffect") in {"OPEN_LONG", "OPEN_SHORT", "ADD_LONG", "ADD_SHORT"}:
            realized = 0
        if (fill.get("liquidation") or {}).get("method") == "LIQUIDATION":
            # The engine's zero is a placeholder; the actual loss is on the account update.
            realized = None
        info = dict(fill)
        if realized is not None:
            info["realizedPnl"] = _arcus_number(realized, "closed PnL")
            info["marginAsset"] = "USD"
        result = dict(fill)
        result.update(
            {
                "id": str(fill.get("tradeId")) if fill.get("tradeId") is not None else None,
                "tradeId": str(fill.get("tradeId")) if fill.get("tradeId") is not None else None,
                "orderId": fill.get("orderId") or fill.get("takerOrderId") or fill.get("makerOrderId"),
                "symbol": symbol or fill.get("marketDisplayName"),
                "price": _arcus_number(fill.get("price"), "fill price", positive=True),
                "amount": _arcus_number(fill.get("size", fill.get("amount")), "fill size", positive=True),
                "timestamp": _arcus_number(fill["createdAt"], "fill timestamp") / 1000
                if fill.get("createdAt") is not None
                else None,
                "side": str(fill.get("side", "")).lower() or None,
                "order": fill.get("orderId") or fill.get("takerOrderId") or fill.get("makerOrderId"),
                "fee": fee,
                "info": info,
            }
        )
        return result

    @staticmethod
    def _apply_orderbook_delta(book: dict[str, list[list[float]]], delta: dict[str, Any]) -> None:
        for side in ("bids", "asks"):
            levels = {float(price): float(size) for price, size in book.get(side, [])}
            for level in delta.get(side, []) or []:
                if isinstance(level, dict):
                    price = _decimal(level.get("price", level.get("p")))
                    size = _decimal(level.get("size", level.get("quantity", level.get("q"))))
                else:
                    price, size = _decimal(level[0]), _decimal(level[1])
                if size == 0:
                    levels.pop(price, None)
                else:
                    levels[price] = size
            ordered = sorted(levels.items(), key=lambda item: item[0], reverse=side == "bids")
            book[side] = [[price, size] for price, size in ordered]
