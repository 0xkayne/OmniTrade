"""Lifecycle and accounting helpers for cross-venue arbitrage cycles.

The lifecycle functions are deliberately side-effect free.  An execution
coordinator can persist the returned record, while the input record remains a
snapshot suitable for audit logs and retry decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .models import ArbCycle, ArbFill, ArbStatus


class CycleTransitionError(ValueError):
    """Raised when a cycle state transition is not allowed."""


# Keep this mapping explicit so state changes are reviewable and deterministic.
ALLOWED_TRANSITIONS: Mapping[ArbStatus, frozenset[ArbStatus]] = {
    "detected": frozenset({"prechecked", "rejected", "manual_review"}),
    "prechecked": frozenset({"opening", "rejected", "manual_review"}),
    "opening": frozenset({"partial_open", "hedging", "unhedged", "recovery", "manual_review"}),
    "partial_open": frozenset({"hedging", "unhedged", "recovery", "manual_review"}),
    "hedging": frozenset({"open", "unhedged", "recovery", "manual_review"}),
    "open": frozenset({"closing", "unhedged", "recovery", "manual_review"}),
    "closing": frozenset({"closed", "unhedged", "recovery", "manual_review"}),
    "unhedged": frozenset({"recovery", "manual_review"}),
    "recovery": frozenset({"prechecked", "opening", "manual_review"}),
    "closed": frozenset(),
    "rejected": frozenset(),
    "manual_review": frozenset(),
}


def is_valid_cycle_transition(from_status: str, to_status: str) -> bool:
    """Return whether the arbitrage state machine permits a transition."""

    try:
        return to_status in ALLOWED_TRANSITIONS[from_status]  # type: ignore[index]
    except (KeyError, TypeError):
        return False


def transition_cycle(cycle: ArbCycle, to_status: ArbStatus) -> ArbCycle:
    """Return a new cycle with ``to_status`` after validating the transition.

    ``ArbCycle`` remains mutable for persistence integration, but this helper
    never mutates the supplied instance.  A fresh fills list avoids accidental
    aliasing when a coordinator appends a new fill to the returned snapshot.
    """

    if not is_valid_cycle_transition(cycle.status, to_status):
        raise CycleTransitionError(f"invalid arbitrage transition: {cycle.status} -> {to_status}")
    return replace(cycle, status=to_status, fills=list(cycle.fills))


def leg_delta_base(cycle: ArbCycle, leg: str) -> float:
    """Return signed base-asset delta for leg ``a`` or ``b``.

    Filled quantities are converted from venue contract units using each
    pair's contract size.  Positive values are long delta and negative values
    are short delta.
    """

    if leg not in {"a", "b"}:
        raise ValueError("leg must be 'a' or 'b'")
    if leg == "a":
        quantity = cycle.filled_qty_a * cycle.pair.contract_size_a
        positive = cycle.direction == "buy_a_sell_b"
    else:
        quantity = cycle.filled_qty_b * cycle.pair.contract_size_b * cycle.pair.hedge_ratio
        positive = cycle.direction == "buy_b_sell_a"
    return quantity if positive else -quantity


def cycle_net_delta_base(cycle: ArbCycle) -> float:
    """Return the signed unhedged base-asset delta for a cycle."""

    return leg_delta_base(cycle, "a") + leg_delta_base(cycle, "b")


def cycle_unhedged_qty_base(cycle: ArbCycle) -> float:
    """Return the absolute unhedged base-asset quantity."""

    return abs(cycle_net_delta_base(cycle))


@dataclass(frozen=True)
class PnlBreakdown:
    """Gross and cost components for an arbitrage cycle."""

    gross_pnl_usd: float
    fee_usd: float
    funding_usd: float
    slippage_usd: float
    net_pnl_usd: float


def aggregate_cycle_pnl(cycle: ArbCycle, fills: list[ArbFill] | None = None) -> PnlBreakdown:
    """Aggregate cash flows and costs from normalized fills.

    ``ArbFill.quantity`` is expressed in base units.  A sell contributes
    positive cash flow and a buy contributes negative cash flow.  When no
    fills are supplied, the realized fields on ``cycle`` are returned, which
    supports loading already-aggregated historical records.
    """

    source_fills = cycle.fills if fills is None else fills
    if source_fills:
        gross = sum(fill.quantity * fill.price * (1.0 if fill.side == "sell" else -1.0) for fill in source_fills)
        fees = sum(fill.fee for fill in source_fills)
        funding = cycle.realized_funding_usd
        slippage = cycle.realized_slippage_usd
        return PnlBreakdown(gross, fees, funding, slippage, gross - fees - funding - slippage)
    return PnlBreakdown(
        cycle.realized_gross_pnl_usd,
        cycle.realized_fee_usd,
        cycle.realized_funding_usd,
        cycle.realized_slippage_usd,
        cycle.realized_net_pnl_usd,
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "CycleTransitionError",
    "PnlBreakdown",
    "aggregate_cycle_pnl",
    "cycle_net_delta_base",
    "cycle_unhedged_qty_base",
    "is_valid_cycle_transition",
    "leg_delta_base",
    "transition_cycle",
]

# Compatibility spelling used by the public package API.  Keeping this alias
# avoids colliding with the generic coordinator state-machine helper.
is_valid_transition = is_valid_cycle_transition
