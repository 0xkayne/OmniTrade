"""MockExchange — configurable test double implementing BaseExchange for tests."""

from __future__ import annotations

import uuid

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
        # Pass minimal config/secrets so BaseExchange.__init__ doesn't blow up
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
        self._markets: list = []
        self._order_errors: dict[str, Exception] = {}
        self._next_order_results: dict[str, dict] = {}
        self._connected = False
        self._order_counter = 0

    # ---- Configuration methods for test code ----

    def set_orderbook(self, symbol: str, bids: list, asks: list) -> None:
        """
        Set a canned orderbook for a symbol.

        Args:
            symbol: Venue-native symbol, e.g. "BTCUSDT".
            bids: List of (price, qty) tuples, descending.
            asks: List of (price, qty) tuples, ascending.
        """
        self._orderbooks[symbol] = {
            "bids": [[p, q] for p, q in bids],
            "asks": [[p, q] for p, q in asks],
        }

    def set_balance(self, asset: str, amount: float) -> None:
        """Set available balance for an asset."""
        self._balances[asset] = amount

    def set_markets(self, instruments: list) -> None:
        """Set the instruments returned by list_markets()."""
        self._markets = list(instruments)

    def inject_order_error(self, symbol: str, exception: Exception) -> None:
        """
        Make the next create_order call for `symbol` raise `exception`.
        Clears after one use.
        """
        self._order_errors[symbol] = exception

    def inject_next_order_result(self, symbol: str, result: dict) -> None:
        """
        Make the next create_order call for `symbol` return `result`.
        Clears after one use.
        """
        self._next_order_results[symbol] = result

    # ---- BaseExchange abstract method implementations ----

    async def connect(self) -> None:
        """Simulate connecting to the exchange."""
        self._connected = True

    async def close(self) -> None:
        """Simulate closing the connection."""
        self._connected = False
        # Call parent close to clean up session if any
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Return canned orderbook data for symbol."""
        if symbol not in self._orderbooks:
            raise KeyError(f"No canned orderbook for '{symbol}'")
        return self._orderbooks[symbol]

    async def fetch_balance(self, params=None) -> dict:
        """Return canned balances. Defaults to 0 for any asset not explicitly set."""
        # Always return a copy so tests can't mutate internal state
        return dict(self._balances)

    async def create_order(
        self, symbol: str, order_type: str, side: str, amount: float, price: float | None = None, params=None
    ) -> dict:
        """Create a simulated order. Supports injection of canned results or errors."""
        # Check for injected error first
        if symbol in self._order_errors:
            err = self._order_errors.pop(symbol)
            raise err

        # Check for injected canned result
        if symbol in self._next_order_results:
            return self._next_order_results.pop(symbol)

        # Default mock response
        self._order_counter += 1
        order_id = f"mock-{uuid.uuid4().hex[:8]}"
        return {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "amount": amount,
            "price": price,
            "status": "closed",
            "filled": amount,
            "average": price or 0.0,
        }

    async def cancel_order(self, order_id: str, symbol: str, params=None) -> bool:
        """Simulate cancelling an order. Always succeeds."""
        return True

    async def fetch_order(self, order_id: str, symbol: str, params=None) -> dict:
        """Simulate fetching an order. Returns a basic closed order."""
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "closed",
        }

    async def list_markets(self) -> list:
        """Return instruments set via set_markets()."""
        return list(self._markets)

    async def connect_websocket(self) -> bool:
        """Simulate connecting to WebSocket. Always succeeds."""
        return True

    async def subscribe_orderbook(self, symbol: str) -> None:
        """Simulate subscribing to orderbook updates. No-op."""
        pass
