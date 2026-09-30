"""Bounded two-leg execution with durable intent and testnet-only real adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, replace
from math import isfinite
from typing import Any

from ccxt.base.errors import AuthenticationError, InsufficientFunds, InvalidOrder, OrderNotFound, PermissionDenied

from src.exchange.order import OrderRequest, OrderSnapshot, parse_order_snapshot
from src.market.instrument import NetworkType

from .lifecycle import CycleTransitionError, cycle_unhedged_qty_base, transition_cycle
from .models import ArbCycle

ArbOrderRequest = OrderRequest
TERMINAL_STATUSES = frozenset({"closed", "filled", "canceled", "cancelled", "expired", "rejected"})


@dataclass(frozen=True)
class LegResult:
    role: str
    venue: str
    client_order_id: str
    order_id: str | None
    status: str
    filled_qty_base: float | None
    avg_price: float | None
    fee_usd: float | None
    error: str | None = None


@dataclass(frozen=True)
class HedgedExecutionResult:
    cycle: ArbCycle
    legs: tuple[LegResult, ...]
    status: str
    error: str | None = None


class HedgedExecutor:
    """Execute one bounded cycle; ambiguous submissions are queried, never resent.

    Offline submissions require ``simulate=True`` and MockExchange adapters.
    Real submissions additionally require the explicit testnet mode, confirmation,
    a durable store, and verified Arcus/Hyperliquid/Binance testnet instruments.
    """

    def __init__(
        self,
        exchanges: dict[str, Any],
        store: Any | None = None,
        *,
        dry_run: bool = True,
        simulate: bool = False,
        execution_mode: str = "offline",
        testnet_confirmed: bool = False,
        timeout_seconds: float = 2.0,
        verify_flat_on_close: bool = False,
    ) -> None:
        if execution_mode not in {"offline", "testnet", "mainnet"}:
            raise ValueError("execution_mode must be offline, testnet, or mainnet")
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        self.exchanges = exchanges
        self.store = store
        self.dry_run = dry_run
        self.simulate = simulate
        self.execution_mode = execution_mode
        self.testnet_confirmed = testnet_confirmed
        self.timeout_seconds = timeout_seconds
        self.verify_flat_on_close = verify_flat_on_close
        self._started: set[tuple[str, str]] = set()

    @staticmethod
    def client_order_id(cycle_id: str, role: str, attempt: int = 0) -> str:
        digest = hashlib.sha256(f"{cycle_id}:{role}:{attempt}".encode()).hexdigest()[:32]
        return f"0x{digest}"

    def _assert_submission_allowed(self, cycle: ArbCycle | None = None) -> None:
        if self.dry_run:
            raise RuntimeError("arbitrage executor is in dry_run mode")
        if self.execution_mode == "mainnet":
            raise RuntimeError("mainnet arbitrage execution is disabled in stage three")
        if cycle is not None and any(
            instrument is not None and (instrument.is_inverse or instrument.contract_size != 1)
            for instrument in (cycle.pair.instrument_a, cycle.pair.instrument_b)
        ):
            raise RuntimeError("arbitrage execution requires linear unit contracts")
        if self.execution_mode == "offline":
            if not self.simulate:
                raise RuntimeError("offline execution requires explicit simulate=True")
            if any(exchange.__class__.__module__ != "src.exchange.mock" for exchange in self.exchanges.values()):
                raise RuntimeError("offline simulation requires src.exchange.mock.MockExchange adapters")
            return
        if not self.testnet_confirmed:
            raise RuntimeError("testnet execution requires testnet_confirmed=True")
        if any(
            getattr(exchange, "network_type", None) is not NetworkType.TESTNET for exchange in self.exchanges.values()
        ):
            raise RuntimeError("testnet execution requires testnet exchange adapters")
        if cycle is not None:
            pair = cycle.pair
            for role, venue, symbol, instrument in (
                ("a", pair.venue_a, pair.symbol_a, pair.instrument_a),
                ("b", pair.venue_b, pair.symbol_b, pair.instrument_b),
            ):
                if instrument is None:
                    raise RuntimeError(f"testnet execution requires instrument metadata for leg {role}")
                if (
                    instrument.network is not NetworkType.TESTNET
                    or instrument.venue != venue
                    or instrument.venue_symbol != symbol
                    or instrument.market_type != "perp"
                    or instrument.base.symbol != pair.base
                    or instrument.is_inverse
                    or instrument.contract_size != 1
                    or instrument.listing_status != "trading"
                ):
                    raise RuntimeError(f"{venue}: testnet execution requires matching linear unit perp instruments")
                exchange = self.exchanges.get(venue)
                if exchange is None:
                    raise RuntimeError(f"missing exchange adapter: {venue}")
                capabilities = exchange.order_capabilities(instrument)
                if not capabilities.has_client_order_id or "IOC" not in capabilities.time_in_force:
                    raise RuntimeError(f"{venue}: client order IDs and IOC are required for testnet execution")
            if (
                not {pair.venue_a, pair.venue_b} <= {"arcus", "hyperliquid", "binance"}
                or len({pair.venue_a, pair.venue_b}) != 2
                or pair.market_type != "perp"
                or pair.contract_size_a != 1
                or pair.contract_size_b != 1
                or pair.hedge_ratio != 1
            ):
                raise RuntimeError(
                    "testnet supports only unit linear perp pairs across Arcus, Hyperliquid, and Binance"
                )
        if self.store is None:
            raise RuntimeError("testnet execution requires a durable persistence store")

    @staticmethod
    def _validate_numbers(quantity: float, buy_price: float, sell_price: float) -> None:
        if any(not isfinite(value) or value <= 0 for value in (quantity, buy_price, sell_price)):
            raise ValueError("quantity and limit prices must be positive and finite")

    async def open_cycle(
        self, cycle: ArbCycle, *, buy_price: float, sell_price: float, target_qty: float | None = None
    ) -> HedgedExecutionResult:
        quantity = cycle.target_qty if target_qty is None else target_qty
        self._validate_numbers(quantity, buy_price, sell_price)
        if self.dry_run:
            return HedgedExecutionResult(cycle, (), "DRY_RUN")
        try:
            self._assert_submission_allowed(cycle)
            current = transition_cycle(replace(cycle, target_qty=quantity), "prechecked")
        except (CycleTransitionError, RuntimeError) as exc:
            return HedgedExecutionResult(cycle, (), "REJECTED", str(exc))
        if (cycle.cycle_id, "open") in self._started:
            return HedgedExecutionResult(cycle, (), "RECOVERY", "cycle opening was already attempted")
        self._started.add((cycle.cycle_id, "open"))
        if self.store is not None:
            if await self.store.get_arbitrage_cycle(cycle.cycle_id) is not None:
                return HedgedExecutionResult(cycle, (), "RECOVERY", "persisted cycle requires reconciliation")
            await self._create_cycle(cycle, quantity)
        await self._save_cycle(current)
        current = await self._transition(current, "opening")
        specs = self._leg_specs(current, quantity, buy_price, sell_price, reduce_only=False)
        rows = [await self._prepare_leg(current, spec, "open") for spec in specs]
        results = await self._execute_rows(current, rows)
        current.filled_qty_a, current.filled_qty_b = (self._filled(result) for result in results)
        if not all(self._confirmed(result) for result in results):
            return await self._recovery(current, results, "opening order confirmation is incomplete")
        if current.filled_qty_a == 0 and current.filled_qty_b == 0:
            current = await self._transition(current, "manual_review", "both opening legs were unfilled")
            return HedgedExecutionResult(current, tuple(results), "REJECTED", "both opening legs were unfilled")
        # All successful openings pass through HEDGING, including balanced fills.
        current = await self._transition(current, "hedging")
        if cycle_unhedged_qty_base(current) > 1e-12:
            hedge = await self._hedge_delta(current, buy_price, sell_price)
            results.append(hedge)
            if hedge.role == "a":
                current.filled_qty_a += self._filled(hedge)
            else:
                current.filled_qty_b += self._filled(hedge)
            if not self._confirmed(hedge) or cycle_unhedged_qty_base(current) > 1e-12:
                return await self._recovery(current, results, "residual hedge remains incomplete")
        current = await self._transition(current, "open")
        return HedgedExecutionResult(current, tuple(results), "OPEN")

    async def close_cycle(self, cycle: ArbCycle, *, buy_price: float, sell_price: float) -> HedgedExecutionResult:
        self._validate_numbers(max(cycle.filled_qty_a, cycle.filled_qty_b), buy_price, sell_price)
        if self.dry_run:
            return HedgedExecutionResult(cycle, (), "DRY_RUN")
        try:
            self._assert_submission_allowed(cycle)
            current = transition_cycle(cycle, "closing")
        except (CycleTransitionError, RuntimeError) as exc:
            return HedgedExecutionResult(cycle, (), "REJECTED", str(exc))
        if (cycle.cycle_id, "close") in self._started:
            return HedgedExecutionResult(cycle, (), "RECOVERY", "cycle closing was already attempted")
        self._started.add((cycle.cycle_id, "close"))
        if self.store is not None:
            saved = await self.store.get_arbitrage_cycle(cycle.cycle_id)
            if saved is None:
                if self.execution_mode == "testnet":
                    raise RuntimeError("cannot close a testnet cycle without its persisted opening")
                await self._create_cycle(cycle, cycle.target_qty)
            elif saved["status"].upper() != "OPEN":
                return HedgedExecutionResult(cycle, (), "RECOVERY", "persisted cycle is not open")
        await self._save_cycle(current)
        specs = self._leg_specs(current, 0, buy_price, sell_price, reduce_only=True)
        rows = []
        for role, venue, symbol, side, _, price in specs:
            quantity = cycle.filled_qty_a if role == "a" else cycle.filled_qty_b
            if quantity > 0:
                rows.append(await self._prepare_leg(current, (role, venue, symbol, side, quantity, price), "close"))
        results = await self._execute_rows(current, rows)
        for result in results:
            if result.role == "a":
                current.filled_qty_a = max(0.0, cycle.filled_qty_a - self._filled(result))
            else:
                current.filled_qty_b = max(0.0, cycle.filled_qty_b - self._filled(result))
        if (
            not all(self._confirmed(result) for result in results)
            or current.filled_qty_a > 1e-12
            or current.filled_qty_b > 1e-12
        ):
            return await self._recovery(current, results, "close confirmation or residual position requires recovery")
        if self.verify_flat_on_close:
            try:
                await self._verify_closed_accounts(current)
            except Exception as exc:
                return await self._recovery(
                    current, results, f"final account verification failed: {type(exc).__name__}"
                )
        current = await self._transition(current, "closed")
        return HedgedExecutionResult(current, tuple(results), "CLOSED")

    async def _verify_closed_accounts(self, cycle: ArbCycle) -> None:
        """Confirm actual flat accounts before persisting the terminal state."""
        for venue, instrument in (
            (cycle.pair.venue_a, cycle.pair.instrument_a),
            (cycle.pair.venue_b, cycle.pair.instrument_b),
        ):
            if instrument is None:
                raise ValueError(f"{venue}: closing account verification requires instrument metadata")
            exchange = self.exchanges[venue]
            position = await asyncio.wait_for(exchange.fetch_order_position(instrument), self.timeout_seconds)
            if (
                position.symbol != instrument.venue_symbol
                or not isfinite(position.qty_native)
                or position.qty_native != 0
            ):
                raise ValueError(f"{venue}:{instrument.venue_symbol}: closing position is not flat")
            orders = await asyncio.wait_for(
                exchange.fetch_open_orders(instrument.venue_symbol, params=exchange.account_params(instrument)),
                self.timeout_seconds,
            )
            if not isinstance(orders, list) or orders:
                raise ValueError(f"{venue}:{instrument.venue_symbol}: closing orders remain unverified")

    @staticmethod
    def _leg_specs(cycle: ArbCycle, quantity: float, buy_price: float, sell_price: float, *, reduce_only: bool):
        buy_a = cycle.direction == "buy_a_sell_b"
        side_a = "sell" if buy_a == reduce_only else "buy"
        side_b = "buy" if buy_a == reduce_only else "sell"
        return [
            (
                "a",
                cycle.pair.venue_a,
                cycle.pair.symbol_a,
                side_a,
                quantity,
                buy_price if side_a == "buy" else sell_price,
            ),
            (
                "b",
                cycle.pair.venue_b,
                cycle.pair.symbol_b,
                side_b,
                quantity,
                buy_price if side_b == "buy" else sell_price,
            ),
        ]

    async def _prepare_leg(self, cycle: ArbCycle, spec: tuple, purpose: str) -> dict:
        role, venue, symbol, side, quantity, price = spec
        row = {
            "leg_id": f"{cycle.cycle_id}-{role}-{purpose}-0",
            "cycle_id": cycle.cycle_id,
            "role": f"{role}:{purpose}:0",
            "venue": venue,
            "symbol": symbol,
            "side": side,
            "target_qty_base": quantity,
            "client_order_id": self.client_order_id(cycle.cycle_id, f"{role}:{purpose}"),
            "status": "PENDING_SEND",
            "filled_qty_base": 0.0,
            "venue_order_id": None,
        }
        if self.store is not None:
            existing = await self.store.get_arbitrage_cycle_leg(row["leg_id"])
            if existing is not None:
                return {**existing, "price": price, "existing": True}
            await self.store.create_arbitrage_cycle_leg(**row)
        return {**row, "price": price, "existing": False}

    @staticmethod
    def request_for_leg(cycle: ArbCycle, row: dict) -> OrderRequest:
        """Rebuild the stable identity for read-only reconciliation of a saved leg."""
        role = row["role"].split(":", 1)[0]
        instrument = cycle.pair.instrument_a if role == "a" else cycle.pair.instrument_b
        return OrderRequest(
            row["symbol"],
            row["side"],
            row["target_qty_base"],
            "limit",
            row.get("price"),
            row["client_order_id"],
            cycle.pair.market_type,
            "IOC",
            ":close:" in row["role"],
            quantity_unit=instrument.quantity_unit if instrument is not None else "base",
        )

    async def _submit_leg(self, cycle: ArbCycle, row: dict) -> LegResult:
        if row.get("existing"):
            return await self.reconcile_leg(cycle, row)
        request = self.request_for_leg(cycle, row)
        role = row["role"].split(":", 1)[0]
        instrument = cycle.pair.instrument_a if role == "a" else cycle.pair.instrument_b
        if self.store is not None:
            await self.store.update_arbitrage_cycle_leg(row["leg_id"], status="UNKNOWN", sent_at=str(time.time()))
        try:
            snapshot = await asyncio.wait_for(
                self.exchanges[row["venue"]].submit_order(request, instrument), self.timeout_seconds
            )
        except (AuthenticationError, PermissionDenied, InsufficientFunds, InvalidOrder) as exc:
            # Only the submission call belongs inside this rejection boundary.
            # OrderNotFound is an identity-query ambiguity, never proof of zero fills.
            if not isinstance(exc, OrderNotFound):
                rejected = await self.record_snapshot(cycle, row, OrderSnapshot(None, "rejected", 0.0, None))
                return replace(rejected, error=f"{type(exc).__name__}: order rejected")
            result = await self.reconcile_leg(cycle, row)
            return replace(result, error=str(exc)) if not self._confirmed(result) else result
        except Exception as exc:
            result = await self.reconcile_leg(cycle, row)
            return replace(result, error=str(exc)) if not self._confirmed(result) else result
        return await self._confirm_leg(cycle, row, snapshot)

    async def _execute_rows(self, cycle: ArbCycle, rows: list[dict]) -> list[LegResult]:
        outcomes = await asyncio.gather(*(self._submit_leg(cycle, row) for row in rows), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        return outcomes

    async def reconcile_leg(self, cycle: ArbCycle, row: dict) -> LegResult:
        """Query a persisted leg by exchange/client ID; this never sends an order."""
        snapshot = OrderSnapshot(row.get("venue_order_id"), "unknown", None, row.get("avg_price"), row.get("fee_usd"))
        return await self._confirm_leg(cycle, row, snapshot)

    async def _confirm_leg(self, cycle: ArbCycle, row: dict, snapshot: Any) -> LegResult:
        exchange = self.exchanges[row["venue"]]
        request = self.request_for_leg(cycle, row)
        instrument = cycle.pair.instrument_a if row["role"].startswith("a:") else cycle.pair.instrument_b
        deadline = time.monotonic() + self.timeout_seconds
        latest = await self.record_snapshot(cycle, row, snapshot)
        if await self._confirmation_complete(cycle, row, latest):
            return latest
        tasks: dict[asyncio.Task, str] = {}

        def start(method: str) -> None:
            callback = getattr(exchange, method, None)
            if callback is not None:
                tasks[asyncio.create_task(callback(request.symbol))] = method

        if self.execution_mode == "testnet" and self._supports_user_fills(exchange):
            start("watch_orders")
            start("watch_user_fills")
        polled_once = False
        try:
            # Persisting the initial UNKNOWN snapshot can consume the whole
            # short test timeout. An ambiguous submission still gets one
            # bounded identity query before the leg is left unresolved.
            while not polled_once or time.monotonic() < deadline:
                polled_once = True
                try:
                    polled = await asyncio.wait_for(
                        exchange.fetch_order_snapshot(request, instrument, latest.order_id),
                        max(0.001, min(5.0, deadline - time.monotonic())),
                    )
                except (AttributeError, NotImplementedError):
                    if not tasks:
                        break
                except Exception:
                    pass
                else:
                    latest = await self.record_snapshot(cycle, row, polled)
                    if await self._confirmation_complete(cycle, row, latest):
                        return latest
                if tasks:
                    done, _ = await asyncio.wait(tasks, timeout=min(0.05, max(0, deadline - time.monotonic())))
                    for task in done:
                        method = tasks.pop(task)
                        try:
                            events = task.result()
                        except Exception:
                            continue
                        for event in events if isinstance(events, list) else [events]:
                            if not self._matches_order(event, latest.order_id, request.client_order_id):
                                continue
                            if method == "watch_orders":
                                update = parse_order_snapshot(event, instrument)
                                latest = await self.record_snapshot(cycle, row, update)
                            else:
                                await self._record_fills(cycle, row, [event])
                        if await self._confirmation_complete(cycle, row, latest):
                            return latest
                        start(method)
                else:
                    await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._confirmed(latest):
            return replace(latest, error="trade details remain incomplete")
        return replace(latest, status="unknown", error="order confirmation deadline exceeded")

    async def _confirmation_complete(self, cycle: ArbCycle, row: dict, result: LegResult) -> bool:
        if not self._confirmed(result):
            return False
        if (
            self.execution_mode != "testnet"
            or not result.filled_qty_base
            or not self._supports_user_fills(self.exchanges[row["venue"]])
        ):
            return True
        fills = await self.store.get_arbitrage_fills(cycle.cycle_id, row["leg_id"])
        return sum(fill["quantity"] for fill in fills) >= result.filled_qty_base - 1e-12

    @staticmethod
    def _supports_user_fills(exchange: Any) -> bool:
        # Tests and specialized adapters may attach a private stream at the
        # instance level; the base stub itself is intentionally unsupported.
        return bool(
            getattr(exchange, "supports_user_fills", False) or "watch_user_fills" in getattr(exchange, "__dict__", {})
        )

    @staticmethod
    def _matches_order(event: Any, order_id: str | None, client_id: str) -> bool:
        if not isinstance(event, dict):
            return False
        event_id = event.get("order", event.get("orderId", event.get("id")))
        event_client = event.get("clientOrderId", event.get("clientId"))
        return (order_id is not None and event_id is not None and str(event_id) == str(order_id)) or (
            event_client is not None and str(event_client) == client_id
        )

    async def record_snapshot(self, cycle: ArbCycle, row: dict, snapshot: Any) -> LegResult:
        """Persist cumulative order evidence and deduplicated real trade IDs."""
        filled = getattr(snapshot, "filled_qty_base", None)
        if filled is not None and (not isfinite(filled) or filled < 0 or filled > row["target_qty_base"] + 1e-12):
            raise ValueError("invalid cumulative fill quantity")
        status = str(getattr(snapshot, "status", "unknown")).lower()
        order_id = getattr(snapshot, "order_id", None) or row.get("venue_order_id")
        average = getattr(snapshot, "avg_price", None)
        fee = getattr(snapshot, "fee_usd", None)
        if self.store is not None:
            saved = await self.store.get_arbitrage_cycle_leg(row["leg_id"])
            if saved is None:
                raise RuntimeError("cannot record an order without a persisted leg")
            if saved["filled_qty_base"] > (filled or 0):
                filled, average = saved["filled_qty_base"], saved["avg_price"]
            if saved["status"].lower() in TERMINAL_STATUSES and status not in TERMINAL_STATUSES:
                status = saved["status"].lower()
                filled = saved["filled_qty_base"]
                average = saved["avg_price"]
                fee = saved["fee_usd"]
            order_id = order_id or saved["venue_order_id"]
            fields = {"status": status.upper(), "venue_order_id": order_id}
            if filled is not None:
                fields["filled_qty_base"] = filled
            if average is not None:
                fields["avg_price"] = average
            if fee is not None:
                fields["fee_usd"] = fee
            await self._record_fills(cycle, row, getattr(snapshot, "fills", []))
            await self.store.update_arbitrage_cycle_leg(row["leg_id"], **fields)
        return LegResult(
            row["role"].split(":", 1)[0], row["venue"], row["client_order_id"], order_id, status, filled, average, fee
        )

    async def _record_fills(self, cycle: ArbCycle, row: dict, fills: list[dict]) -> None:
        if self.store is None:
            return
        for fill in fills:
            trade_id = fill.get("tradeId", fill.get("id"))
            if trade_id is None:
                continue
            quantity, price = float(fill["amount"]), float(fill["price"])
            if not isfinite(quantity) or not isfinite(price) or quantity <= 0 or price <= 0:
                raise ValueError("invalid trade quantity or price")
            await self.store.insert_arbitrage_fill(
                fill_id=hashlib.sha256(f"{row['venue']}:{trade_id}".encode()).hexdigest(),
                cycle_id=cycle.cycle_id,
                leg_id=row["leg_id"],
                venue=row["venue"],
                trade_id=str(trade_id),
                quantity=quantity,
                price=price,
                exchange_timestamp=fill.get("timestamp"),
            )

    async def _hedge_delta(self, cycle: ArbCycle, buy_price: float, sell_price: float) -> LegResult:
        role = "a" if cycle.filled_qty_a < cycle.filled_qty_b else "b"
        spec = next(
            spec
            for spec in self._leg_specs(cycle, cycle_unhedged_qty_base(cycle), buy_price, sell_price, reduce_only=False)
            if spec[0] == role
        )
        row = await self._prepare_leg(cycle, spec, "hedge")
        return await self._submit_leg(cycle, row)

    @staticmethod
    def _filled(result: LegResult) -> float:
        return 0.0 if result.filled_qty_base is None else result.filled_qty_base

    @staticmethod
    def _confirmed(result: LegResult) -> bool:
        return result.status in TERMINAL_STATUSES and result.filled_qty_base is not None

    async def _create_cycle(self, cycle: ArbCycle, quantity: float) -> None:
        pair_context = asdict(cycle.pair)
        for role in ("a", "b"):
            instrument = pair_context[f"instrument_{role}"]
            if instrument is not None:
                instrument["network"] = instrument["network"].value
        await self.store.create_arbitrage_cycle(
            cycle_id=cycle.cycle_id,
            base=cycle.pair.base,
            market_type=cycle.pair.market_type,
            direction=cycle.direction,
            venue_buy=cycle.pair.venue_a,
            venue_sell=cycle.pair.venue_b,
            symbol_buy=cycle.pair.symbol_a,
            symbol_sell=cycle.pair.symbol_b,
            target_qty_base=quantity,
            status=cycle.status.upper(),
            execution_context_json=json.dumps({"execution_mode": self.execution_mode, "pair": pair_context}),
        )

    async def _save_cycle(self, cycle: ArbCycle, reason: str | None = None) -> None:
        if self.store is not None:
            fields: dict[str, Any] = {"status": cycle.status.upper(), "failure_reason": reason}
            if cycle.status == "open":
                fields["opened_qty_base"] = min(cycle.filled_qty_a, cycle.filled_qty_b)
            elif cycle.status == "closed":
                saved = await self.store.get_arbitrage_cycle(cycle.cycle_id)
                fields["closed_qty_base"] = saved["opened_qty_base"]
            await self.store.update_arbitrage_cycle(cycle.cycle_id, **fields)

    async def _transition(self, cycle: ArbCycle, status: str, reason: str | None = None) -> ArbCycle:
        current = transition_cycle(cycle, status)
        await self._save_cycle(current, reason)
        return current

    async def _recovery(self, cycle: ArbCycle, results: list[LegResult], reason: str) -> HedgedExecutionResult:
        current = await self._transition(cycle, "unhedged", reason)
        return HedgedExecutionResult(current, tuple(results), "RECOVERY", reason)


__all__ = ["ArbOrderRequest", "HedgedExecutionResult", "HedgedExecutor", "LegResult"]
