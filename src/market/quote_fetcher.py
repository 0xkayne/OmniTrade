"""QuoteFetcher — fetches real-time Quote snapshots for one or more Instruments."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .instrument import Instrument
    from .quote import Quote

logger = logging.getLogger(__name__)


class QuoteFetcher:
    """
    Fetches real-time Quote snapshots for one or more Instruments.

    Uses the exchange adapter's fetch_orderbook() + fee data from Instrument.
    """

    def __init__(self, exchanges: dict[str, object]):
        """
        Args:
            exchanges: dict of venue_name -> BaseExchange adapter.
        """
        self._exchanges = exchanges

    async def fetch(self, instrument: Instrument, depth: int = 20) -> Quote:
        """
        Fetch a single Quote for one instrument.

        Args:
            instrument: The Instrument to fetch a quote for.
            depth: Orderbook depth to request.

        Returns:
            A Quote object with top-of-book, full depth, and fee rates.

        Raises:
            ValueError: If no exchange adapter found for the instrument's venue.
        """
        from .quote import Quote

        exchange = self._exchanges.get(instrument.venue)
        if exchange is None:
            raise ValueError(f"No exchange adapter for venue '{instrument.venue}'")

        ob = await exchange.fetch_orderbook(instrument.venue_symbol, depth)

        bids = _parse_orderbook_side(ob.get("bids", []))
        asks = _parse_orderbook_side(ob.get("asks", []))

        if not bids or not asks:
            logger.warning(
                "Empty orderbook for %s on %s", instrument.venue_symbol, instrument.venue
            )

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

    async def fetch_many(
        self, instruments: list[Instrument], depth: int = 20
    ) -> list[Quote | None]:
        """
        Fetch Quotes for multiple instruments concurrently via asyncio.gather.

        A single instrument failure will not fail the whole batch — None is
        returned in that slot. List length matches input order.

        Args:
            instruments: List of Instruments to fetch quotes for.
            depth: Orderbook depth to request.

        Returns:
            List of Quote or None in the same order as input instruments.
        """

        async def _fetch_one(instr: Instrument) -> Quote | None:
            try:
                return await self.fetch(instr, depth)
            except Exception:
                logger.warning(
                    "Failed to fetch quote for %s on %s",
                    instr.venue_symbol,
                    instr.venue,
                    exc_info=True,
                )
                return None

        results = await asyncio.gather(*[_fetch_one(i) for i in instruments])
        return list(results)


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
