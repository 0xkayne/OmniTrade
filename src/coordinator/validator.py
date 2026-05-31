"""
Validator: pre-flight checks before any orders are sent. NO side effects (pure reads).

Checks per leg:
- Instrument listing_status is "trading"
- Account balance >= required (notional for spot; notional/leverage for perp)
- Quantity respects min_qty and qty_step
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.base_exchange import BaseExchange

    from .plan import Plan
    from .timing import TimingCollector


@dataclass
class ValidationResult:
    is_valid: bool
    failures: list[tuple[str, str]]  # [(venue, reason), ...]


class Validator:
    """Pre-flight checks executed concurrently across all legs in a Plan.

    Pure read-only — makes no state changes and sends no orders.
    """

    def __init__(self, exchanges: dict[str, BaseExchange]):
        self._exchanges = exchanges

    async def fetch_balances(self, venues: list[str]) -> dict[str, dict | Exception]:
        """Pre-fetch balances for the given venues concurrently."""
        tasks = {}
        for venue in venues:
            exchange = self._exchanges.get(venue)
            if exchange is not None:
                tasks[venue] = exchange.fetch_balance()
        if not tasks:
            return {}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return dict(zip(tasks.keys(), results, strict=True))

    async def validate(self, plan: Plan, timing: TimingCollector | None = None,
                       prefetched_balances: dict[str, dict] | None = None) -> ValidationResult:
        results = await asyncio.gather(
            *(self._validate_leg(leg, leg.leverage, timing=timing, prefetched_balances=prefetched_balances)
              for leg in plan.legs),
            return_exceptions=True,
        )
        failures: list[tuple[str, str]] = []
        for leg, result in zip(plan.legs, results, strict=True):
            if isinstance(result, Exception):
                failures.append((leg.venue, f"validation error: {result}"))
            elif isinstance(result, list):
                failures.extend(result)
        return ValidationResult(is_valid=(len(failures) == 0), failures=failures)

    async def _validate_leg(self, leg: Plan.legs[0], leverage: int = 1,
                             timing: TimingCollector | None = None,
                             prefetched_balances: dict[str, dict] | None = None) -> list[tuple[str, str]]:
        """Returns a list of (venue, reason) failure tuples for this leg."""
        failures: list[tuple[str, str]] = []

        inst = leg.instrument
        venue = leg.venue

        # 1. Listing status (CPU)
        if inst.listing_status != "trading":
            failures.append((venue, f"{inst.venue_symbol} is not trading (status: {inst.listing_status})"))

        # 2. Exchange present
        exchange = self._exchanges.get(venue)
        if exchange is None:
            failures.append((venue, f"no exchange configured for venue {venue}"))
            return failures  # cannot check balance without exchange

        # 3. Quantity rules (CPU)
        if leg.planned_qty_base <= 0:
            failures.append((venue, "planned qty is zero or negative"))
        elif leg.planned_qty_base < inst.min_qty:
            failures.append((venue, f"qty {leg.planned_qty_base} below min_qty {inst.min_qty}"))

        # 4. Balance check — use prefetched balance if available, otherwise fetch.
        if prefetched_balances and venue in prefetched_balances:
            balance_or_exc = prefetched_balances[venue]
            if isinstance(balance_or_exc, BaseException):
                failures.append((venue, f"failed to fetch balance: {balance_or_exc}"))
                return failures
            balance = balance_or_exc
            if timing:
                leg_t = timing.ensure_leg("validate", venue)
                leg_t["balance_fetch_ms"] = 0.0
        else:
            if timing:
                timing.mark(f"validate.{venue}.balance_fetch")
            try:
                balance = await exchange.fetch_balance()
            except Exception as e:
                if timing:
                    timing.pop(f"validate.{venue}.balance_fetch")
                failures.append((venue, f"failed to fetch balance: {e}"))
                return failures
            if timing:
                leg_t = timing.ensure_leg("validate", venue)
                leg_t["balance_fetch_ms"] = timing.pop(f"validate.{venue}.balance_fetch")

        if timing:
            timing.mark(f"validate.{venue}.cpu")

        free = balance.get("free", {})
        quote_asset = inst.quote.symbol
        available = free.get(quote_asset, 0.0)

        # For spot: need the full notional in quote asset
        # For perp: need notional / leverage (margin)
        margin_required = leg.planned_notional_usd
        if inst.market_type == "perp" and leverage > 0:
            margin_required = leg.planned_notional_usd / leverage

        if available < margin_required:
            failures.append((venue, f"insufficient balance: need ${margin_required:.2f}, have ${available:.2f}"))

        if timing:
            leg_t["cpu_ms"] = timing.pop(f"validate.{venue}.cpu")

        return failures
