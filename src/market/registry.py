from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .instrument import Instrument

logger = logging.getLogger(__name__)


class InstrumentRegistry:
    """A list of all known instruments loaded from every venue's markets API."""

    def __init__(self, ttl_hours: int = 24):
        self._ttl_hours = ttl_hours
        self._instruments: dict[tuple, Instrument] = {}
        self._loaded_at: float | None = None

    def add(self, instrument: Instrument) -> None:
        """Add a single instrument directly (convenience for testing)."""
        self._instruments[instrument.instrument_key] = instrument

    async def load_all(self, exchanges: dict) -> None:
        """
        Call list_markets() on every exchange concurrently (asyncio.gather).
        Individual venue failures are logged but don't fail the whole load.
        """
        venue_names = list(exchanges.keys())

        async def _load_one(name: str) -> None:
            try:
                exchange = exchanges[name]
                markets = await exchange.list_markets()
                for instrument in markets:
                    self._instruments[instrument.instrument_key] = instrument
                logger.info("Loaded %d instruments from %s", len(markets), name)
            except Exception:
                logger.exception("Failed to load instruments from %s", name)

        tasks = [_load_one(name) for name in venue_names]
        await asyncio.gather(*tasks)
        self._loaded_at = time.time()

    async def reload(self, venue: str, exchanges: dict) -> None:
        """Reload a single venue's instruments. Remove old entries, load new ones, merge back."""
        # Remove old entries for this venue
        keys_to_remove = [k for k in self._instruments if k[0] == venue]
        for k in keys_to_remove:
            del self._instruments[k]

        if venue not in exchanges:
            logger.warning("Cannot reload %s: no exchange adapter found", venue)
            return

        try:
            exchange = exchanges[venue]
            markets = await exchange.list_markets()
            for instrument in markets:
                self._instruments[instrument.instrument_key] = instrument
            logger.info("Reloaded %d instruments from %s", len(markets), venue)
        except Exception:
            logger.exception("Failed to reload instruments from %s", venue)

    def list_instruments(
        self, *, base: str | None = None, market_type: str | None = None, venue: str | None = None
    ) -> list[Instrument]:
        """Filter by any combination of base symbol, market_type, venue. All filters optional."""
        results = []
        for instr in self._instruments.values():
            if base is not None and instr.base.symbol != base:
                continue
            if market_type is not None and instr.market_type != market_type:
                continue
            if venue is not None and instr.venue != venue:
                continue
            results.append(instr)
        return results

    def find_one(
        self, *, base: str, venue: str, market_type: str, quote_preference: list[str],
    ) -> Instrument | None:
        """
        List instruments matching (base, venue, market_type).
        Walk quote_preference in order. Return the first instrument whose
        quote symbol is in the preference list. Return None if none match.
        """
        candidates = self.list_instruments(base=base, venue=venue, market_type=market_type)
        for preferred_quote in quote_preference:
            for instr in candidates:
                if instr.quote.symbol == preferred_quote:
                    return instr
        return None

    def is_stale(self) -> bool:
        """Return True if more than self._ttl_hours have passed since load_all()."""
        if self._loaded_at is None:
            return True
        elapsed_seconds = time.time() - self._loaded_at
        return elapsed_seconds > (self._ttl_hours * 3600)

    @property
    def venue_count(self) -> int:
        return len({i.venue for i in self._instruments.values()})

    @property
    def instrument_count(self) -> int:
        return len(self._instruments)
