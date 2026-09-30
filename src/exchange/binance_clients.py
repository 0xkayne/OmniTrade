"""Fixed Binance product clients and the shared UM/CM transport budget."""

from __future__ import annotations

import asyncio
import math
import time
from copy import deepcopy
from urllib.parse import urlparse

from src.market.instrument import NetworkType

from .ccxt import _is_placeholder_value

_BINANCE_CLASSES = {"spot": "binance", "usdm": "binanceusdm", "coinm": "binancecoinm"}


class _BinanceRateBudget:
    """Serialize contract transports and share fixed-window weights/order counts.

    REST and ccxt.pro snapshot/listen-key requests use this same instance. Limits
    are refined from exchangeInfo and observed response headers. Requests are
    never automatically retried, especially when an order result is ambiguous.
    """

    def __init__(self):
        self.lock = asyncio.Lock()
        self.limits = {("weight", 60): 2400, ("orders", 10): 300, ("orders", 60): 1200}
        self.usage: dict[tuple[str, int], tuple[int, float]] = {}
        self.blocked_until = 0.0

    def _used(self, key: tuple[str, int], now: float) -> float:
        period, used = self.usage.get(key, (-1, 0))
        return used if period == int(now // key[1]) else 0

    async def reserve(self, weight: float, orders: int):
        while True:
            now = time.time()
            delay = max(0, self.blocked_until - now)
            for key, limit in self.limits.items():
                cost = weight if key[0] == "weight" else orders
                if cost > limit:
                    raise ValueError("binance: request exceeds configured shared rate budget")
                if self._used(key, now) + cost > limit:
                    delay = max(delay, (int(now // key[1]) + 1) * key[1] - now + 0.01)
            if delay <= 0:
                break
            await asyncio.sleep(min(delay, 60))
        for key in self.limits:
            cost = weight if key[0] == "weight" else orders
            self.usage[key] = (int(now // key[1]), self._used(key, now) + cost)

    def observe(self, headers: dict | None, response=None):
        now = time.time()
        for name, value in (headers or {}).items():
            lowered = name.lower()
            kind = "weight" if lowered.startswith("x-mbx-used-weight-") else "orders"
            if not lowered.startswith(("x-mbx-used-weight-", "x-mbx-order-count-")):
                if lowered == "retry-after":
                    try:
                        self.blocked_until = max(self.blocked_until, now + float(value))
                    except (TypeError, ValueError):
                        self.blocked_until = max(self.blocked_until, now + 60)
                continue
            suffix = lowered.rsplit("-", 1)[-1]
            try:
                seconds = int(suffix[:-1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[suffix[-1]]
                key = (kind, seconds)
                self.usage[key] = (int(now // seconds), max(self._used(key, now), float(value)))
            except (KeyError, TypeError, ValueError):
                continue
        if isinstance(response, dict):
            for limit in response.get("rateLimits") or []:
                kind = {"REQUEST_WEIGHT": "weight", "ORDERS": "orders"}.get(limit.get("rateLimitType"))
                seconds = {"SECOND": 1, "MINUTE": 60, "HOUR": 3600, "DAY": 86400}.get(limit.get("interval"))
                if kind and seconds and limit.get("limit"):
                    key = (kind, seconds * int(limit.get("intervalNum", 1)))
                    # Older CM/UM metadata may disagree during migration.
                    # Honor the tighter observed shared limit.
                    self.limits[key] = min(self.limits.get(key, int(limit["limit"])), int(limit["limit"]))


def _attach_binance_budget(client, budget: _BinanceRateBudget):
    original = client.fetch2

    async def fetch2(path, api="public", method="GET", params=None, headers=None, body=None, config=None):
        params, config = params or {}, config or {}
        if str(api).startswith("papi"):
            raise ValueError("binance: Portfolio Margin routes are unsupported")
        # CCXT's weighted costs can be expressed as 0.2 per API weight; using
        # five times their value is conservative for both derivative families.
        weight = max(1, math.ceil(client.calculate_rate_limiter_cost(api, method, path, params, config) * 5))
        orders = (
            len(params.get("batchOrders", [])) if path == "batchOrders" else int(path == "order" and method == "POST")
        )
        async with budget.lock:
            await budget.reserve(weight, orders)
            try:
                response = await original(path, api, method, params, headers, body, config)
            except Exception:
                budget.observe(getattr(client, "last_response_headers", None))
                raise
            budget.observe(getattr(client, "last_response_headers", None), response)
            return response

    client.fetch2 = fetch2


def _filter_binance_order_stream(client, family: str):
    """Drop foreign product events before CCXT parses a shared UM/CM stream."""
    original = client.handle_order_update

    def handle_order_update(connection, message):
        payload = message.get("o", message)
        native_symbol = payload.get("s") if isinstance(payload, dict) else None
        candidates = (client.markets_by_id or {}).get(native_symbol) or []
        candidates = candidates if isinstance(candidates, list) else [candidates]
        is_matching = any(
            (
                market.get("type") == "spot"
                if family == "spot"
                else market.get("type") == "swap" and market.get("inverse") is (family == "coinm")
            )
            and not market.get("expiry")
            for market in candidates
        )
        if is_matching:
            return original(connection, message)
        return None

    client.handle_order_update = handle_order_update


def _configure_binance_network(client, network: NetworkType, family: str):
    if network is NetworkType.TESTNET:
        client.enable_demo_trading(True)
        demo_ws = client.urls.get("demo", {}).get("ws")
        if demo_ws:
            client.urls["api"]["ws"] = deepcopy(demo_ws)
    api = client.urls.get("api", {})
    prefixes = {"spot": ("public", "private"), "usdm": ("fapi",), "coinm": ("dapi",)}[family]
    relevant = [value for key, value in api.items() if any(key.startswith(prefix) for prefix in prefixes)]
    if not relevant:
        raise ValueError(f"binance:{family}: no REST endpoint for {network.value}")
    for url in relevant:
        if not isinstance(url, str):
            continue
        host = urlparse(url).hostname or ""
        is_demo = host.startswith("demo-") and host.endswith(".binance.com")
        is_mainnet = host.endswith(".binance.com") and not is_demo and "testnet" not in host
        if (network is NetworkType.TESTNET and not is_demo) or (network is NetworkType.MAINNET and not is_mainnet):
            raise ValueError(f"binance:{family}: REST endpoint does not match {network.value}")


def _create_binance_client(
    family: str,
    network: NetworkType,
    secrets: dict | None = None,
    options: dict | None = None,
    *,
    use_websocket: bool = False,
    rate_budget: _BinanceRateBudget | None = None,
):
    if family not in _BINANCE_CLASSES:
        raise ValueError(f"binance: unsupported market family {family!r}")
    if use_websocket:
        import ccxt.pro as ccxt
    else:
        import ccxt.async_support as ccxt
    settings = deepcopy(options or {})
    settings.update(
        defaultType="spot" if family == "spot" else "swap",
        defaultSubType=("inverse" if family == "coinm" else "linear") if family != "spot" else None,
        fetchMarkets={"types": [{"spot": "spot", "usdm": "linear", "coinm": "inverse"}[family]]},
        maxRetriesOnFailure=0,
        portfolioMargin=False,
    )
    secrets = secrets or {}
    config = {"enableRateLimit": True, "options": settings}
    for target, value in (
        ("apiKey", secrets.get("apiKey") or secrets.get("api_key")),
        ("secret", secrets.get("secret")),
    ):
        if not _is_placeholder_value(value):
            config[target] = value
    client = getattr(ccxt, _BINANCE_CLASSES[family])(config)
    _configure_binance_network(client, network, family)
    if use_websocket and network is NetworkType.TESTNET and not client.urls.get("demo", {}).get("ws"):
        raise ValueError(f"binance:{family}: Demo WebSocket endpoints are unavailable")
    if use_websocket:
        _filter_binance_order_stream(client, family)
    if rate_budget is not None and family != "spot":
        _attach_binance_budget(client, rate_budget)
    return client
