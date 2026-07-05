"""FundingRateComparator — premium mean-reversion profitability model.

Profit = convergence_pnl + funding_collected - fees - slippage

Premium (perp mark vs spot index) is the LEADING indicator.  When
premium diverges across venues for the same base, mean-reversion
creates a delta-neutral profit:

  1. Long  the venue where premium is negative (discount) → perp underpriced
  2. Short the venue where premium is positive (premium) → perp overpriced
  3. When premiums converge back to equilibrium, both legs gain
  4. Plus: collect funding on the first-settling venue

Key insight: most profit comes from premium convergence, NOT from
funding rate spread.  See docs/FUNDING_ARB_THEORY.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from src.market.pair_matcher import CrossVenuePair

Signal = Literal["open_long_a_short_b", "open_short_a_long_b", "close", "reverse", "none"]


@dataclass
class NetReturn:
    """Premium mean-reversion net return."""

    convergence_pnl_pct: float  # premium reversion gain (50% assumed)
    funding_pnl_pct: float  # guaranteed funding from first-settling venue
    fee_cost_pct: float  # (fee_a + fee_b) × 2 × 100
    slippage_cost_pct: float  # estimated slippage
    total_cost_pct: float  # sum of all costs
    net_per_period_pct: float  # convergence + funding - costs
    avg_funding_hours: float  # min funding interval
    net_annual_pct: float  # display only
    is_profitable: bool
    venue_first: str  # "a" or "b"


@dataclass
class FundingSpread:
    """A funding rate differential between two venues with profitability."""

    pair: CrossVenuePair
    rate_a: float | None
    rate_b: float | None
    spread: float | None
    spread_pct_annual: float | None
    next_funding_a: float | None
    next_funding_b: float | None
    signal: Signal = "none"
    fee_cost_pct: float = 0.0
    slippage_cost_pct: float = 0.0
    net_annual_return_pct: float | None = None
    is_profitable: bool = False
    venue_first: str = ""


class FundingRateComparator:
    """Premium mean-reversion arbitrage comparator.

    The model:
    1. Check premiums point opposite directions (one +, one -)
    2. convergence_pnl = (|premium_a| + |premium_b|) × 0.5 (conservative)
    3. funding_collected = first-settling venue's funding rate
    4. profitable when convergence + funding > fees + slippage
    """

    def __init__(self, min_spread_pct: float = 0.0):
        self._min_spread = min_spread_pct

    def compute_net_return(
        self,
        *,
        rate_a: float,
        rate_b: float,
        premium_a: float = 0.0,
        premium_b: float = 0.0,
        taker_fee_a: float,
        taker_fee_b: float,
        slippage_pct_a: float = 0.0,
        slippage_pct_b: float = 0.0,
        next_funding_a: float | None = None,
        next_funding_b: float | None = None,
        history_a: list[dict] | None = None,
        history_b: list[dict] | None = None,
    ) -> NetReturn:
        """Compute premium mean-reversion arbitrage return.

        Profit = convergence_pnl + funding_collected - fees - slippage
        """
        # 1. Guard: no arbitrage when premiums point same direction
        if (rate_a > 0 and rate_b > 0) or (rate_a < 0 and rate_b < 0):
            return NetReturn(
                convergence_pnl_pct=0.0,
                funding_pnl_pct=0.0,
                fee_cost_pct=0.0,
                slippage_cost_pct=0.0,
                total_cost_pct=0.0,
                net_per_period_pct=0.0,
                avg_funding_hours=0.0,
                net_annual_pct=0.0,
                is_profitable=False,
                venue_first="",
            )

        # 2. Convergence PnL (conservative: 50% mean-reversion)
        convergence_pnl = (abs(premium_a) + abs(premium_b)) * 0.5

        # 3. Funding collected (only first-settling venue)
        now = time.time()
        hours_a = _funding_hours(next_funding_a, now)
        hours_b = _funding_hours(next_funding_b, now)
        min_hours = min(hours_a, hours_b)
        venue_first = "a" if hours_a <= hours_b else "b"

        if rate_a < rate_b:
            if venue_first == "a":
                funding_collected = abs(rate_a) * 100
            else:
                funding_collected = 0
        else:
            if venue_first == "b":
                funding_collected = abs(rate_b) * 100
            else:
                funding_collected = 0

        # 4. Costs
        fee_cost_pct = (taker_fee_a + taker_fee_b) * 2 * 100
        slippage_cost_pct = slippage_pct_a + slippage_pct_b
        total_cost_pct = fee_cost_pct + slippage_cost_pct

        # 5. Net
        net_pct = convergence_pnl + funding_collected - total_cost_pct
        is_profitable = net_pct > 0
        net_annual_pct = (net_pct / min_hours * 365 * 24) if min_hours > 0 else 0

        return NetReturn(
            convergence_pnl_pct=convergence_pnl,
            funding_pnl_pct=funding_collected,
            fee_cost_pct=fee_cost_pct,
            slippage_cost_pct=slippage_cost_pct,
            total_cost_pct=total_cost_pct,
            net_per_period_pct=net_pct,
            avg_funding_hours=min_hours,
            net_annual_pct=net_annual_pct,
            is_profitable=is_profitable,
            venue_first=venue_first,
        )

    def compare(
        self,
        pair: CrossVenuePair,
        rate_a: float | None,
        rate_b: float | None,
        next_ft_a: float | None = None,
        next_ft_b: float | None = None,
        *,
        premium_a: float = 0.0,
        premium_b: float = 0.0,
        taker_fee_a: float = 0.0005,
        taker_fee_b: float = 0.0005,
        slippage_pct_a: float = 0.0,
        slippage_pct_b: float = 0.0,
        history_a: list[dict] | None = None,
        history_b: list[dict] | None = None,
    ) -> FundingSpread:
        """Compute spread and profitability for one pair."""
        spread: float | None = None
        spread_annual: float | None = None
        signal: Signal = "none"
        net_return: NetReturn | None = None

        if rate_a is not None and rate_b is not None:
            spread = rate_b - rate_a
            net_return = self.compute_net_return(
                rate_a=rate_a,
                rate_b=rate_b,
                premium_a=premium_a,
                premium_b=premium_b,
                taker_fee_a=taker_fee_a,
                taker_fee_b=taker_fee_b,
                slippage_pct_a=slippage_pct_a,
                slippage_pct_b=slippage_pct_b,
                next_funding_a=next_ft_a,
                next_funding_b=next_ft_b,
                history_a=history_a,
                history_b=history_b,
            )
            intervals_per_year = (365 * 24) / max(net_return.avg_funding_hours, 1.0)
            spread_annual = (spread or 0) * intervals_per_year * 100

            if net_return.is_profitable and abs(spread) * 100 > self._min_spread:
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
            fee_cost_pct=net_return.fee_cost_pct if net_return else 0.0,
            slippage_cost_pct=net_return.slippage_cost_pct if net_return else 0.0,
            net_annual_return_pct=net_return.net_annual_pct if net_return else None,
            is_profitable=net_return.is_profitable if net_return else False,
            venue_first=net_return.venue_first if net_return else "",
        )

    def compare_all(
        self,
        pairs: list[CrossVenuePair],
        rates: dict[tuple[str, str], dict],
    ) -> list[FundingSpread]:
        """Compare all pairs, sorted by profitable-first then net return."""
        results: list[FundingSpread] = [
            self.compare(
                p,
                rate_a=rates.get((p.venue_a, p.instrument_a.venue_symbol), {}).get("funding_rate"),
                rate_b=rates.get((p.venue_b, p.instrument_b.venue_symbol), {}).get("funding_rate"),
                premium_a=rates.get((p.venue_a, p.instrument_a.venue_symbol), {}).get("premium_pct", 0),
                premium_b=rates.get((p.venue_b, p.instrument_b.venue_symbol), {}).get("premium_pct", 0),
                next_ft_a=rates.get((p.venue_a, p.instrument_a.venue_symbol), {}).get("next_funding_time"),
                next_ft_b=rates.get((p.venue_b, p.instrument_b.venue_symbol), {}).get("next_funding_time"),
                taker_fee_a=p.instrument_a.taker_fee_rate,
                taker_fee_b=p.instrument_b.taker_fee_rate,
            )
            for p in pairs
        ]
        results.sort(key=lambda s: (not s.is_profitable, -(s.net_annual_return_pct or -9999)))
        return results


def _funding_hours(next_funding: float | None, now: float) -> float:
    """Estimate hours until next funding settlement."""
    if next_funding is not None and next_funding > now:
        return max((next_funding - now) / 3600, 0.25)
    return 8.0
