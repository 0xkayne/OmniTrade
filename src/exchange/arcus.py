"""Native Arcus exchange adapter.

Arcus is not currently supported by ccxt.  This module keeps Arcus-specific
transport, authentication and field mapping at the exchange boundary while
exposing the project's :class:`BaseExchange` contract.
"""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from src.exchange.base import BaseExchange
from src.exchange.order import OrderCapabilities
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

    def __init__(self, private_key: str | bytes):
        if Ed25519PrivateKey is None:
            raise RuntimeError("Arcus signing requires the 'cryptography' package")
        if isinstance(private_key, str):
            value = private_key.removeprefix("0x")
            try:
                private_key = bytes.fromhex(value)
            except ValueError as exc:
                raise ValueError("Arcus private key must be hex encoded") from exc
        if len(private_key) != 32:
            raise ValueError("Arcus Ed25519 private key must contain 32 bytes")
        self._key = Ed25519PrivateKey.from_private_bytes(private_key)

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


def build_place_order_payload(
    *, address: str, account_index: int, market_id: int, timestamp_ns: int,
    price_ticks: int, quantity_quanta: int, side_code: int, order_type_code: int,
    expiry: int, reduce_only: bool = False, client_order_id: str | None = None,
    operation: int = 1,
) -> dict[str, Any]:
    """Build the typed ordersign payload used by ``placeOrder``."""
    payload: dict[str, Any] = {
        "ad": address.lower(), "ai": int(account_index), "ct": int(timestamp_ns),
        "g": int(expiry), "m": int(market_id), "op": int(operation),
        "p": int(price_ticks), "q": int(quantity_quanta), "r": int(bool(reduce_only)),
        "s": int(side_code), "t": int(order_type_code), "v": 1,
    }
    if client_order_id:
        payload["c"] = str(client_order_id)
    return payload


class ArcusExchange(BaseExchange):
    """Native Arcus REST/WebSocket adapter."""

    supports_user_fills = True

    def __init__(self, name: str, config: dict, secrets: dict):
        super().__init__(name, config, secrets)
        self.api_key = secrets.get("api_key") or secrets.get("apiKey")
        self.address = secrets.get("address") or secrets.get("wallet_address")
        private_key = secrets.get("private_key") or secrets.get("privateKey")
        self.account_index = int(config.get("options", {}).get("account_index", 0))
        self._signer = ArcusSigner(private_key) if private_key else None
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
        self._seen_trade_ids: set[str] = set()

    async def connect(self) -> None:
        await self._load_markets()

    async def _load_markets(self) -> None:
        response = await self._request("GET", "/v1/markets")
        rows = response.get("markets", response if isinstance(response, list) else [])
        for row in rows:
            instrument = self._parse_market(row)
            if instrument is None:
                continue
            self._markets_by_symbol[instrument.venue_symbol] = instrument
            market_id = int(row.get("marketId"))
            self._markets_by_id[market_id] = instrument
            self._market_meta[instrument.venue_symbol] = row

    def _parse_market(self, row: dict[str, Any]) -> Instrument | None:
        if not isinstance(row, dict):
            return None
        status = str(row.get("status", "ONLINE")).upper()
        base = str(row.get("baseAsset") or "").upper()
        quote = str(row.get("quoteAsset") or "USD").upper()
        symbol = str(row.get("marketDisplayName") or (f"{base}-{quote}" if base else ""))
        if not base or not symbol:
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
        return {"symbol": symbol, "bid": _decimal(bid) if bid is not None else None,
                "ask": _decimal(ask) if ask is not None else None, "info": response}

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
        query = {"address": self.address, "accountIndex": self.account_index, **(params or {})}
        response = await self._request("GET", "/v1/account", params=query, authenticated=True)
        if "free" in response:
            return response
        balances = response.get("balances", response.get("collateral", {}))
        if isinstance(balances, list):
            free = {str(item.get("asset", "USD")): _decimal(item.get("available", item.get("free"))) for item in balances}
        elif isinstance(balances, dict) and balances:
            free = {str(k): _decimal(v.get("available", v) if isinstance(v, dict) else v) for k, v in balances.items()}
        else:
            free_value = response.get("freeCollateral", response.get("equity"))
            free = {"USD": _decimal(free_value)} if free_value is not None else {}
        return {"free": free, "total": free, "info": response}

    def order_capabilities(self, instrument: Instrument) -> OrderCapabilities:
        if (instrument.venue != self.name or instrument.network is not self.network_type
                or instrument.market_type != "perp" or instrument.is_inverse
                or instrument.contract_size != 1 or instrument.listing_status != "trading"):
            return OrderCapabilities()
        return OrderCapabilities(True, ("GTC", "IOC", "FOK"))

    def _signed_headers(self, payload: dict[str, Any], action: str | None = None) -> dict[str, str]:
        if not self.api_key or not self._signer:
            raise ValueError("Arcus API key and private key are required for authenticated operations")
        timestamp = int(payload.get("ct", time.time_ns()))
        signature = self._signer.sign_payload(payload) if action is None else self._signer.sign_legacy(timestamp, action, payload)
        return {"X-API-Key": self.api_key, "X-Timestamp": str(timestamp), "X-Signature": signature}

    def _sign_order_payload(self, payload: dict[str, Any]) -> str:
        if self._signer is None:
            raise ValueError("Arcus private key is required for signing")
        return self._signer.sign_payload(payload)

    def _build_place_order_payload(self, **kwargs: Any) -> dict[str, Any]:
        return build_place_order_payload(**kwargs)

    async def create_order(
        self, symbol: str, order_type: str, side: str, amount: float, price: float | None = None, params: dict | None = None
    ) -> dict:
        market = self._market(symbol)
        options = params or {}
        timestamp = time.time_ns()
        client_id = options.get("clientOrderId") or options.get("clientId")
        order_type_upper = order_type.upper()
        side_upper = side.upper()
        tif = str(options.get("timeInForce", "GTC")).upper()
        tif_code = {"GTC": 0, "GTT": 0, "FOK": 1, "IOC": 2, "ALO": 3}.get(tif)
        if tif_code is None:
            raise ValueError(f"Arcus time-in-force is unsupported: {tif}")
        wire_tif = "GTT" if tif in ("GTC", "GTT") else tif
        # Arcus requires goodTilTime even for IOC/FOK. The signed g field is
        # nanoseconds; the REST body carries goodTilTime in microseconds.
        good_til_us = int(options.get("goodTilTime", (timestamp + int(31 * 24 * 60 * 60 * 1e9)) // 1000))
        payload = self._build_place_order_payload(
            address=str(self.address), account_index=self.account_index, timestamp_ns=timestamp,
            expiry=good_til_us * 1000, market_id=self._market_id(market),
            operation=1, price_ticks=_integer_step(price, market.price_step) if price is not None else 0,
            quantity_quanta=_integer_step(amount, market.qty_step), reduce_only=bool(options.get("reduceOnly")),
            side_code=0 if side_upper == "BUY" else 1, order_type_code=tif_code,
            client_order_id=client_id,
        )
        body = {"address": self.address, "accountIndex": self.account_index, "marketId": payload["m"],
                "quantity": str(amount), "orderSide": side_upper, "orderType": order_type_upper,
                "price": str(price) if price is not None else None, "clientId": client_id,
                "reduceOnly": bool(options.get("reduceOnly")), "timeInForce": wire_tif, "goodTilTime": str(good_til_us),
                "timestamp": timestamp, "signature": self._sign_order_payload(payload) if self._signer else None}
        response = await self._request("POST", "/v1/placeOrder", params={"address": self.address}, json=body,
                                       headers=self._signed_headers(payload))
        self.invalidate_balance_cache()
        return self._normalize_order(response, symbol)

    async def cancel_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> bool:
        market = self._market(symbol) if symbol else None
        options = params or {}
        timestamp = time.time_ns()
        payload = {"ad": str(self.address).lower(), "ai": self.account_index, "ct": timestamp,
                   "m": self._market_id(market) if market else int(options.get("marketId", 0)), "op": 2, "v": 1}
        if options.get("clientOrderId"):
            payload["c"] = options["clientOrderId"]
        else:
            payload["id"] = str(id)
        await self._request("POST", "/v1/cancelOrder", params={"address": self.address},
                            json={"address": self.address, "marketId": payload["m"], "orderId": id,
                                  "accountIndex": self.account_index}, headers=self._signed_headers(payload))
        # Arcus cancellation is asynchronous; a successful HTTP response is
        # an acknowledgement, while the terminal state arrives via WS/query.
        return True

    async def fetch_order(self, id: str, symbol: str | None = None, params: dict | None = None) -> dict:
        query = {"address": self.address, "accountIndex": self.account_index, **(params or {})}
        response = await self._request("GET", f"/v1/order/{id}", params=query, authenticated=True)
        return self._normalize_order(response, symbol)

    async def fetch_order_by_client_id(
        self, client_order_id: str, symbol: str | None = None, params: dict | None = None
    ) -> dict:
        """Resolve Arcus' client ID through the account order history endpoint."""
        query = {
            "address": self.address,
            "accountIndex": self.account_index,
            "limit": 1000,
            **(params or {}),
        }
        query.pop("clientOrderId", None)
        query.pop("clientId", None)
        if symbol:
            query.setdefault("market", self._market(symbol).venue_symbol)
        response = await self._request("GET", "/v1/orders", params=query)
        rows = response.get("orders", response.get("data", response))
        if isinstance(rows, dict):
            rows = rows.get("orders", [])
        for row in rows if isinstance(rows, list) else []:
            candidate = row.get("clientId", row.get("clientOrderId"))
            if candidate is not None and str(candidate) == str(client_order_id):
                return self._normalize_order(row, symbol)
        # Keep the ambiguity explicit.  Callers must not interpret a missing
        # asynchronous order as a cancellation.
        return {"id": None, "clientOrderId": client_order_id, "symbol": symbol,
                "status": "unknown", "filled": 0.0, "average": None}

    async def connect_websocket(self) -> bool:
        if self._websocket is not None:
            return True
        try:
            import websockets

            self._ws_closing = False
            self._websocket = await websockets.connect(self.websocket_url)
            self._ws_queue = asyncio.Queue()
            self._ws_queues = {}
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
        message.update({k: v for k, v in (params or {}).items() if k in ("nLevels", "sigFigs", "roundStep", "snapshot")})
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
        await self._websocket.send(json.dumps(message, separators=(",", ":")))

    async def subscribe_orders(self, symbol: str | None = None) -> None:
        """Subscribe to Arcus account order updates on the shared socket."""
        if not self.address:
            raise ValueError("Arcus address is required for order updates")
        if self._websocket is None and not await self.connect_websocket():
            raise RuntimeError("Arcus WebSocket is not connected")
        key = f"orders:{symbol or '*'}"
        if key in self._subscriptions:
            return
        message = {"type": "subscribe", "channel": "orders", "id": self.address,
                   "accountIndex": self.account_index}
        if symbol:
            message["market"] = self._markets_by_symbol.get(symbol, self._markets_by_symbol.get(symbol.upper()))
            message["market"] = message["market"].venue_symbol if isinstance(message["market"], Instrument) else symbol
        self._subscriptions[key] = message
        await self._websocket.send(json.dumps(message, separators=(",", ":")))

    async def subscribe_user_fills(self, symbol: str | None = None, params: dict | None = None) -> None:
        if not self.address:
            raise ValueError("Arcus address is required for user fills")
        if self._websocket is None and not await self.connect_websocket():
            raise RuntimeError("Arcus WebSocket is not connected")
        key = f"userFills:{symbol or '*'}"
        if key in self._subscriptions:
            return
        message = {"type": "subscribe", "channel": "userFills", "id": self.address,
                   "accountIndex": self.account_index, "snapshot": True}
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
                return event

    async def watch_orders(self, symbol: str | None = None, params: dict | None = None) -> dict:
        await self.subscribe_orders(symbol)
        while True:
            update = await self._next_ws_event("orders")
            contents = update.get("contents", update)
            if isinstance(contents, list):
                contents = contents[0] if contents else {}
            return self._normalize_order(contents, symbol or update.get("market"))

    async def watch_user_fills(self, symbol: str | None = None, params: dict | None = None) -> dict:
        await self.subscribe_user_fills(symbol, params)
        while True:
            update = await self._next_ws_event("userFills")
            contents = update.get("contents", update)
            if isinstance(contents, dict) and "fills" in contents:
                fills = contents.get("fills") or []
                if contents.get("isSnapshot") and not fills:
                    continue
                contents = fills
            if isinstance(contents, list):
                if not contents:
                    continue
                contents = contents[0]
            fill = self._normalize_fill(contents, symbol or update.get("market"))
            trade_id = fill.get("tradeId")
            if trade_id is not None:
                trade_id = str(trade_id)
                if trade_id in self._seen_trade_ids:
                    continue
                self._seen_trade_ids.add(trade_id)
            return fill

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
                        event = {"channel": channel, "type": message.get("type"),
                                 "id": message.get("id"), "market": message.get("market"),
                                 "accountIndex": message.get("accountIndex"), "contents": row}
                        await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("Arcus WebSocket reader stopped: %s", exc)
            if not self._ws_closing and self._subscriptions and self._ws_reconnect_task is None:
                self._ws_reconnect_task = asyncio.create_task(self._reconnect_ws())

    async def _reconnect_ws(self) -> None:
        delay = 0.25
        try:
            while not self._ws_closing:
                try:
                    if self._websocket is not None:
                        await self._websocket.close()
                    import websockets
                    self._websocket = await websockets.connect(self.websocket_url)
                    self._ws_queue = asyncio.Queue()
                    self._ws_queues = {}
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
                    raise ArcusError(str(data.get("error", data)), endpoint=endpoint, code=str(data.get("code", response.status)))
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
        status_map = {"OPEN": "open", "PLACED": "open", "PARTIALLY_FILLED": "open", "FILLED": "closed",
                      "CANCELED": "canceled", "CANCELLED": "canceled", "REJECTED": "rejected", "EXPIRED": "expired"}
        status = str(order.get("state", order.get("status", "unknown"))).upper()
        filled = order.get("filled", order.get("filledSize", order.get("filledQuantity", order.get("executedQty"))))
        average = order.get("average", order.get("avgFillPrice", order.get("avgPrice", order.get("averagePrice"))))
        result = dict(order)
        result.update({"id": order.get("id", order.get("orderId")), "clientOrderId": order.get("clientId", order.get("clientOrderId")),
                       "symbol": symbol or order.get("marketDisplayName", order.get("symbol")), "status": status_map.get(status, status.lower()),
                       "filled": _decimal(filled) if filled is not None else 0.0,
                       "average": _decimal(average) if average is not None else None})
        return result

    @staticmethod
    def _normalize_fill(fill: dict[str, Any], symbol: str | None = None) -> dict:
        result = dict(fill)
        result.update({
            "id": str(fill.get("tradeId")) if fill.get("tradeId") is not None else None,
            "tradeId": str(fill.get("tradeId")) if fill.get("tradeId") is not None else None,
            "orderId": fill.get("orderId") or fill.get("takerOrderId") or fill.get("makerOrderId"),
            "symbol": symbol or fill.get("marketDisplayName"),
            "price": _decimal(fill.get("price")),
            "amount": _decimal(fill.get("size", fill.get("amount"))),
            "timestamp": fill.get("createdAt", fill.get("timestamp")),
        })
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
