"""HedgedPositionManager — lifecycle management for delta-neutral funding-rate arb positions.

Each position is a pair of opposing legs (long on one venue, short on another)
that together are delta-neutral and earn the funding rate spread.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from src.market.pair_matcher import CrossVenuePair

Status = Literal["OPEN", "CLOSING", "CLOSED"]


@dataclass
class HedgedPosition:
    """A delta-neutral cross-venue funding rate arbitrage position."""

    position_id: str
    pair: CrossVenuePair
    intent_open: str
    leg_long_id: str
    leg_short_id: str
    notional_per_leg: float
    opened_at: float
    rate_at_open_a: float | None
    rate_at_open_b: float | None
    status: Status = "OPEN"
    intent_close: str | None = None


class HedgedPositionManager:
    """Tracks and manages hedged cross-venue positions.

    Uses the PersistenceStore's ``hedged_positions`` table for state,
    and the Orchestrator for execution (open + close).  In MVP mode
    the manager is advisory — it records positions but the user
    decides when to execute.
    """

    def __init__(self, store):
        self._store = store

    async def record_open(
        self,
        pair: CrossVenuePair,
        notional_per_leg: float,
        intent_id: str,
        leg_long_id: str,
        leg_short_id: str,
        rate_a: float | None = None,
        rate_b: float | None = None,
    ) -> str:
        """Record a newly opened hedged position. Returns position_id."""
        position_id = f"hp-{uuid.uuid4().hex[:12]}"
        await self._store.create_hedged_position(
            position_id=position_id,
            base=pair.base,
            venue_long=pair.venue_a,
            venue_short=pair.venue_b,
            notional_usd=notional_per_leg,
            intent_open=intent_id,
            leg_long_id=leg_long_id,
            leg_short_id=leg_short_id,
            rate_a=rate_a,
            rate_b=rate_b,
        )
        return position_id

    async def record_close(self, position_id: str, intent_close_id: str) -> None:
        """Mark a position as closed."""
        await self._store.close_hedged_position(position_id, intent_close_id)

    async def get_open_positions(self) -> list[HedgedPosition]:
        """Return all currently open hedged positions."""
        from src.market.instrument import Instrument

        rows = await self._store.get_open_hedged_positions()
        positions: list[HedgedPosition] = []
        for r in rows:
            # Reconstruct a minimal pair from stored fields
            pair = CrossVenuePair(
                base=r["base"],
                venue_a=r["venue_long"],
                venue_b=r["venue_short"],
                instrument_a=Instrument(
                    venue=r["venue_long"],
                    network="testnet",
                    market_type="perp",
                    base=r["base"],
                    quote=r.get("quote_a", "USD"),
                    venue_symbol=r.get("symbol_a", ""),
                ),
                instrument_b=Instrument(
                    venue=r["venue_short"],
                    network="testnet",
                    market_type="perp",
                    base=r["base"],
                    quote=r.get("quote_b", "USD"),
                    venue_symbol=r.get("symbol_b", ""),
                ),
            )
            positions.append(
                HedgedPosition(
                    position_id=r["position_id"],
                    pair=pair,
                    intent_open=r["intent_open"],
                    leg_long_id=r["leg_long_id"],
                    leg_short_id=r["leg_short_id"],
                    notional_per_leg=r["notional_usd"],
                    opened_at=0,
                    rate_at_open_a=r.get("rate_at_open_a"),
                    rate_at_open_b=r.get("rate_at_open_b"),
                    status=r.get("status", "OPEN"),
                    intent_close=r.get("intent_close"),
                )
            )
        return positions
