"""QuoteFetcher — fetches real-time Quote snapshots for one or more Instruments."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .instrument import Instrument
    from .orderbook_cache import OrderbookCache
    from .quote import Quote

logger = logging.getLogger(__name__)


class QuoteFetcher:
    """
    Fetches real-time Quote snapshots for one or more Instruments.

    Uses WebSocket-streamed local cache when available; falls back to
    REST exchange.fetch_orderbook() on cache miss or staleness.

    For perp instruments, funding rate / next funding time are enriched
    via a dedicated REST call after the orderbook is obtained.  This
    enrichment is always best-effort — failures never block the Quote.
    """

    def __init__(self, exchanges: dict[str, object], cache: OrderbookCache | None = None):
        self._exchanges = exchanges
        self._cache = cache

    async def fetch(self, instrument: Instrument, depth: int = 20, *, enrich_funding: bool = True) -> Quote:
        """Fetch a Quote for *instrument*.

        Tries the WebSocket cache first; falls back to a REST orderbook
        call.  For perp instruments the funding rate is enriched afterward
        (unless *enrich_funding* is False, which *fetch_many* uses so it
        can batch funding calls by venue).
        """
        exchange = self._exchanges.get(instrument.venue)
        if exchange is None:
            raise ValueError(f"No exchange adapter for venue '{instrument.venue}'")

        # Try WebSocket cache first
        quote: Quote | None = None
        if self._cache is not None:
            quote = self._cache.get_quote(instrument)

        if quote is None:
            # REST fallback — orderbook only (funding enriched below)
            quote = await self._fetch_rest(instrument, exchange, depth)

        # Single, unified funding enrichment for perp instruments.
        # fetch_many sets enrich_funding=False so it can batch calls.
        if enrich_funding and instrument.market_type == "perp":
            await self._enrich_funding(quote, exchange)

        return quote

    async def _fetch_rest(self, instrument: Instrument, exchange, depth: int) -> Quote:
        """Fetch a Quote via REST orderbook call.  Funding data is NOT populated
        here — it is always added by *fetch* / *fetch_many* afterward so that
        there is a single code path for enrichment.
        """
        from .quote import Quote

        ob = await exchange.fetch_orderbook(instrument.venue_symbol, depth)

        bids = _parse_orderbook_side(ob.get("bids", []))
        asks = _parse_orderbook_side(ob.get("asks", []))

        if not bids or not asks:
            logger.warning("Empty orderbook for %s on %s", instrument.venue_symbol, instrument.venue)

        bid_price = bids[0][0] if bids else 0.0
        bid_size = bids[0][1] if bids else 0.0
        ask_price = asks[0][0] if asks else 0.0
        ask_size = asks[0][1] if asks else 0.0

        if bid_price > 0 and ask_price > 0:
            mid_price = (bid_price + ask_price) / 2.0
        elif ask_price > 0:
            mid_price = ask_price
        elif bid_price > 0:
            mid_price = bid_price
        else:
            mid_price = 0.0

        return Quote(
            instrument=instrument,
            fetched_at=time.time(),
            bid_price=bid_price,
            bid_size=bid_size,
            ask_price=ask_price,
            ask_size=ask_size,
            mid_price=mid_price,
            taker_fee_rate=instrument.taker_fee_rate,
            maker_fee_rate=instrument.maker_fee_rate,
            _bids=bids,
            _asks=asks,
        )

    # ------------------------------------------------------------------
    # Funding rate helpers (best-effort — never raise)
    # ------------------------------------------------------------------

    @staticmethod
    async def _fetch_funding(exchange, instrument: Instrument) -> tuple[float | None, float | None]:
        """Fetch funding rate and next funding time for a perp instrument.

        Returns (funding_rate, next_funding_time) — both floats or None.
        Funding rate is a decimal (e.g. 0.0001 = 0.01%).
        Next funding time is a Unix timestamp in seconds.
        Failures are logged and return (None, None).
        """
        try:
            fr_data = await exchange.fetch_funding_rate(instrument.venue_symbol)
            funding_rate = fr_data.get("fundingRate")
            nft = fr_data.get("nextFundingTimestamp")
            next_funding_time: float | None = None
            if isinstance(nft, (int, float)) and nft > 0:
                next_funding_time = nft / 1000.0  # ms → seconds
            return funding_rate, next_funding_time
        except Exception:
            logger.warning(
                "Failed to fetch funding rate for %s on %s",
                instrument.venue_symbol,
                instrument.venue,
                exc_info=True,
            )
            return None, None

    @staticmethod
    def _apply_funding(quote: Quote, funding_rate: float | None, next_funding_time: float | None) -> None:
        """Set funding fields on *quote* in-place."""
        quote.funding_rate = funding_rate
        quote.next_funding_time = next_funding_time

    async def _enrich_funding(self, quote: Quote, exchange) -> None:
        """Enrich a single Quote with funding rate data (single REST call)."""
        funding_rate, next_funding_time = await self._fetch_funding(exchange, quote.instrument)
        self._apply_funding(quote, funding_rate, next_funding_time)

    async def close(self) -> None:
        if self._cache is not None:
            await self._cache.close()

    # ------------------------------------------------------------------
    # Batch fetch
    # ------------------------------------------------------------------

    async def fetch_many(self, instruments: list[Instrument], depth: int = 20) -> list[Quote | None]:
        """
        Fetch Quotes for multiple instruments concurrently.

        A single instrument failure will not fail the whole batch — None is
        returned in that slot.  List length matches input order.

        For perp instruments, funding rates are batch-fetched per venue
        (one *fetch_funding_rates* call per venue) rather than individually,
        reducing N REST calls to 1 per venue.
        """

        # Phase 1: fetch orderbook quotes concurrently (no funding enrichment)
        async def _fetch_one(instr: Instrument) -> Quote | None:
            try:
                return await self.fetch(instr, depth, enrich_funding=False)
            except Exception:
                logger.warning(
                    "Failed to fetch quote for %s on %s",
                    instr.venue_symbol,
                    instr.venue,
                    exc_info=True,
                )
                return None

        results = await asyncio.gather(*[_fetch_one(i) for i in instruments])
        quotes: list[Quote | None] = list(results)

        # Phase 2: batch funding rates by venue for perp instruments
        perp_by_venue: dict[str, list[tuple[int, Quote]]] = {}
        for idx, (instr, q) in enumerate(zip(instruments, quotes, strict=True)):
            if q is not None and instr.market_type == "perp":
                perp_by_venue.setdefault(instr.venue, []).append((idx, q))

        for venue, pairs in perp_by_venue.items():
            exchange = self._exchanges.get(venue)
            if exchange is None:
                continue
            symbols = [instruments[idx].venue_symbol for idx, _q in pairs]
            try:
                fr_data = await exchange.fetch_funding_rates(symbols)
            except Exception:
                logger.warning(
                    "Batch funding rate fetch failed for %s",
                    venue,
                    exc_info=True,
                )
                continue  # Quotes keep None funding — best-effort

            # Distribute results back to each Quote
            for idx, quote in pairs:
                sym = instruments[idx].venue_symbol
                entry = fr_data.get(sym, {}) if isinstance(fr_data, dict) else {}
                nft = entry.get("nextFundingTimestamp")
                next_ft: float | None = None
                if isinstance(nft, (int, float)) and nft > 0:
                    next_ft = nft / 1000.0
                self._apply_funding(quote, entry.get("fundingRate"), next_ft)

        return quotes


def _parse_orderbook_side(raw_side: list) -> list[tuple[float, float]]:
    """
    Normalise orderbook side data into list[(price, qty)].

    Handles both ccxt-style [[price, qty], ...] and list-of-tuples formats.
    """
    result = []
    for entry in raw_side:
        if isinstance(entry, (list, tuple)):
            result.append((float(entry[0]), float(entry[1])))
        else:
            logger.warning("Unexpected orderbook entry format: %r", entry)
    return result
