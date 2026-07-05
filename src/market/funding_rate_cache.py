"""FundingRateCache — in-memory cache of perp funding rates.

Polling-based (REST fallback) design for MVP.  WebSocket-streamed
updates are deferred to a future iteration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.market.instrument import Instrument

logger = logging.getLogger(__name__)


class FundingRateCache:
    """In-memory snapshot of the latest funding rate per (venue, symbol).

    Uses a polling loop that calls ``exchange.fetch_funding_rates(symbols)``
    once per venue.  Entries older than *ttl_seconds* are considered stale.
    """

    def __init__(self, exchanges: dict[str, object], ttl_seconds: float = 60.0):
        self._exchanges = exchanges
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], _Entry] = {}

    # -- public query API -------------------------------------------------

    def get(self, venue: str, symbol: str) -> dict | None:
        """Return the cached funding rate entry, or None if stale / missing."""
        key = (venue, symbol)
        entry = self._entries.get(key)
        if entry is None or time.monotonic() - entry.ts > self._ttl:
            return None
        return {"funding_rate": entry.funding_rate, "next_funding_time": entry.next_funding_time}

    def all_rates(self) -> list[dict]:
        """Return all currently fresh entries as flat dicts with venue + symbol."""
        now = time.monotonic()
        return [
            {"venue": k[0], "symbol": k[1], "funding_rate": e.funding_rate, "next_funding_time": e.next_funding_time}
            for k, e in self._entries.items()
            if now - e.ts <= self._ttl
        ]

    # -- refresh ----------------------------------------------------------

    async def refresh(self, instruments: list[Instrument]) -> None:
        """Fetch latest funding rates for *instruments* grouped by venue.

        Only perp instruments are included.  Spot instruments are silently
        skipped (they have no funding rate).
        """
        perp_by_venue: dict[str, list[str]] = {}
        for inst in instruments:
            if inst.market_type == "perp":
                perp_by_venue.setdefault(inst.venue, []).append(inst.venue_symbol)

        tasks = []
        for venue, symbols in perp_by_venue.items():
            exchange = self._exchanges.get(venue)
            if exchange is None:
                continue
            tasks.append(self._refresh_venue(exchange, venue, symbols))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _refresh_venue(self, exchange, venue: str, symbols: list[str]) -> None:
        now = time.monotonic()
        try:
            fr_data = await exchange.fetch_funding_rates(symbols)
        except Exception:
            logger.warning("Funding rate fetch failed for %s", venue, exc_info=True)
            return

        if isinstance(fr_data, dict):
            for sym in symbols:
                entry = fr_data.get(sym, {}) if isinstance(fr_data, dict) else {}
                fr = entry.get("fundingRate")
                nft = entry.get("nextFundingTimestamp")
                next_ft = None
                if isinstance(nft, (int, float)) and nft > 0:
                    next_ft = float(nft) / 1000.0
                self._entries[(venue, sym)] = _Entry(
                    funding_rate=fr,
                    next_funding_time=next_ft,
                    ts=now,
                )

    async def close(self) -> None:
        self._entries.clear()


class _Entry:
    __slots__ = ("funding_rate", "next_funding_time", "ts")

    def __init__(self, funding_rate: float | None, next_funding_time: float | None, ts: float):
        self.funding_rate = funding_rate
        self.next_funding_time = next_funding_time
        self.ts = ts
