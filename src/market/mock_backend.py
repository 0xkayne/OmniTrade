"""MockExchange — configurable test double implementing BaseExchange for tests."""

from __future__ import annotations

import asyncio
import time

from src.core.base_exchange import BaseExchange


class MockExchange(BaseExchange):
    """
    Configurable mock exchange for testing.

    Usage:
        mock = MockExchange("mock")
        mock.set_orderbook("BTCUSDT", bids=[(50000.0, 1.0)], asks=[(50010.0, 0.5)])
        mock.set_balance("USDT", 50000.0)
        mock.set_markets([Instrument(...), Instrument(...)])
        mock.inject_order_error("BTCUSDT", RuntimeError("rate limit"))
    """

    def __init__(self, name: str = "mock"):
        config = {
            "default_network": "testnet",
            "networks": {
                "testnet": {
                    "rest_base_url": "http://mock.local",
                    "websocket_url": "ws://mock.local",
                    "api_paths": {},
                },
                "mainnet": {
                    "rest_base_url": "http://mock.local",
                    "websocket_url": "ws://mock.local",
                    "api_paths": {},
                },
            },
            "fees": {"taker": 0.001, "maker": 0.0005},
        }
        secrets = {}
        super().__init__(name=name, config=config, secrets=secrets)

        # Canned data stores
        self._orderbooks: dict[str, dict] = {}
        self._balances: dict[str, float] = {}
        self._balances_by_type: dict[str, dict[str, float]] = {}
        self._markets: list = []
        self._order_errors: dict[str, Exception] = {}
        self._next_order_results: dict[str, dict] = {}
        self._connected = False
        self._order_counter = 0

        # Order lifecycle tracking (for polling simulation)
        self._orders: dict[str, dict] = {}

        # Per-symbol listing statuses (for Validator tests)
        self._listing_statuses: dict[str, str] = {}

        # Perp-specific state
        self._funding_rates: dict[str, dict] = {}
        self._margins: dict[str, dict[str, float]] = {}
        self._max_leverages: dict = {}

        # Global fail switches (for Executor/Reconciler tests)
        self._fail_create: bool = False
        self._fail_create_message: str = "network error"
        self._fail_cancel: bool = False
        self._fail_fetch: bool = False
        self.balance_fetch_params: list[dict | None] = []
        self.create_order_calls: list[dict] = []
        self.cancel_order_calls: list[dict] = []
        self.fetch_order_calls: list[dict] = []
        self.watch_order_calls: list[dict] = []
        self.set_leverage_calls: list[dict] = []

    # ---- Configuration methods for test code ----

    def set_orderbook(self, symbol: str, bids: list, asks: list) -> None:
        self._orderbooks[symbol] = {
            "bids": [[p, q] for p, q in bids],
            "asks": [[p, q] for p, q in asks],
        }

    def set_balance(self, asset: str, amount: float, account_type: str | None = None) -> None:
        if account_type is None:
            self._balances[asset] = amount
            return
        self._balances_by_type.setdefault(account_type, {})[asset] = amount

    def set_markets(self, instruments: list) -> None:
        self._markets = list(instruments)

    def set_listing_status(self, symbol: str, status: str) -> None:
        self._listing_statuses[symbol] = status

    def get_listing_status(self, symbol: str) -> str:
        return self._listing_statuses.get(symbol, "trading")

    def set_fail_create(self, fail: bool, message: str = "network error") -> None:
        self._fail_create = fail
        self._fail_create_message = message

    def set_fail_cancel(self, fail: bool) -> None:
        self._fail_cancel = fail

    def set_fail_fetch(self, fail: bool) -> None:
        self._fail_fetch = fail

    def inject_order_error(self, symbol: str, exception: Exception) -> None:
        """Make the next create_order call for `symbol` raise `exception`. One-shot."""
        self._order_errors[symbol] = exception

    def inject_next_order_result(self, symbol: str, result: dict) -> None:
        """Make the next create_order call for `symbol` return `result`. One-shot."""
        self._next_order_results[symbol] = result

    def get_order(self, order_id: str) -> dict | None:
        return self._orders.get(order_id)

    def set_funding_rate(self, symbol: str, funding_rate: float, next_funding_time: float | None = None) -> None:
        """Configure funding rate data that fetch_funding_rate will return."""
        self._funding_rates[symbol] = {
            "fundingRate": funding_rate,
            "nextFundingTimestamp": (next_funding_time * 1000) if next_funding_time else None,
            "fundingTimestamp": None,
            "symbol": symbol,
        }

    def set_max_leverage(self, symbol: str, leverage: float) -> None:
        """Configure the max leverage reported for a symbol.

        Use to test leverage-feasibility checks in Validator — set a low
        max_leverage on the Instrument and verify Validator rejects.
        """
        self._max_leverages[symbol] = leverage

    def set_margin(self, asset: str, amount: float, account_type: str | None = None) -> None:
        """Set free margin by account type (for Validator perp checks)."""
        key = account_type or "default"
        self._margins.setdefault(key, {})[asset] = amount

    # ---- BaseExchange abstract method implementations ----

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_orderbook(self, symbol: str, limit: int = 20, params: dict | None = None) -> dict:
        return self._orderbooks.get(symbol, {"bids": [], "asks": []})

    async def _fetch_balance_impl(self, params: dict | None = None) -> dict:
        self.balance_fetch_params.append(dict(params) if params else None)
        account_type = (params or {}).get("type")
        balances = self._balances_by_type.get(account_type, self._balances)
        return {"free": dict(balances)}

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params=None,
    ) -> dict:
        self.create_order_calls.append(
            {
                "symbol": symbol,
                "order_type": order_type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": dict(params) if params else None,
            }
        )
        if self._fail_create:
            raise RuntimeError(self._fail_create_message)

        if symbol in self._order_errors:
            err = self._order_errors.pop(symbol)
            raise err

        if symbol in self._next_order_results:
            return self._next_order_results.pop(symbol)

        self._order_counter += 1
        order_id = f"mock-{self.name}-{self._order_counter}"
        order = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "amount": amount,
            "price": price,
            "status": "open",
            "filled": 0.0,
            "average": None,
            "fee": {"cost": 1.25, "currency": "USDT"},
            "timestamp": time.time(),
        }
        self._orders[order_id] = order
        return dict(order)

    async def cancel_order(self, order_id: str, symbol: str, params=None) -> bool:
        self.cancel_order_calls.append(
            {
                "order_id": order_id,
                "symbol": symbol,
                "params": dict(params) if params else None,
            }
        )
        if self._fail_cancel:
            raise RuntimeError("cancel failed")
        if order_id not in self._orders:
            raise ValueError(f"order {order_id} not found")
        self._orders[order_id]["status"] = "canceled"
        return True

    async def fetch_order(self, order_id: str, symbol: str, params=None) -> dict:
        self.fetch_order_calls.append(
            {
                "order_id": order_id,
                "symbol": symbol,
                "params": dict(params) if params else None,
            }
        )
        if self._fail_fetch:
            raise RuntimeError("fetch order failed")
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"order {order_id} not found")
        # Simulate fill on first fetch (like FakeExchange)
        if order["status"] == "open":
            order["status"] = "closed"
            order["filled"] = order["amount"]
            order["average"] = 50000.0
        return dict(order)

    async def watch_orders(self, symbol: str | None = None, params: dict | None = None) -> dict:
        """Simulate WebSocket order watching.

        Returns the first open order as filled (matching the behaviour of
        fetch_order), simulating an immediate fill notification via WS.
        If no open orders, sleeps briefly and returns a dummy response.
        """
        self.watch_order_calls.append(
            {
                "symbol": symbol,
                "params": dict(params) if params else None,
            }
        )
        for _order_id, order in self._orders.items():
            if order["status"] == "open":
                if symbol is None or order.get("symbol") == symbol:
                    order["status"] = "closed"
                    order["filled"] = order["amount"]
                    order["average"] = 50000.0
                    return dict(order)
        await asyncio.sleep(0.01)
        return {"id": "no-open-orders", "status": "open"}

    async def list_markets(self) -> list:
        return list(self._markets)

    async def connect_websocket(self) -> bool:
        return True

    async def subscribe_orderbook(self, symbol: str) -> None:
        pass

    # ---- Perp-specific overrides (Stage 4) ----

    async def set_leverage(self, leverage: int, symbol: str | None = None, params: dict | None = None) -> dict:
        self.set_leverage_calls.append(
            {"leverage": leverage, "symbol": symbol, "params": dict(params) if params else None}
        )
        return {"leverage": leverage, "symbol": symbol}

    async def fetch_funding_rate(self, symbol: str, params: dict | None = None) -> dict:
        if symbol in self._funding_rates:
            return dict(self._funding_rates[symbol])
        return {"fundingRate": None, "nextFundingTimestamp": None, "symbol": symbol}

    async def fetch_funding_rates(self, symbols: list[str] | None = None, params: dict | None = None) -> dict:
        result = {}
        for sym in symbols or list(self._funding_rates.keys()):
            if sym in self._funding_rates:
                result[sym] = dict(self._funding_rates[sym])
        return result

    async def fetch_free_margin(self, params: dict | None = None) -> dict:
        account_type = (params or {}).get("type", "default")
        free = dict(self._margins.get(account_type, {}))
        if not free:
            # Fall back to regular balance if no perp margin set
            free = dict(self._balances)
        return {"free": free}

    async def create_reduce_only_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        params = dict(params) if params else {}
        params["reduceOnly"] = True
        return await self.create_order(symbol, type, side, amount, price=price, params=params)
