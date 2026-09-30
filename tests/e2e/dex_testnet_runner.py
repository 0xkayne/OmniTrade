"""Bounded live DEX validation. Importing this module performs no I/O."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from urllib.parse import urlparse

from ccxt.base.errors import InvalidOrder, NetworkError

from src.arbitrage.canary import CONFIRMATION_TOKEN, CanaryRequest, TestnetCanary
from src.arbitrage.executor import HedgedExecutor
from src.arbitrage.recovery import ArbitrageRecovery
from src.cli.config import load_exchange_configuration
from src.coordinator.intent import Intent
from src.coordinator.leg_context import deserialize_leg_context, serialize_leg_context
from src.coordinator.leg_orders import LegOrderManager
from src.coordinator.plan import PlannedLeg
from src.exchange.factory import ExchangeFactory
from src.exchange.order import OrderRequest, OrderSnapshot
from src.market.instrument import NetworkType
from src.market.quote import EstimatedFill
from src.persistence.store import PersistenceStore


class _Blocked(RuntimeError):
    """A scenario cannot establish the required live evidence."""


class _SubmissionBlocked(_Blocked, InvalidOrder):
    """A locally rejected request that never reached the exchange transport."""


def _rounded(value, step, *, up=False):
    value, step = Decimal(str(value)), Decimal(str(step))
    if not value.is_finite() or not step.is_finite() or step <= 0:
        raise _Blocked("invalid quantity or price step")
    return float((value / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * step)


def _common_step(*steps):
    decimals = [Decimal(str(value)) for value in steps]
    if any(not value.is_finite() or value <= 0 for value in decimals):
        raise _Blocked("missing common quantity precision")
    scale = 10 ** max(-value.as_tuple().exponent for value in decimals)
    return float(Decimal(math.lcm(*(int(value * scale) for value in decimals))) / scale)


class _Budget:
    """Reserve worst-case turnover before I/O; canceled orders keep their reserve."""

    def __init__(self, maximum_order, maximum_total, path):
        if not 0 < maximum_order <= 100 or not maximum_order * 4 <= maximum_total <= 5000:
            raise ValueError("budgets require 0 < per-order <= 100 and 4*per-order <= total <= 5000")
        self.maximum_order = float(maximum_order)
        self.maximum_total = float(maximum_total)
        self.path = path
        self.reservations = {}
        self.actual = {}

    @property
    def reserved(self):
        return sum(self.reservations.values())

    def reserve(self, identity, notional):
        if identity in self.reservations:
            raise _Blocked("submission identity was already used; query it instead of resending")
        if not math.isfinite(notional) or not 0 < notional <= self.maximum_order:
            raise _Blocked("order exceeds the per-order notional limit")
        if self.reserved + notional > self.maximum_total:
            raise _Blocked("run turnover budget exhausted")
        self.reservations[identity] = notional
        self.flush()

    def require_cleanup_reserve(self, legs=1):
        if self.reserved + 4 * legs * self.maximum_order > self.maximum_total:
            raise _Blocked("insufficient remaining turnover budget for opening and cleanup")

    def observe(self, identity, snapshot):
        if snapshot.filled_notional_quote is not None:
            self.actual[identity] = snapshot.filled_notional_quote
        elif snapshot.filled_qty_base is not None and snapshot.avg_price is not None:
            self.actual[identity] = snapshot.filled_qty_base * snapshot.avg_price
        self.flush()

    def flush(self):
        data = {
            "maximum_order_usd": self.maximum_order,
            "maximum_total_usd": self.maximum_total,
            "reserved_worst_case_usd": self.reserved,
            "observed_turnover_usd": sum(self.actual.values()),
            "reservations": self.reservations,
            "actual": self.actual,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        os.replace(temporary, self.path)


@dataclass
class _OwnedOrder:
    instrument: object
    manager: LegOrderManager
    request: OrderRequest
    intent_id: str
    snapshot: OrderSnapshot | None = None


class _CredentialFilter(logging.Filter):
    def __init__(self, redact):
        super().__init__()
        self.redact = redact

    def filter(self, record):
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


class _DexRun:
    def __init__(self, output, *, trade=False, maximum_order=100, maximum_total=5000):
        self.output = Path(output)
        self.trade = trade
        self.run_id = "dex-" + uuid.uuid4().hex
        self.budget = _Budget(maximum_order, maximum_total, self.output / "budget.json")
        self.exchanges = {}
        self.markets = {}
        self.selected = {}
        self.checks = []
        self.orders = []
        self.baselines = {}
        self.sensitive = []
        self.store = None
        self.halted = False
        self.ws_events = {}
        self.ws_tasks = []
        self._gate_lock = asyncio.Lock()

    def redact(self, value):
        if isinstance(value, str):
            for secret in self.sensitive:
                value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, dict):
            return {str(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self.redact(item) for item in value]
        return value

    def record(self, name, status, details=None):
        self.checks.append({"check": name, "status": status, "details": self.redact(details), "time": time.time()})
        self.flush()

    def flush(self):
        report = {
            "run_id": self.run_id,
            "network": "testnet",
            "trading_enabled": self.trade,
            "halted": self.halted,
            "checks": self.checks,
            "reserved_worst_case_usd": self.budget.reserved,
            "observed_turnover_usd": sum(self.budget.actual.values()),
        }
        (self.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        rows = [
            "# DEX testnet validation",
            "",
            f"Run: `{self.run_id}`",
            "",
            "| Check | Result | Evidence |",
            "|---|---|---|",
        ]
        for check in self.checks:
            evidence = (
                json.dumps(check["details"], ensure_ascii=False, default=str).replace("|", "\\|").replace("\n", " ")
            )
            rows.append(f"| {check['check']} | {check['status']} | {evidence} |")
        rows += [
            "",
            f"Reserved worst-case turnover: {self.budget.reserved:.4f} USD.",
            f"Observed turnover: {sum(self.budget.actual.values()):.4f} USD.",
            "Canceled/unfilled orders retain their budget reservation; this is stricter than executed turnover.",
        ]
        (self.output / "report.md").write_text("\n".join(rows) + "\n")

    async def check(self, name, action, *, timeout=60):
        try:
            value = await asyncio.wait_for(action(), timeout)
        except NotImplementedError as exc:
            self.record(name, "UNSUPPORTED", f"{type(exc).__name__}: {exc}")
            return None
        except _Blocked as exc:
            self.record(name, "BLOCKED", str(exc))
            return None
        except Exception as exc:
            self.record(name, "FAIL", f"{type(exc).__name__}: {exc}")
            return None
        self.record(name, "PASS", value)
        return value

    @staticmethod
    def network_guard(exchange):
        expected = {"arcus": "api.testnet.arcus.xyz", "hyperliquid": "api.hyperliquid-testnet.xyz"}
        host = expected.get(exchange.name)
        if exchange.network_type is not NetworkType.TESTNET or not host:
            raise _Blocked("only Arcus and Hyperliquid testnet adapters are allowed")
        for url in (exchange.rest_base_url, exchange.websocket_url):
            if urlparse(url).hostname != host:
                raise _Blocked("adapter endpoint is outside the testnet allowlist")
        if exchange.name == "hyperliquid":
            config = exchange._build_ccxt_config()
            if not config["options"].get("sandboxMode"):
                raise _Blocked("Hyperliquid signing domain is not testnet")

    def install_gate(self, exchange):
        original = exchange.create_order

        async def validate(symbol, order_type, side, amount, price=None, params=None):
            self.network_guard(exchange)
            if not self.trade:
                raise _Blocked("live orders require the explicit testnet trading flag")
            if self.halted:
                raise _Blocked("run halted after unresolved execution")
            instrument = next(item for item in self.markets[exchange.name] if item.venue_symbol == symbol)
            identity = (params or {}).get("clientOrderId")
            if not identity or order_type != "limit" or price is None:
                raise _Blocked("only identified, price-protected limit orders are allowed")
            if not math.isfinite(amount) or amount <= 0 or not math.isfinite(price) or price <= 0:
                raise _Blocked("invalid order quantity or limit price")
            if Decimal(str(amount)) % Decimal(str(instrument.qty_step)) or amount < instrument.min_qty:
                raise _Blocked("order quantity violates venue precision or minimum")
            notional = instrument.quote_notional(amount, price)
            if notional > self.budget.maximum_order:
                raise _Blocked("order exceeds the per-order notional limit")
            if notional < instrument.min_notional and not (params or {}).get("reduceOnly"):
                raise _Blocked("order violates minimum notional")
            book = await self.book(exchange, instrument)
            mid = (float(book["bids"][0][0]) + float(book["asks"][0][0])) / 2
            if not mid * 0.995 <= price <= mid * 1.005:
                raise _Blocked("fresh quote moved outside the fixed 0.5% protection band")
            if (params or {}).get("timeInForce") == "IOC":
                self.require_depth(book, side, amount, price)
            if instrument.market_type == "perp" and not (params or {}).get("reduceOnly"):
                position = await exchange.fetch_order_position(instrument)
                if position.qty_native != 0:
                    raise _Blocked("opening baseline changed immediately before submission")
            async with self._gate_lock:
                self.budget.reserve(str(identity), notional)

        async def guarded(symbol, order_type, side, amount, price=None, params=None):
            try:
                await validate(symbol, order_type, side, amount, price, params)
            except _Blocked as exc:
                # These checks run before transport: rejection has an exact zero fill.
                raise _SubmissionBlocked(str(exc)) from exc
            return await original(symbol, order_type, side, amount, price, params)

        exchange.create_order = guarded

    async def run(self):
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        configs, keys = load_exchange_configuration(target_network=NetworkType.TESTNET, venues=("arcus", "hyperliquid"))
        self.sensitive = sorted(
            {
                variant
                for fields in keys.values()
                for value in fields.values()
                if isinstance(value, str) and len(value) >= 8
                for variant in (value, value.lower(), value.upper())
            },
            key=len,
            reverse=True,
        )
        log_filter = _CredentialFilter(self.redact)
        handlers = list(logging.getLogger().handlers)
        for handler in handlers:
            handler.addFilter(log_filter)
        try:
            self.store = PersistenceStore(self.output / "execution.db", self.output / "audit")
            await self.store.initialize()
            for venue in ("arcus", "hyperliquid"):
                exchange = ExchangeFactory.create_exchange(venue, configs[venue], keys[venue])
                self.exchanges[venue] = exchange
                self.network_guard(exchange)
                self.install_gate(exchange)
                await self.check(f"{venue}.connect", lambda ex=exchange: self.connect(ex))
                if venue not in self.markets:
                    continue
                await self.readonly(exchange)
            if self.trade:
                await self.trading()
            await self.check("orders.restart_readback", self.readback)
        finally:
            for task in self.ws_tasks:
                task.cancel()
            await asyncio.gather(*self.ws_tasks, return_exceptions=True)
            for exchange in self.exchanges.values():
                try:
                    await exchange.close()
                except Exception as exc:
                    self.record(f"{exchange.name}.close", "FAIL", type(exc).__name__)
            if self.store is not None:
                await self.store.close()
            self.flush()
            for handler in handlers:
                handler.removeFilter(log_filter)
        return self.checks

    async def connect(self, exchange):
        await exchange.connect()
        if exchange.name == "arcus":
            registry = await exchange._request("GET", "/v1/apiKeys", params={"address": exchange.master_wallet_address})
            matched = [entry for entry in registry.get("apiKeys", []) if entry.get("apiKey") == exchange.api_key]
            if len(matched) != 1:
                raise _Blocked("configured API key is not registered to the master wallet")
            key = matched[0]
            if key.get("status") != "ACTIVE" or int(key.get("validUntil", 0)) <= time.time() * 1000:
                raise _Blocked("configured API key is inactive or expired")
            if not key.get("allSubaccounts") and key.get("accountIndex") != exchange.account_index:
                raise _Blocked(
                    f"configured accountIndex={exchange.account_index}, but API key only authorizes accountIndex={key.get('accountIndex')}"
                )
        self.markets[exchange.name] = await exchange.list_markets()
        return {"markets": len(self.markets[exchange.name]), "credentials_configured": exchange.has_credentials()}

    async def book(self, exchange, instrument):
        book = await exchange.fetch_orderbook(instrument.venue_symbol, limit=20)
        if not book.get("bids") or not book.get("asks"):
            raise _Blocked("empty order book")
        bid, ask = float(book["bids"][0][0]), float(book["asks"][0][0])
        if not all(math.isfinite(p) and p > 0 for p in (bid, ask)) or bid >= ask:
            raise _Blocked("invalid or crossed order book")
        return book

    @staticmethod
    def require_depth(book, side, quantity, price):
        levels = book["asks" if side == "buy" else "bids"]
        available = sum(
            float(qty) for px, qty, *_ in levels if (float(px) <= price if side == "buy" else float(px) >= price)
        )
        if available + 1e-12 < quantity:
            raise _Blocked("insufficient depth inside the protected limit")
        return available

    async def clean(self, exchange, instrument):
        orders = await exchange.fetch_open_orders(instrument.venue_symbol, params=exchange.account_params(instrument))
        if not isinstance(orders, list) or orders:
            raise _Blocked("selected market has existing or unverified open orders")
        if instrument.market_type == "perp":
            position = await exchange.fetch_order_position(instrument)
            if (
                position.symbol != instrument.venue_symbol
                or not math.isfinite(position.qty_native)
                or position.qty_native != 0
            ):
                raise _Blocked("selected market has an existing or unverified position")
            return {
                "position_qty": position.qty_native,
                "leverage": position.leverage,
                "margin_mode": position.margin_mode,
            }
        balance = await exchange.fetch_balance(exchange.account_params(instrument))
        if not isinstance(balance.get("total"), dict) or not balance["total"]:
            raise _Blocked("spot balance response is incomplete")
        total = float(balance["total"].get(instrument.base.symbol, 0))
        if not math.isfinite(total) or total != 0:
            raise _Blocked("selected spot asset has an existing holding")
        return {"base_balance": total}

    async def choose(self, exchange, market_type):
        candidates = [
            item
            for item in self.markets[exchange.name]
            if item.market_type == market_type
            and item.listing_status == "trading"
            and not item.is_inverse
            and item.contract_size == 1
            and item.quote.symbol in {"USD", "USDC", "USDT"}
        ]
        candidates.sort(
            key=lambda item: (
                ("BTC", "ETH").index(item.base.symbol) if item.base.symbol in {"BTC", "ETH"} else 2,
                item.venue_symbol,
            )
        )
        failures = []
        eligible = []
        fallback = [item for item in candidates if item.base.symbol not in {"BTC", "ETH"}]
        if market_type == "spot" and fallback:
            tickers = await exchange.fetch_tickers([item.venue_symbol for item in fallback], params={"type": "spot"})
            fallback.sort(
                key=lambda item: float((tickers.get(item.venue_symbol) or {}).get("quoteVolume") or 0), reverse=True
            )
        candidates = [item for item in candidates if item.base.symbol in {"BTC", "ETH"}] + fallback[:20]
        for instrument in candidates:
            if market_type == "perp" and instrument.base.symbol not in {"BTC", "ETH"}:
                continue
            try:
                baseline = await self.clean(exchange, instrument)
                book = await self.book(exchange, instrument)
                price = self.price(exchange, instrument, book, "buy")
                quantity = self.quantity(instrument, price)
                if instrument.quote_notional(quantity, price) > self.budget.maximum_order:
                    raise _Blocked("minimum order exceeds the per-order budget")
                bid_depth = self.require_depth(book, "sell", quantity, self.price(exchange, instrument, book, "sell"))
                ask_depth = self.require_depth(book, "buy", quantity, price)
            except Exception as exc:
                failures.append(
                    {"symbol": instrument.venue_symbol, "reason": self.redact(f"{type(exc).__name__}: {exc}")}
                )
                continue
            eligible.append((min(bid_depth, ask_depth) * price, instrument, quantity, baseline))
            if instrument.base.symbol in {"BTC", "ETH"}:
                break
        if eligible:
            _, instrument, quantity, baseline = max(eligible, key=lambda item: item[0])
            self.selected[(exchange.name, market_type)] = instrument
            self.baselines[(exchange.name, instrument.venue_symbol)] = baseline
            return {"symbol": instrument.venue_symbol, "quantity": quantity, "baseline": baseline, "excluded": failures}
        raise _Blocked("no eligible empty market: " + json.dumps(failures[:8]))

    def price(self, exchange, instrument, book, side, *, resting=False):
        bid, ask = float(book["bids"][0][0]), float(book["asks"][0][0])
        mid = (bid + ask) / 2
        raw = (
            mid * (1 + (0.004 if resting else -0.004)) if side == "sell" else mid * (1 + (-0.004 if resting else 0.004))
        )
        step = (
            exchange.price_tick(instrument.venue_symbol, raw)
            if exchange.name == "arcus" and hasattr(exchange, "price_tick")
            else instrument.price_step
        )
        price = _rounded(raw, step, up=side == "sell")
        if not mid * 0.995 <= price <= mid * 1.005:
            raise _Blocked("price precision cannot fit the fixed 0.5% protection band")
        if not resting and ((side == "buy" and price < ask) or (side == "sell" and price > bid)):
            raise _Blocked("spread exceeds the protection band")
        if resting and ((side == "buy" and price >= ask) or (side == "sell" and price <= bid)):
            raise _Blocked("resting limit would immediately cross the book")
        return price

    @staticmethod
    def quantity(instrument, price):
        target = max(25.0, instrument.min_notional)
        return _rounded(max(instrument.min_qty, target / price), instrument.qty_step, up=True)

    async def readonly(self, exchange):
        for market_type in ("perp", "spot") if exchange.name == "hyperliquid" else ("perp",):
            await self.check(f"{exchange.name}.{market_type}.baseline", lambda t=market_type: self.choose(exchange, t))
            instrument = self.selected.get((exchange.name, market_type))
            if instrument is None:
                continue
            params = exchange.account_params(instrument)

            async def account(inst=instrument):
                snapshot = await exchange.fetch_order_account(inst)
                return asdict(snapshot)

            await self.check(f"{exchange.name}.{market_type}.account", account)

            async def public_book(inst=instrument):
                book = await self.book(exchange, inst)
                return {"bid": book["bids"][0], "ask": book["asks"][0], "timestamp": book.get("timestamp")}

            await self.check(f"{exchange.name}.{market_type}.orderbook", public_book)
            methods = [
                ("ticker", lambda i=instrument: exchange.fetch_ticker(i.venue_symbol)),
                ("trades", lambda i=instrument: exchange.fetch_trades(i.venue_symbol, limit=5)),
                ("ohlcv", lambda i=instrument: exchange.fetch_ohlcv(i.venue_symbol, "1m", limit=5)),
                (
                    "my_trades",
                    lambda i=instrument, p=params: exchange.fetch_my_trades(i.venue_symbol, limit=10, params=p),
                ),
            ]
            if market_type == "perp":
                methods.append(("funding", lambda i=instrument: exchange.fetch_funding_rate(i.venue_symbol)))
            for name, method in methods:

                async def summarize(call=method, label=name):
                    value = await call()
                    if value is None:
                        raise _Blocked("endpoint returned no data")
                    if not value and label in {"ohlcv", "trades"}:
                        raise _Blocked("public endpoint returned no market observations")
                    return {"count": len(value), "sample": value[:1] if isinstance(value, list) else value}

                await self.check(f"{exchange.name}.{market_type}.{name}", summarize)

            async def websocket(inst=instrument):
                book = await exchange.watch_order_book(inst.venue_symbol, 5)
                if not book.get("bids") or not book.get("asks"):
                    raise _Blocked("no usable websocket book")
                return {"bid": book["bids"][0], "ask": book["asks"][0]}

            await self.check(f"{exchange.name}.{market_type}.websocket_book", websocket, timeout=30)
            await self.check(
                f"{exchange.name}.{market_type}.websocket_reconnect",
                lambda inst=instrument: self.reconnect(exchange, inst),
                timeout=45,
            )

    async def reconnect(self, exchange, instrument):
        if exchange.name == "arcus":
            previous = exchange._websocket
            if previous is None:
                raise _Blocked("no existing socket to interrupt")
            await previous.close()
            deadline = time.monotonic() + 30
            while exchange._websocket is previous and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            if exchange._websocket is previous:
                raise _Blocked("Arcus socket did not reconnect automatically")
        else:
            clients = list(exchange._ws_client.clients.values())
            if not clients:
                raise _Blocked("no existing socket to interrupt")
            for client in clients:
                await client.connection.close()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                book = await asyncio.wait_for(exchange.watch_order_book(instrument.venue_symbol, 5), 5)
                if book.get("bids") and book.get("asks"):
                    return {
                        "symbol": instrument.venue_symbol,
                        "rest_fallback": False,
                        "bid": book["bids"][0],
                        "ask": book["asks"][0],
                    }
            except (asyncio.TimeoutError, ConnectionError, NetworkError):
                pass
            await asyncio.sleep(0.1)
        raise _Blocked("no fresh websocket book after disconnect")

    async def watch_account(self, exchange, instrument, kind):
        events = self.ws_events.setdefault((exchange.name, instrument.venue_symbol, kind), [])
        method = exchange.watch_orders if kind == "orders" else exchange.watch_user_fills
        while True:
            try:
                update = await asyncio.wait_for(
                    method(instrument.venue_symbol, params=exchange.account_params(instrument)), 30
                )
                events.extend(update if isinstance(update, list) else [update])
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.record(
                    f"{exchange.name}.{instrument.market_type}.ws_{kind}", "FAIL", f"{type(exc).__name__}: {exc}"
                )
                return

    async def new_order(self, exchange, instrument, side, amount, price, *, reduce_only=False, tif="IOC"):
        identity = self.run_id + "-" + uuid.uuid4().hex
        position = await exchange.fetch_order_position(instrument) if instrument.market_type == "perp" else None
        if position and not reduce_only and position.qty_native != 0:
            raise _Blocked("opening baseline changed; refusing to touch an existing position")
        intent = Intent(
            identity,
            instrument.base.symbol,
            [instrument.quote.symbol],
            instrument.market_type,
            side,
            "limit",
            instrument.quote_notional(amount, price),
            {exchange.name: 1.0},
            limit_price=price,
            time_in_force=tif,
            quantity_native=amount,
            position_effect="close" if reduce_only else "open",
        )
        await self.store.create_intent(intent)
        leg = await self.store.create_leg(
            intent_id=identity,
            venue=exchange.name,
            instrument_venue_symbol=instrument.venue_symbol,
            instrument_base=instrument.base.symbol,
            instrument_quote=instrument.quote.symbol,
            instrument_market_type=instrument.market_type,
            planned_qty_base=amount,
            planned_qty_native=str(amount),
            quantity_unit=instrument.quantity_unit,
            planned_notional_usd=instrument.quote_notional(amount, price),
        )
        context = PlannedLeg(
            exchange.name,
            instrument,
            instrument.quote.symbol,
            instrument.quote_notional(amount, price),
            amount,
            EstimatedFill(price, 0, 0, filled_fully=False),
            0,
            side=side,
            leverage=position.leverage if position else 1,
            reference_price=price,
            quote_fetched_at=time.time(),
            quote_source="rest",
            planned_qty_native=amount,
            position_before_qty_native=position.qty_native if position else None,
            position_entry_price=position.entry_price if position else None,
            position_effect="close" if reduce_only else "open",
        )
        await self.store.update_leg(leg, execution_context_json=serialize_leg_context(context))
        request = OrderRequest(
            instrument.venue_symbol,
            side,
            amount,
            "limit",
            price,
            LegOrderManager.client_order_id(leg, "validation", 0),
            instrument.market_type,
            time_in_force=tif,
            is_reduce_only=reduce_only,
            quantity_unit=instrument.quantity_unit,
        )
        manager = LegOrderManager(exchange, self.store, instrument, use_websocket=False)
        owned = _OwnedOrder(instrument, manager, request, identity)
        self.orders.append(owned)
        await self.store.append_event(
            identity,
            "testnet_baseline",
            {
                "network": "testnet",
                "instrument": {**asdict(instrument), "network": instrument.network.value},
                "account_baseline": self.baselines.get((exchange.name, instrument.venue_symbol)),
            },
        )
        owned.snapshot = await manager.execute(
            request,
            leg,
            identity,
            "close" if reduce_only else "original",
            time.monotonic() + (5 if tif == "GTC" else 60),
        )
        self.budget.observe(request.client_order_id, owned.snapshot)
        return owned

    async def refresh(self, exchange, owned):
        if (
            owned.snapshot is not None
            and owned.snapshot.status == "rejected"
            and owned.request.client_order_id not in self.budget.reservations
        ):
            return owned.snapshot
        observed = await exchange.fetch_order_snapshot(
            owned.request, owned.instrument, owned.snapshot.order_id if owned.snapshot else None
        )
        owned.snapshot = observed
        row = await self.store.get_order_row(owned.request.client_order_id)
        await owned.manager._record(row, observed)
        self.budget.observe(owned.request.client_order_id, observed)
        return observed

    async def terminal(self, exchange, owned):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            snapshot = await self.refresh(exchange, owned)
            if snapshot.is_terminal and snapshot.filled_qty_base is not None:
                return snapshot
            await asyncio.sleep(0.5)
        raise _Blocked("order outcome remains unknown; subsequent trading stopped")

    async def cancel(self, exchange, owned):
        row = await self.store.get_order_row(owned.request.client_order_id)
        owned.snapshot = await owned.manager.cancel(row, time.monotonic() + 60)
        self.budget.observe(owned.request.client_order_id, owned.snapshot)
        return await self.terminal(exchange, owned)

    async def flatten(self, exchange, instrument, owned):
        for order in owned:
            if order.snapshot is None or not order.snapshot.is_terminal:
                await self.cancel(exchange, order)
        if any(
            order.snapshot is None or not order.snapshot.is_terminal or order.snapshot.filled_qty_base is None
            for order in owned
        ):
            raise _Blocked("cannot clean up an ambiguous order")
        signed = sum((1 if order.request.side == "buy" else -1) * order.snapshot.filled_qty_base for order in owned)
        if instrument.market_type == "spot":
            signed -= sum(
                float(fee["cost"])
                for order in owned
                for fee in order.snapshot.fees
                if fee.get("currency") == instrument.base.symbol and fee.get("cost") is not None
            )
        if abs(signed) < 1e-12:
            return
        if instrument.market_type == "perp":
            position = await exchange.fetch_order_position(instrument)
            if abs(position.qty_native - signed) > 1e-12:
                raise _Blocked("account position differs from this run's confirmed fills")
        elif signed < 0:
            raise _Blocked("spot cleanup would consume a pre-existing balance")
        book = await self.book(exchange, instrument)
        side = "sell" if signed > 0 else "buy"
        quantity = _rounded(abs(signed), instrument.qty_step)
        if quantity <= 0:
            raise _Blocked(f"unsellable residual quantity: {signed}")
        closing = await self.new_order(
            exchange,
            instrument,
            side,
            quantity,
            self.price(exchange, instrument, book, side),
            reduce_only=instrument.market_type == "perp",
        )
        owned.append(closing)
        await self.terminal(exchange, closing)
        if closing.snapshot.filled_qty_base is None or abs(closing.snapshot.filled_qty_base - abs(signed)) > 1e-12:
            raise _Blocked("cleanup is incomplete; remaining exposure must be investigated")

    async def lifecycle(self, exchange, instrument, side):
        if self.halted:
            raise _Blocked("prior scenario left unresolved execution")
        self.budget.require_cleanup_reserve()
        await self.clean(exchange, instrument)
        owned = []
        start = len(self.orders)
        try:
            book = await self.book(exchange, instrument)
            quantity = self.quantity(instrument, self.price(exchange, instrument, book, "buy"))
            # Spot resting orders buy only: never offer existing inventory.
            resting = await self.new_order(
                exchange,
                instrument,
                side,
                quantity,
                self.price(exchange, instrument, book, side, resting=True),
                tif="GTC",
            )
            owned.append(resting)
            queried = await self.refresh(exchange, resting)
            if not queried.order_id:
                raise _Blocked("resting order has no confirmed server identity")
            by_client = await exchange.fetch_order_by_client_id(
                resting.request.client_order_id, instrument.venue_symbol, exchange.account_params(instrument)
            )
            if str(by_client.get("id")) != queried.order_id:
                raise _Blocked("client ID lookup does not identify the resting order")
            canceled = await self.cancel(exchange, resting)
            if canceled.filled_qty_base:
                await self.flatten(exchange, instrument, owned)
                raise _Blocked("resting order filled during cancellation; cleaned and stopped this scenario")
            book = await self.book(exchange, instrument)
            price = self.price(exchange, instrument, book, side)
            quantity = self.quantity(instrument, price)
            opening = await self.new_order(exchange, instrument, side, quantity, price)
            owned.append(opening)
            fill = await self.terminal(exchange, opening)
            if not fill.filled_qty_base:
                raise _Blocked("IOC was unfilled or rejected; trading permission is not proven")
            await self.flatten(exchange, instrument, owned)
            await self.clean(exchange, instrument)
            for order in owned:
                await self.refresh(exchange, order)
                if order.snapshot.filled_qty_base and (not order.snapshot.fills or order.snapshot.fee_usd is None):
                    raise _Blocked("complete fills and fee valuation were not confirmed")
                await self.store.update_intent_status(
                    order.intent_id, "ALL_FILLED" if order.snapshot.filled_qty_base else "REJECTED"
                )
            return {
                "symbol": instrument.venue_symbol,
                "side": side,
                "orders": [{"client_id": order.request.client_order_id, **asdict(order.snapshot)} for order in owned],
            }
        finally:
            # Include a request that raised after its durable reservation/send.
            owned = list(self.orders[start:])
            try:
                await self.flatten(exchange, instrument, owned)
                await self.clean(exchange, instrument)
            except asyncio.CancelledError:
                self.halted = True
                self.record(
                    f"{exchange.name}.{instrument.venue_symbol}.cleanup",
                    "BLOCKED",
                    "cleanup canceled; retained order identities require reconciliation",
                )
                raise
            except Exception as exc:
                self.halted = True
                self.record(
                    f"{exchange.name}.{instrument.venue_symbol}.cleanup", "BLOCKED", f"{type(exc).__name__}: {exc}"
                )
                raise _Blocked("cleanup could not establish a flat account; run halted") from exc

    async def pair_cycle(self, direction):
        if self.halted:
            raise _Blocked("prior scenario left unresolved execution")
        instruments = [self.selected.get((venue, "perp")) for venue in ("arcus", "hyperliquid")]
        if any(item is None for item in instruments) or instruments[0].base != instruments[1].base:
            raise _Blocked("no common eligible perpetual pair")
        self.budget.require_cleanup_reserve(2)
        quantities = []
        for instrument in instruments:
            exchange = self.exchanges[instrument.venue]
            await self.clean(exchange, instrument)
            book = await self.book(exchange, instrument)
            quantities.append(self.quantity(instrument, self.price(exchange, instrument, book, "buy")))
        quantity = _rounded(max(quantities), _common_step(*(item.qty_step for item in instruments)), up=True)
        result = None
        try:
            result = await TestnetCanary(self.exchanges, self.store, timeout_seconds=60).run(
                CanaryRequest(
                    instruments[0].base.symbol,
                    "arcus",
                    "hyperliquid",
                    quantity,
                    direction=direction,
                    symbol_a=instruments[0].venue_symbol,
                    symbol_b=instruments[1].venue_symbol,
                    max_notional_usd=self.budget.maximum_order,
                    confirmation=CONFIRMATION_TOKEN,
                )
            )
        finally:
            if result is None or result.status not in {"CLOSED", "REJECTED"}:
                self.halted = True
                self.record(
                    "pair.recovery_required",
                    "BLOCKED",
                    {
                        "direction": direction,
                        "cycle_id": result.cycle_id if result else None,
                        "note": "retain durable identities; do not resend or clear manual review",
                    },
                )
        for phase in (result.opening, result.closing):
            if phase:
                for leg in phase.legs:
                    self.budget.observe(
                        leg.client_order_id,
                        OrderSnapshot(
                            leg.order_id,
                            leg.status,
                            leg.filled_qty_base,
                            leg.avg_price,
                            leg.fee_usd,
                        ),
                    )
        if result.status != "CLOSED":
            if result.status != "REJECTED":
                self.halted = True
            raise _Blocked(f"canary {result.status}: {result.error}")
        for instrument in instruments:
            await self.clean(self.exchanges[instrument.venue], instrument)
        return asdict(result)

    async def trading(self):
        for (venue, product), instrument in list(self.selected.items()):
            exchange = self.exchanges[venue]
            for kind in ("orders", "fills"):
                self.ws_tasks.append(asyncio.create_task(self.watch_account(exchange, instrument, kind)))
            if product == "perp":
                await asyncio.sleep(1)
                for side in ("buy", "sell"):
                    await self.check(
                        f"{venue}.{product}.{side}_roundtrip",
                        lambda ex=exchange, inst=instrument, s=side: self.lifecycle(ex, inst, s),
                        timeout=300,
                    )
        for direction in ("buy_a_sell_b", "buy_b_sell_a"):
            await self.check(f"pair.{direction}", lambda d=direction: self.pair_cycle(d), timeout=300)
        instrument = self.selected.get(("hyperliquid", "spot"))
        if instrument:
            await self.check(
                "hyperliquid.spot.buy_sell_roundtrip",
                lambda: self.lifecycle(self.exchanges["hyperliquid"], instrument, "buy"),
                timeout=300,
            )
        for (venue, symbol, kind), events in self.ws_events.items():
            order_ids = {
                order.snapshot.order_id
                for order in self.orders
                if order.instrument.venue == venue
                and order.instrument.venue_symbol == symbol
                and order.snapshot
                and order.snapshot.order_id
            }
            matches = [
                event
                for event in events
                if str(event.get("id" if kind == "orders" else "orderId", event.get("order"))) in order_ids
            ]
            self.record(
                f"{venue}.{symbol}.ws_{kind}_evidence",
                "PASS" if matches else "BLOCKED",
                {"matching_events": len(matches), "events_received": len(events)},
            )
        for (venue, _), instrument in self.selected.items():
            await self.check(
                f"{venue}.{instrument.venue_symbol}.final_account",
                lambda ex=self.exchanges[venue], inst=instrument: self.clean(ex, inst),
            )

    async def readback(self):
        reader = PersistenceStore(self.output / "execution.db", self.output / "audit")
        await reader.initialize()
        count = 0
        try:
            for intent in await reader.list_intents(limit=1000):
                for leg in await reader.get_legs_for_intent(intent.intent_id):
                    if not leg.execution_context_json:
                        raise _Blocked("durable leg context is missing")
                    context = deserialize_leg_context(leg.execution_context_json)
                    for row in await reader.get_orders_for_leg(leg.leg_id):
                        request = LegOrderManager.request_from_json(row.request_json)
                        saved = LegOrderManager.snapshot_from_json(row.snapshot_json) if row.snapshot_json else None
                        if (
                            saved
                            and saved.status == "rejected"
                            and request.client_order_id not in self.budget.reservations
                        ):
                            count += 1
                            continue
                        snapshot = await self.exchanges[context.venue].fetch_order_snapshot(
                            request, context.instrument, saved.order_id if saved else None
                        )
                        if not snapshot.is_terminal or snapshot.filled_qty_base is None:
                            raise _Blocked("restart readback found an unresolved order")
                        count += 1
            cycles = await reader.list_arbitrage_cycles()
            recovery = ArbitrageRecovery(reader, execution_mode="testnet", testnet_confirmed=True)
            for cycle in cycles:
                if cycle["status"] != "CLOSED":
                    raise _Blocked("persisted arbitrage cycle is not closed; recovery/manual review required")
                loaded = recovery._load_cycle(cycle, self.exchanges)
                legs = await reader.get_arbitrage_cycle_legs(cycle["cycle_id"])
                if not legs:
                    raise _Blocked("persisted arbitrage legs are missing")
                for leg in legs:
                    instrument = loaded.pair.instrument_a if leg["role"].startswith("a:") else loaded.pair.instrument_b
                    request = HedgedExecutor.request_for_leg(loaded, leg)
                    snapshot = await self.exchanges[leg["venue"]].fetch_order_snapshot(
                        request, instrument, leg.get("venue_order_id")
                    )
                    if not snapshot.is_terminal or snapshot.filled_qty_base is None:
                        raise _Blocked("persisted arbitrage order is not terminal on the venue")
                if not await reader.get_arbitrage_fills(cycle["cycle_id"]):
                    raise _Blocked("persisted arbitrage fills are missing")
            return {"orders": count, "cycles": len(cycles), "resubmissions": 0}
        finally:
            await reader.close()
