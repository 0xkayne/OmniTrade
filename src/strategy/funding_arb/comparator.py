"""FundingRateComparator — compute spreads and generate signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.market.pair_matcher import CrossVenuePair

Signal = Literal["open_long_a_short_b", "open_short_a_long_b", "close", "reverse", "none"]


@dataclass
class FundingSpread:
    """A funding rate differential between two venues for the same base."""

    pair: CrossVenuePair
    rate_a: float | None
    rate_b: float | None
    spread: float | None  # rate_b - rate_a (> 0 means venue A is cheaper to long)
    spread_pct_annual: float | None
    next_funding_a: float | None
    next_funding_b: float | None
    signal: Signal = "none"


class FundingRateComparator:
    """Compare funding rates across venue pairs and produce ranked spreads.

    The core formula:  spread = rate_b - rate_a

    When *spread* is positive, venue A has the lower (more favourable
    for longs) rate — going long on A and short on B yields a positive
    carry.  When negative, the opposite direction is profitable.
    """

    def __init__(
        self,
        min_spread_pct: float = 0.0,
        hours_per_funding: float = 8.0,
    ):
        self._min_spread = min_spread_pct
        self._hours_per_funding = hours_per_funding

    def compare(
        self,
        pair: CrossVenuePair,
        rate_a: float | None,
        rate_b: float | None,
        next_ft_a: float | None = None,
        next_ft_b: float | None = None,
    ) -> FundingSpread:
        """Compute the spread for one pair."""
        spread: float | None = None
        spread_annual: float | None = None
        signal: Signal = "none"

        if rate_a is not None and rate_b is not None:
            spread = rate_b - rate_a
            # Annualised: spread × (365 * 24 / hours_per_funding)
            intervals_per_year = (365 * 24) / self._hours_per_funding
            spread_annual = spread * intervals_per_year * 100  # as percentage

            abs_spread = abs(spread)
            if abs_spread > self._min_spread:
                if spread > 0:
                    signal = "open_long_a_short_b"
                else:
                    signal = "open_short_a_long_b"

        return FundingSpread(
            pair=pair,
            rate_a=rate_a,
            rate_b=rate_b,
            spread=spread,
            spread_pct_annual=spread_annual,
            next_funding_a=next_ft_a,
            next_funding_b=next_ft_b,
            signal=signal,
        )

    def compare_all(
        self,
        pairs: list[CrossVenuePair],
        rates: dict[tuple[str, str], dict],
    ) -> list[FundingSpread]:
        """Compare all pairs and return spreads sorted by annualised yield descending."""
        results: list[FundingSpread] = []
        for pair in pairs:
            ra = rates.get((pair.venue_a, pair.instrument_a.venue_symbol))
            rb = rates.get((pair.venue_b, pair.instrument_b.venue_symbol))
            results.append(
                self.compare(
                    pair,
                    rate_a=ra["funding_rate"] if ra else None,
                    rate_b=rb["funding_rate"] if rb else None,
                    next_ft_a=ra["next_funding_time"] if ra else None,
                    next_ft_b=rb["next_funding_time"] if rb else None,
                )
            )
        # Sort: largest absolute spread first
        results.sort(key=lambda s: abs(s.spread or 0.0), reverse=True)
        return results
