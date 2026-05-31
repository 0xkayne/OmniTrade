from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.persistence.store import PersistenceStore

    from .instrument import Instrument

logger = logging.getLogger(__name__)


class InstrumentRegistry:
    """A list of all known instruments loaded from every venue's markets API.

    When a PersistenceStore is available, instruments are cached in SQLite
    and reused across restarts (TTL-controlled). Otherwise falls back to
    in-memory-only behaviour (useful for tests).
    """

    def __init__(self, ttl_hours: int = 24):
        self._ttl_hours = ttl_hours
        self._instruments: dict[tuple, Instrument] = {}
        self._loaded_at: float | None = None
        self._store: PersistenceStore | None = None

    def add(self, instrument: Instrument) -> None:
        """Add a single instrument directly (convenience for testing)."""
        self._instruments[instrument.instrument_key] = instrument

    async def load_all(self, exchanges: dict, store: PersistenceStore | None = None) -> None:
        """
        Load instruments from cache (if available and fresh), otherwise
        fetch from every exchange concurrently and persist to cache.
        Individual venue fetch failures are logged but don't fail the load.
        """
        self._store = store

        # Try cache first
        if store is not None:
            cached_age = await store.instrument_cache_age()
            if cached_age is not None:
                try:
                    age_dt = datetime.fromisoformat(cached_age)
                    age_seconds = (datetime.now(timezone.utc) - age_dt).total_seconds()
                    if age_seconds <= self._ttl_hours * 3600:
                        cached = await store.load_instruments()
                        if cached:
                            for inst in cached:
                                self._instruments[inst.instrument_key] = inst
                            self._loaded_at = time.time()
                            logger.info(
                                "Loaded %d instruments from cache (age: %.1f hours)",
                                len(cached),
                                age_seconds / 3600,
                            )
                            return
                except Exception:
                    logger.exception("Failed to load instruments from cache, will re-fetch")

        # Fetch from exchanges
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

        # Persist to cache
        if store is not None and self._instruments:
            try:
                count = await store.save_instruments(list(self._instruments.values()))
                logger.info("Saved %d instruments to cache", count)
            except Exception:
                logger.exception("Failed to save instruments to cache")

    async def refresh(self, exchanges: dict) -> None:
        """Force re-fetch all instruments from exchanges and overwrite cache."""
        if self._store is not None:
            await self._store.clear_instruments()
        self._instruments.clear()
        await self.load_all(exchanges, store=self._store)

    async def reload(self, venue: str, exchanges: dict) -> None:
        """Reload a single venue's instruments. Remove old entries, load new ones, merge back."""
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

            # Update cache for this venue
            if self._store is not None:
                await self._store.clear_instruments(venue=venue)
                await self._store.save_instruments(markets)
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
        """Return True if more than self._ttl_hours have passed since last cache write."""
        if self._loaded_at is None:
            return True
        elapsed_seconds = time.time() - self._loaded_at
        return elapsed_seconds > (self._ttl_hours * 3600)

    async def check_stale(self) -> bool:
        """Async variant that checks the actual cache timestamp when a store is available."""
        if self._store is not None:
            cached_age = await self._store.instrument_cache_age()
            if cached_age is not None:
                age_dt = datetime.fromisoformat(cached_age)
                age_seconds = (datetime.now(timezone.utc) - age_dt).total_seconds()
                return age_seconds > self._ttl_hours * 3600
        return self.is_stale()

    @property
    def venue_count(self) -> int:
        return len({i.venue for i in self._instruments.values()})

    @property
    def instrument_count(self) -> int:
        return len(self._instruments)
