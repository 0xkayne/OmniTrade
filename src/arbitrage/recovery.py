"""Bounded, read-only venue reconciliation after an arbitrage restart."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from math import isfinite
from typing import Any

from src.exchange.order import OrderRequest
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType

from .executor import HedgedExecutor
from .lifecycle import cycle_net_delta_base
from .models import ArbCycle, ArbPair

_TERMINAL = {"closed", "filled", "canceled", "cancelled", "expired", "rejected"}
_PENDING = {"open", "pending", "new", "partially_filled"}
_EPSILON = 1e-12


@dataclass(frozen=True)
class RecoveryResult:
    cycle_id: str
    status: str
    reason: str | None = None
    cycle: ArbCycle | None = None
    net_qty_base: float | None = None


class ArbitrageRecovery:
    """Rebuild persisted exposure without submitting or cancelling any order.

    Legacy cycles without saved pair/network context require manual review.
    A recovered OPEN cycle may be passed to an explicitly authorized executor;
    a RECOVERY result still has active orders or unmatched exposure.
    """

    def __init__(
        self,
        store: Any,
        *,
        simulate: bool = False,
        execution_mode: str = "offline",
        testnet_confirmed: bool = False,
        max_cycles: int = 100,
        timeout_seconds: float = 2.0,
    ) -> None:
        if max_cycles <= 0 or not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("recovery bounds must be positive")
        self.store = store
        self.simulate = simulate
        self.execution_mode = execution_mode
        self.testnet_confirmed = testnet_confirmed
        self.max_cycles = max_cycles
        self.timeout_seconds = timeout_seconds

    async def recover(self, exchanges: dict[str, Any]) -> list[RecoveryResult]:
        """Query saved identities and classify exposure, with no retries of sends."""

        gate = HedgedExecutor(
            exchanges,
            store=self.store,
            dry_run=False,
            simulate=self.simulate,
            execution_mode=self.execution_mode,
            testnet_confirmed=self.testnet_confirmed,
        )
        gate._assert_submission_allowed()
        rows = await self.store.list_unfinished_arbitrage_cycles()
        results = []
        for row in rows[: self.max_cycles]:
            try:
                result = await self._recover_cycle(row, exchanges)
            except Exception as exc:
                result = RecoveryResult(row["cycle_id"], "MANUAL_REVIEW", str(exc))
            await self.store.update_arbitrage_cycle(
                result.cycle_id,
                status=result.status,
                failure_reason=result.reason,
            )
            results.append(result)
        return results

    def _load_cycle(self, row: dict, exchanges: dict[str, Any]) -> ArbCycle:
        context_json = row.get("execution_context_json")
        if not context_json:
            raise ValueError("saved cycle lacks execution context")
        context = json.loads(context_json)
        if context["execution_mode"] != self.execution_mode:
            raise ValueError("saved execution mode does not match recovery mode")
        pair_data = dict(context["pair"])
        for role in ("a", "b"):
            raw_instrument = pair_data.get(f"instrument_{role}")
            if not raw_instrument:
                raise ValueError(f"saved cycle lacks instrument context for leg {role}")
            instrument_data = dict(raw_instrument)
            instrument_data["base"] = Asset(**instrument_data["base"])
            instrument_data["quote"] = Asset(**instrument_data["quote"])
            if instrument_data.get("settlement_asset"):
                instrument_data["settlement_asset"] = Asset(**instrument_data["settlement_asset"])
            instrument_data["network"] = NetworkType(instrument_data["network"])
            instrument = Instrument(**instrument_data)
            venue, symbol = pair_data[f"venue_{role}"], pair_data[f"symbol_{role}"]
            exchange = exchanges.get(venue)
            if exchange is None:
                raise ValueError(f"missing exchange for saved venue {venue}")
            network = getattr(exchange, "network_type", None)
            if getattr(network, "value", network) != instrument.network.value:
                raise ValueError(f"saved network does not match adapter for {venue}")
            if (
                instrument.venue != venue
                or instrument.venue_symbol != symbol
                or instrument.base.symbol != pair_data["base"]
                or instrument.market_type != pair_data["market_type"]
            ):
                raise ValueError(f"saved instrument does not match pair for leg {role}")
            pair_data[f"instrument_{role}"] = instrument
        pair = ArbPair(**pair_data)
        if pair.base != row["base"] or pair.market_type != row["market_type"]:
            raise ValueError("saved pair does not match cycle")
        if (pair.venue_a, pair.venue_b, pair.symbol_a, pair.symbol_b) != (
            row["venue_buy"],
            row["venue_sell"],
            row["symbol_buy"],
            row["symbol_sell"],
        ):
            raise ValueError("saved pair venues or symbols do not match cycle")
        if row["direction"] not in {"buy_a_sell_b", "buy_b_sell_a"}:
            raise ValueError("saved cycle direction is invalid")
        return ArbCycle(row["cycle_id"], pair, row["direction"], row["target_qty_base"])

    async def _recover_cycle(self, row: dict, exchanges: dict[str, Any]) -> RecoveryResult:
        cycle = self._load_cycle(row, exchanges)
        legs = await self.store.get_arbitrage_cycle_legs(cycle.cycle_id)
        if not legs:
            raise ValueError("saved cycle has no persisted orders")
        quantities = {"a": 0.0, "b": 0.0}
        opened = {"a": 0.0, "b": 0.0}
        closed = {"a": 0.0, "b": 0.0}
        roles_seen: set[str] = set()
        pending = False
        failures = []
        for leg in legs:
            role = leg["role"].split(":", 1)[0]
            if role not in quantities:
                raise ValueError(f"unsupported saved leg role {leg['role']}")
            venue, symbol = getattr(cycle.pair, f"venue_{role}"), getattr(cycle.pair, f"symbol_{role}")
            if leg["venue"] != venue or leg["symbol"] != symbol or leg["side"] not in {"buy", "sell"}:
                raise ValueError("saved leg does not match pair")
            if not leg.get("venue_order_id") and not leg.get("client_order_id"):
                raise ValueError("saved leg lacks an order identity")
            opening_side = "buy" if (role == "a") == (cycle.direction == "buy_a_sell_b") else "sell"
            reducing = leg["side"] != opening_side
            request = OrderRequest(
                symbol=symbol,
                side=leg["side"],
                amount=leg["target_qty_base"],
                order_type="limit",
                price=None,
                client_order_id=leg.get("client_order_id") or "",
                product=cycle.pair.market_type,
                time_in_force="IOC",
                is_reduce_only=reducing,
            )
            try:
                snapshot = await asyncio.wait_for(
                    exchanges[venue].fetch_order_snapshot(
                        request,
                        getattr(cycle.pair, f"instrument_{role}"),
                        leg.get("venue_order_id"),
                    ),
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:
                await self.store.update_arbitrage_cycle_leg(leg["leg_id"], error_msg=str(exc))
                failures.append(f"{leg['leg_id']}: {exc}")
                continue
            status = str(snapshot.status).lower()
            quantity = snapshot.filled_qty_base
            if snapshot.order_id and leg.get("venue_order_id") and snapshot.order_id != leg["venue_order_id"]:
                raise ValueError(f"order identity changed for leg {leg['leg_id']}")
            await self._persist_fills(cycle.cycle_id, leg, snapshot.fills)
            if quantity is None or not isfinite(quantity) or quantity < 0:
                failures.append(f"unknown filled quantity for leg {leg['leg_id']}")
                continue
            if status not in _TERMINAL | _PENDING:
                failures.append(f"unknown order status for leg {leg['leg_id']}")
                continue
            if quantity > leg["target_qty_base"] + _EPSILON:
                raise ValueError(f"filled quantity exceeds request for leg {leg['leg_id']}")
            if quantity + _EPSILON < (leg.get("filled_qty_base") or 0):
                raise ValueError(f"cumulative fills regressed for leg {leg['leg_id']}")
            known_fills = await self.store.get_arbitrage_fills(cycle.cycle_id, leg["leg_id"])
            if sum(fill["quantity"] for fill in known_fills) > quantity + _EPSILON:
                raise ValueError(f"order snapshot contradicts saved fills for leg {leg['leg_id']}")
            updates = {
                "status": status.upper(),
                "venue_order_id": snapshot.order_id or leg.get("venue_order_id"),
                "filled_qty_base": quantity,
                "avg_price": snapshot.avg_price,
                "error_msg": None,
            }
            if snapshot.fee_usd is not None:
                updates["fee_usd"] = snapshot.fee_usd
            await self.store.update_arbitrage_cycle_leg(leg["leg_id"], **updates)
            quantities[role] += -quantity if reducing else quantity
            (closed if reducing else opened)[role] += quantity
            roles_seen.add(role)
            pending = pending or status in _PENDING
        if failures:
            return RecoveryResult(cycle.cycle_id, "MANUAL_REVIEW", "; ".join(failures))
        cycle.filled_qty_a = quantities["a"]
        cycle.filled_qty_b = quantities["b"]
        if any(quantity < -_EPSILON for quantity in quantities.values()):
            raise ValueError("confirmed closes exceed saved opening fills")
        delta = cycle_net_delta_base(cycle)
        reason = None
        if pending:
            status, reason = "RECOVERY", "orders are still active; no automatic action was taken"
        elif abs(delta) > _EPSILON or roles_seen != {"a", "b"}:
            status, reason = "RECOVERY", "confirmed unmatched exposure; explicit action is required"
        elif max(quantities.values()) <= _EPSILON:
            status = "CLOSED"
        else:
            status = "OPEN"
        await self.store.update_arbitrage_cycle(
            cycle.cycle_id,
            opened_qty_base=min(opened.values()),
            closed_qty_base=min(closed.values()),
        )
        cycle.status = status.lower()
        return RecoveryResult(cycle.cycle_id, status, reason, cycle, delta)

    async def _persist_fills(self, cycle_id: str, leg: dict, fills: list[dict]) -> None:
        for fill in fills:
            trade_id = fill.get("tradeId", fill.get("id"))
            if trade_id is None:
                continue
            quantity, price = float(fill["amount"]), float(fill["price"])
            if not isfinite(quantity) or quantity <= 0 or not isfinite(price) or price <= 0:
                raise ValueError("invalid actual fill in recovered order")
            fill_id = hashlib.sha256(f"{leg['venue']}:{trade_id}".encode()).hexdigest()
            await self.store.insert_arbitrage_fill(
                fill_id=fill_id,
                cycle_id=cycle_id,
                leg_id=leg["leg_id"],
                venue=leg["venue"],
                trade_id=str(trade_id),
                quantity=quantity,
                price=price,
                exchange_timestamp=str(fill["timestamp"]) if fill.get("timestamp") is not None else None,
            )


__all__ = ["ArbitrageRecovery", "RecoveryResult"]
