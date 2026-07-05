"""PremiumTracker — perp premium (mark vs spot) monitoring and divergence detection.

Premium is the LEADING indicator of cross-venue arbitrage opportunity.
Funding rate is a lagging indicator (based on historical premium).
When premium diverges across venues for the same base, mean-reversion
creates a delta-neutral profit opportunity.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.market.instrument import Instrument

logger = logging.getLogger(__name__)


@dataclass
class PremiumSnapshot:
    venue: str
    symbol: str
    base: str
    mark_price: float | None
    index_price: float | None  # spot index price
    premium_pct: float | None  # (mark - index) / index × 100
    funding_rate: float | None
    next_funding_time: float | None


@dataclass
class PremiumDivergence:
    base: str
    venue_discount: str  # venue with lowest premium (long here)
    venue_premium: str  # venue with highest premium (short here)
    discount_pct: float  # negative premium on discount venue
    premium_pct: float  # positive premium on premium venue
    spread_pct: float  # abs(discount) + abs(premium)
    snap_discount: PremiumSnapshot
    snap_premium: PremiumSnapshot


class PremiumTracker:
    """Fetch and compare perp premiums across venues."""

    def __init__(self, exchanges: dict[str, object]):
        self._exchanges = exchanges

    async def fetch_snapshots(
        self,
        instruments: list[Instrument],
        funding_rates: dict[str, dict],
    ) -> list[PremiumSnapshot]:
        """Pull mark prices and compute premium % for perp instruments.

        Uses fetch_mark_prices (batch) per venue, then pairs each
        mark price with the corresponding funding rate from the cache.
        """
        # Batch mark prices per venue
        by_venue: dict[str, list[Instrument]] = {}
        for inst in instruments:
            if inst.market_type == "perp":
                by_venue.setdefault(inst.venue, []).append(inst)

        mark_prices: dict[str, dict] = {}
        tasks = []
        for venue, insts in by_venue.items():
            exchange = self._exchanges.get(venue)
            if exchange is None:
                continue
            symbols = [i.venue_symbol for i in insts]
            tasks.append(self._fetch_mark_batch(exchange, venue, symbols, mark_prices))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Build snapshots
        snapshots: list[PremiumSnapshot] = []
        for inst in instruments:
            if inst.market_type != "perp":
                continue
            fr_entry = funding_rates.get(inst.venue_symbol, {})
            mp = mark_prices.get(inst.venue_symbol, {})
            mark = mp.get("mark")
            index = mp.get("index")
            premium = None
            if mark is not None and index is not None and index > 0:
                premium = (mark - index) / index * 100
            snapshots.append(
                PremiumSnapshot(
                    venue=inst.venue,
                    symbol=inst.venue_symbol,
                    base=inst.base.symbol,
                    mark_price=mark,
                    index_price=index,
                    premium_pct=premium,
                    funding_rate=fr_entry.get("funding_rate"),
                    next_funding_time=fr_entry.get("next_funding_time"),
                )
            )
        return snapshots

    def detect_divergence(
        self,
        base: str,
        snapshots: list[PremiumSnapshot],
    ) -> PremiumDivergence | None:
        """Find the min and max premium for a base across venues.

        Returns a PremiumDivergence if there are at least 2 snapshots
        with valid premium data for the same base.
        """
        base_snaps = [s for s in snapshots if s.base == base and s.premium_pct is not None]
        if len(base_snaps) < 2:
            return None

        min_snap = min(base_snaps, key=lambda s: s.premium_pct or 0)
        max_snap = max(base_snaps, key=lambda s: s.premium_pct or 0)
        if min_snap is max_snap:
            return None

        discount = min_snap.premium_pct or 0
        premium_val = max_snap.premium_pct or 0
        return PremiumDivergence(
            base=base,
            venue_discount=min_snap.venue,
            venue_premium=max_snap.venue,
            discount_pct=discount,
            premium_pct=premium_val,
            spread_pct=abs(discount) + abs(premium_val),
            snap_discount=min_snap,
            snap_premium=max_snap,
        )

    async def _fetch_mark_batch(
        self,
        exchange,
        venue: str,
        symbols: list[str],
        result: dict[str, dict],
    ) -> None:
        try:
            data = await exchange.fetch_mark_prices(symbols)
        except Exception:
            logger.warning("fetch_mark_prices failed for %s", venue, exc_info=True)
            return
        if isinstance(data, dict):
            for sym, entry in data.items():
                result[sym] = {
                    "mark": entry.get("markPrice") or entry.get("mark"),
                    "index": entry.get("indexPrice") or entry.get("index"),
                }
