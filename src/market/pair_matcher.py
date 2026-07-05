"""CrossVenuePairMatcher — find perp instruments with the same base across venues."""

from __future__ import annotations

from dataclasses import dataclass

from src.market.instrument import Instrument


@dataclass
class CrossVenuePair:
    """A matched pair of perp instruments for the same base on different venues."""

    base: str
    venue_a: str
    venue_b: str
    instrument_a: Instrument
    instrument_b: Instrument


class PairMatcher:
    """Matches perp instruments with the same *base* asset across venues.

    Uses an InstrumentRegistry for lookups.  Only instruments with
    ``market_type == "perp"`` and ``listing_status == "trading"`` are
    considered.
    """

    def __init__(self, registry):
        self._registry = registry

    def find_pairs(self, base_filter: list[str] | None = None) -> list[CrossVenuePair]:
        """Return all cross-venue perp pairs for the given bases.

        If *base_filter* is None, all perp bases in the registry are used.
        """
        pairs: list[CrossVenuePair] = []
        if base_filter is None:
            all_perps = self._registry.list_instruments(market_type="perp")
            bases = sorted({inst.base.symbol for inst in all_perps})
        else:
            bases = base_filter

        for base in bases:
            instruments = [
                i
                for i in self._registry.list_instruments(base=base, market_type="perp")
                if i.listing_status == "trading"
            ]
            if len(instruments) < 2:
                continue
            for i in range(len(instruments)):
                for j in range(i + 1, len(instruments)):
                    pairs.append(
                        CrossVenuePair(
                            base=base,
                            venue_a=instruments[i].venue,
                            venue_b=instruments[j].venue,
                            instrument_a=instruments[i],
                            instrument_b=instruments[j],
                        )
                    )
        return pairs
