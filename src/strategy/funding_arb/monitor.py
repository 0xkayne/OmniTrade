"""FundingRateMonitor — continuous funding rate scanning loop."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.market.funding_rate_cache import FundingRateCache
    from src.market.instrument import Instrument
    from src.market.pair_matcher import PairMatcher
    from src.persistence.store import PersistenceStore
    from src.strategy.funding_arb.comparator import FundingRateComparator, FundingSpread
    from src.strategy.funding_arb.position_manager import HedgedPositionManager

logger = logging.getLogger(__name__)


class FundingRateMonitor:
    """Orchestrates funding rate scanning, comparison, and persistence.

    Usage::

        monitor = FundingRateMonitor(
            registry, cache, pair_matcher, comparator, position_manager, store,
        )
        # One-shot scan
        spreads = await monitor.scan_once()

        # Continuous loop
        await monitor.run_loop(interval_seconds=60)
    """

    def __init__(
        self,
        registry,
        cache: FundingRateCache,
        pair_matcher: PairMatcher,
        comparator: FundingRateComparator,
        position_manager: HedgedPositionManager,
        store: PersistenceStore,
    ):
        self._registry = registry
        self._cache = cache
        self._pair_matcher = pair_matcher
        self._comparator = comparator
        self._position_manager = position_manager
        self._store = store

    async def scan_once(
        self,
        base_filter: list[str] | None = None,
    ) -> list[FundingSpread]:
        """Single scan: fetch rates, compare, persist, return spreads."""
        pairs = self._pair_matcher.find_pairs(base_filter=base_filter)
        if not pairs:
            return []

        # Collect unique instruments
        seen: set[tuple[str, str]] = set()
        all_instruments: list[Instrument] = []
        for p in pairs:
            for inst in (p.instrument_a, p.instrument_b):
                key = (inst.venue, inst.venue_symbol)
                if key not in seen:
                    seen.add(key)
                    all_instruments.append(inst)

        # Refresh cache
        await self._cache.refresh(all_instruments)

        # Build rates lookup
        rates: dict[tuple[str, str], dict] = {}
        for inst in all_instruments:
            entry = self._cache.get(inst.venue, inst.venue_symbol)
            if entry is not None:
                rates[(inst.venue, inst.venue_symbol)] = entry

        # Compare
        spreads = self._comparator.compare_all(pairs, rates)

        # Persist snapshots
        for inst in all_instruments:
            entry = self._cache.get(inst.venue, inst.venue_symbol)
            if entry is not None:
                await self._store.insert_funding_snapshot(
                    venue=inst.venue,
                    symbol=inst.venue_symbol,
                    funding_rate=entry["funding_rate"],
                    next_funding_time=entry["next_funding_time"],
                )

        return spreads

    async def run_loop(
        self,
        interval_seconds: float = 60.0,
        base_filter: list[str] | None = None,
    ) -> None:
        """Continuous monitoring loop.

        Press Ctrl+C to stop gracefully.
        """
        logger.info(
            "Funding rate monitor started (interval=%.0fs, bases=%s)",
            interval_seconds,
            base_filter or "all",
        )
        try:
            while True:
                t0 = time.perf_counter()
                try:
                    spreads = await self.scan_once(base_filter=base_filter)
                    if spreads:
                        best = spreads[0]
                        if best.spread is not None:
                            logger.info(
                                "Top spread: %s %s-vs-%s spread=%.4f%% signal=%s",
                                best.pair.base,
                                best.pair.venue_a,
                                best.pair.venue_b,
                                best.spread * 100,
                                best.signal,
                            )

                    open_positions = await self._position_manager.get_open_positions()
                    if open_positions:
                        logger.info("Open hedged positions: %d", len(open_positions))
                except Exception:
                    logger.warning("Scan failed", exc_info=True)

                elapsed = time.perf_counter() - t0
                sleep_for = max(0.0, interval_seconds - elapsed)
                await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            logger.info("Funding rate monitor stopped.")

    async def close(self) -> None:
        await self._cache.close()
