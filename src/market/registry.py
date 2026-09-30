from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.persistence.store import InstrumentRow

from .asset import Asset
from .instrument import Instrument, NetworkType

if TYPE_CHECKING:
    from src.persistence.store import PersistenceStore

logger = logging.getLogger(__name__)


def instrument_from_row(row: InstrumentRow) -> Instrument:
    """Rebuild a domain ``Instrument`` from its persisted row.

    Lives here, not in ``persistence``: the store reads and writes columns and
    knows nothing about domain objects. See
    docs/developer-guide/standards/directory-structure.md §5.3.

    """
    return Instrument(
        venue=row.venue,
        network=NetworkType(row.network),
        market_type=row.market_type,
        base=Asset(row.base),
        quote=Asset(row.quote),
        venue_symbol=row.venue_symbol,
        min_qty=row.min_qty,
        qty_step=row.qty_step,
        price_step=row.price_step,
        min_notional=row.min_notional,
        taker_fee_rate=row.taker_fee_rate,
        maker_fee_rate=row.maker_fee_rate,
        contract_size=row.contract_size,
        is_inverse=row.is_inverse,
        listing_status=row.listing_status,
        settlement_asset=Asset(row.settlement_asset) if row.settlement_asset else None,
        quantity_unit=row.quantity_unit,
        max_leverage=row.max_leverage,
    )


def instrument_to_row(inst: Instrument) -> InstrumentRow:
    """Flatten a domain ``Instrument`` into the row the store persists."""
    return InstrumentRow(
        venue=inst.venue,
        network=inst.network.value,
        market_type=inst.market_type,
        base=inst.base.symbol,
        quote=inst.quote.symbol,
        venue_symbol=inst.venue_symbol,
        min_qty=inst.min_qty,
        qty_step=inst.qty_step,
        price_step=inst.price_step,
        min_notional=inst.min_notional,
        taker_fee_rate=inst.taker_fee_rate,
        maker_fee_rate=inst.maker_fee_rate,
        contract_size=inst.contract_size,
        is_inverse=inst.is_inverse,
        listing_status=inst.listing_status,
        settlement_asset=inst.settlement_asset.symbol if inst.settlement_asset else None,
        quantity_unit=inst.quantity_unit,
        max_leverage=inst.max_leverage,
    )


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
        self._instruments.clear()

        async def _load_one(name: str) -> None:
            exchange = exchanges[name]
            network = getattr(exchange, "network_type", None)
            network_name = network.value if isinstance(network, NetworkType) else None
            if store is not None:
                try:
                    rows = await store.load_instruments_by_query(venue=name, network=network_name)
                    current = datetime.now(timezone.utc)
                    if rows and all(
                        (current - datetime.fromisoformat(row.cached_at)).total_seconds() <= self._ttl_hours * 3600
                        for row in rows
                    ):
                        cached = [instrument_from_row(row) for row in rows]
                        cached = [inst for inst in cached if self._belongs_to_exchange(inst, name, exchange)]
                        families = self._enabled_families(exchange)
                        if cached and (families is None or families <= {self._family(inst) for inst in cached}):
                            for inst in cached:
                                self.add(inst)
                            return
                except Exception:
                    logger.exception("Failed to load %s instruments from cache, will re-fetch", name)
            try:
                markets = await exchange.list_markets()
                markets = [inst for inst in markets if self._belongs_to_exchange(inst, name, exchange)]
                for inst in markets:
                    self.add(inst)
                logger.info("Loaded %d instruments from %s", len(markets), name)
                if store is not None:
                    await store.clear_instruments(venue=name, network=network_name)
                    await store.save_instrument_rows([instrument_to_row(inst) for inst in markets])
            except Exception:
                logger.exception("Failed to load instruments from %s", name)

        await asyncio.gather(*(_load_one(name) for name in exchanges))
        self._loaded_at = time.time()

    @staticmethod
    def _family(instrument: Instrument) -> str:
        return "spot" if instrument.market_type == "spot" else "coinm" if instrument.is_inverse else "usdm"

    @staticmethod
    def _enabled_families(exchange) -> set[str] | None:
        clients = getattr(exchange, "clients", None)
        if isinstance(clients, dict):
            return set(clients)
        families = getattr(exchange, "market_families", None)
        return set(families) if isinstance(families, (list, tuple, set)) else None

    @classmethod
    def _belongs_to_exchange(cls, instrument: Instrument, name: str, exchange) -> bool:
        network = getattr(exchange, "network_type", None)
        families = cls._enabled_families(exchange)
        return (
            instrument.venue == name
            and (not isinstance(network, NetworkType) or instrument.network == network)
            and (families is None or cls._family(instrument) in families)
        )

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
            markets = [inst for inst in markets if self._belongs_to_exchange(inst, venue, exchange)]
            for instrument in markets:
                self._instruments[instrument.instrument_key] = instrument
            logger.info("Reloaded %d instruments from %s", len(markets), venue)

            # Update cache for this venue
            if self._store is not None:
                network = getattr(exchange, "network_type", None)
                await self._store.clear_instruments(
                    venue=venue,
                    network=network.value if isinstance(network, NetworkType) else None,
                )
                await self._store.save_instrument_rows([instrument_to_row(i) for i in markets])
        except Exception:
            logger.exception("Failed to reload instruments from %s", venue)

    def list_instruments(
        self,
        *,
        base: str | None = None,
        market_type: str | None = None,
        venue: str | None = None,
        contract_type: str | None = None,
        settlement_asset: str | None = None,
        network: NetworkType | None = None,
    ) -> list[Instrument]:
        """Filter by any combination of base symbol, market_type, venue. All filters optional."""
        if contract_type not in {None, "linear", "inverse"}:
            raise ValueError("contract_type must be linear or inverse")
        results = []
        for instr in self._instruments.values():
            if base is not None and instr.base.symbol != base:
                continue
            if market_type is not None and instr.market_type != market_type:
                continue
            if venue is not None and instr.venue != venue:
                continue
            if network is not None and instr.network != network:
                continue
            if contract_type is not None and (
                instr.market_type != "perp" or instr.is_inverse != (contract_type == "inverse")
            ):
                continue
            if settlement_asset is not None and (
                instr.settlement_asset is None or instr.settlement_asset.symbol != settlement_asset
            ):
                continue
            results.append(instr)
        return results

    def find_one(
        self,
        *,
        base: str,
        venue: str,
        market_type: str,
        quote_preference: list[str],
        contract_type: str = "linear",
        settlement_asset: str | None = None,
    ) -> Instrument | None:
        """
        List instruments matching (base, venue, market_type).
        Walk quote_preference in order. Return the first instrument whose
        quote symbol is in the preference list. Return None if none match.
        """
        candidates = self.list_instruments(
            base=base,
            venue=venue,
            market_type=market_type,
            contract_type=contract_type if market_type == "perp" else None,
            settlement_asset=settlement_asset,
        )
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
