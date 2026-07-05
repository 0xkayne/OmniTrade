"""FundingRateComparator — worst-case settlement-aware profitability model.

Funding on CEXes is PAID AT DISCRETE SETTLEMENT TIMES (not earned
continuously).  Cross-venue arbitrage with different settlement
intervals (e.g. Binance 8h vs Hyperliquid 1h) has a fundamental
timing risk: after the first venue settles, the spread may disappear.
If you close before the second venue settles, you get ZERO funding
on that leg.

This module uses a WORST-CASE model: only the venue that settles
first is assumed to produce guaranteed revenue.  The other leg is
closed before settlement and yields nothing.  If the guaranteed
revenue exceeds all costs, the trade is profitable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from src.market.pair_matcher import CrossVenuePair

Signal = Literal["open_long_a_short_b", "open_short_a_long_b", "close", "reverse", "none"]


@dataclass
class NetReturn:
    """Worst-case net return: guaranteed revenue from first-settling venue."""

    revenue_pct: float  # guaranteed revenue as % of notional
    fee_cost_pct: float  # (fee_a + fee_b) × 2 × 100  (open + close)
    slippage_cost_pct: float  # estimated slippage from orderbook depth
    total_cost_pct: float  # sum of all costs
    net_per_period_pct: float  # revenue - costs
    avg_funding_hours: float  # min funding interval (= max holding time)
    net_annual_pct: float  # annualised for display
    is_profitable: bool
    venue_first: str  # "a" or "b" — which venue settles first


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
    """Worst-case funding rate arbitrage comparator.

    The model: only the venue that settles FIRST produces guaranteed
    revenue.  After that venue settles, the spread may disappear — we
    assume the worst and close both legs.  The second venue's funding
    is never collected.

    Profitable when::

        guaranteed_revenue_pct > (fee_a + fee_b) × 2 × 100 + slippage

    This is conservative but honest.  No ad-hoc safety margins,
    volatility penalties, or annualisation tricks are needed.
    """

    def __init__(self, min_spread_pct: float = 0.0):
        self._min_spread = min_spread_pct

    def compute_net_return(
        self,
        *,
        rate_a: float,
        rate_b: float,
        taker_fee_a: float,
        taker_fee_b: float,
        slippage_pct_a: float = 0.0,
        slippage_pct_b: float = 0.0,
        next_funding_a: float | None = None,
        next_funding_b: float | None = None,
        history_a: list[dict] | None = None,
        history_b: list[dict] | None = None,
    ) -> NetReturn:
        """Compute guaranteed worst-case net return.

        - Determines which venue settles first.
        - Assumes only the favourable leg on the first-settling venue
          produces revenue.
        - The other leg is closed before settlement → 0 funding.
        """
        now = time.time()
        hours_a = _funding_hours(next_funding_a, now)
        hours_b = _funding_hours(next_funding_b, now)
        min_hours = min(hours_a, hours_b)
        venue_first = "a" if hours_a <= hours_b else "b"

        # Direction: long the CHEAPER venue (lower rate = receive funding),
        # short the more expensive venue (higher rate = pay funding).
        if rate_a < rate_b:
            # long A (receive |rate_a|), short B (pay |rate_b|)
            if venue_first == "a":
                guaranteed_revenue_pct = abs(rate_a) * 100
            else:
                guaranteed_revenue_pct = 0  # expensive leg settled first → we pay
        else:
            # long B, short A
            if venue_first == "b":
                guaranteed_revenue_pct = abs(rate_b) * 100
            else:
                guaranteed_revenue_pct = 0

        # Costs
        fee_cost_pct = (taker_fee_a + taker_fee_b) * 2 * 100
        slippage_cost_pct = slippage_pct_a + slippage_pct_b
        total_cost_pct = fee_cost_pct + slippage_cost_pct

        net_pct = guaranteed_revenue_pct - total_cost_pct
        is_profitable = net_pct > 0

        # Annualised for display — based on guaranteed revenue only
        net_annual_pct = (net_pct / min_hours * 365 * 24) if min_hours > 0 else 0

        return NetReturn(
            revenue_pct=guaranteed_revenue_pct,
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
        taker_fee_a: float = 0.0005,
        taker_fee_b: float = 0.0005,
        slippage_pct_a: float = 0.0,
        slippage_pct_b: float = 0.0,
        history_a: list[dict] | None = None,
        history_b: list[dict] | None = None,
    ) -> FundingSpread:
        """Compute spread and worst-case profitability for one pair."""
        spread: float | None = None
        spread_annual: float | None = None
        signal: Signal = "none"
        net_return: NetReturn | None = None

        if rate_a is not None and rate_b is not None:
            spread = rate_b - rate_a

            net_return = self.compute_net_return(
                rate_a=rate_a,
                rate_b=rate_b,
                taker_fee_a=taker_fee_a,
                taker_fee_b=taker_fee_b,
                slippage_pct_a=slippage_pct_a,
                slippage_pct_b=slippage_pct_b,
                next_funding_a=next_ft_a,
                next_funding_b=next_ft_b,
                history_a=history_a,
                history_b=history_b,
            )

            if spread > 0:
                long_v, short_v = pair.venue_a, pair.venue_b
            else:
                long_v, short_v = pair.venue_b, pair.venue_a

            intervals_per_year = (365 * 24) / max(net_return.avg_funding_hours, 1.0)
            spread_annual = spread * intervals_per_year * 100

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
                next_ft_a=rates.get((p.venue_a, p.instrument_a.venue_symbol), {}).get("next_funding_time"),
                next_ft_b=rates.get((p.venue_b, p.instrument_b.venue_symbol), {}).get("next_funding_time"),
                taker_fee_a=p.instrument_a.taker_fee_rate,
                taker_fee_b=p.instrument_b.taker_fee_rate,
            )
            for p in pairs
        ]
        results.sort(key=lambda s: (not s.is_profitable, -(s.net_annual_return_pct or -9999)))
        return results


# ── helpers ───────────────────────────────────────────────────


def _funding_hours(next_funding: float | None, now: float) -> float:
    """Estimate hours until next funding settlement."""
    if next_funding is not None and next_funding > now:
        return max((next_funding - now) / 3600, 0.25)
    return 8.0
