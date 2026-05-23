from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .instrument import Instrument


class InstrumentRegistry:
    """A list of all known instruments loaded from every venue's markets API."""

    def __init__(self, ttl_hours: int = 24):
        self._ttl_hours = ttl_hours
        self._instruments: dict[tuple, Instrument] = {}
        self._loaded_at: float | None = None

    async def load_all(self, exchanges: dict) -> None:
        raise NotImplementedError  # Subagent A

    async def reload(self, venue: str, exchanges: dict) -> None:
        raise NotImplementedError  # Subagent A

    def list_instruments(
        self, *, base: str = None, market_type: str = None, venue: str = None
    ) -> list[Instrument]:
        raise NotImplementedError  # Subagent A

    def find_one(
        self, *, base: str, venue: str, market_type: str, quote_preference: list[str],
    ) -> Instrument | None:
        raise NotImplementedError  # Subagent A

    def is_stale(self) -> bool:
        raise NotImplementedError  # Subagent A

    @property
    def venue_count(self) -> int:
        return len({i.venue for i in self._instruments.values()})

    @property
    def instrument_count(self) -> int:
        return len(self._instruments)
