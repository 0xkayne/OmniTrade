"""FundingRateComparator — compute spreads with cost-aware profitability."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Literal

from src.market.pair_matcher import CrossVenuePair

Signal = Literal["open_long_a_short_b", "open_short_a_long_b", "close", "reverse", "none"]


@dataclass
class NetReturn:
    """Expected net return after fees, slippage, and safety margin."""

    revenue_pct: float  # gross spread as percentage per period
    fee_cost_pct: float  # (fee_a + fee_b) × 2 × 100  (open + close)
    slippage_cost_pct: float  # estimated slippage from orderbook depth
    safety_margin_pct: float  # extra margin for execution risk
    volatility_penalty_pct: float  # penalty for rate prediction uncertainty
    total_cost_pct: float  # sum of all costs
    net_per_period_pct: float  # revenue - costs for one funding period
    avg_funding_hours: float  # weighted average hours per funding interval
    net_annual_pct: float  # annualised net return after all costs
    is_profitable: bool


@dataclass
class FundingSpread:
    """A funding rate differential between two venues for the same base.

    Includes full cost breakdown: fees, slippage, safety margin,
    and venue-specific funding intervals for accurate annualisation.
    """

    pair: CrossVenuePair
    rate_a: float | None
    rate_b: float | None
    spread: float | None  # rate_b - rate_a (> 0 means venue A is cheaper to long)
    spread_pct_annual: float | None
    next_funding_a: float | None
    next_funding_b: float | None
    signal: Signal = "none"

    # Cost-aware fields (populated when instrument fee + slippage data is available)
    fee_cost_pct: float = 0.0
    slippage_cost_pct: float = 0.0
    safety_margin_pct: float = 0.0
    net_annual_return_pct: float | None = None  # after ALL costs
    is_profitable: bool = False


class FundingRateComparator:
    """Compare funding rates across venue pairs with cost-aware profitability.

    Net profit formula::

        revenue = abs(rate_a - rate_b) × 100  # per period, as %
        costs   = (fee_a + fee_b) × 2 × 100   # open + close taker fees
                + slippage_a + slippage_b     # orderbook depth estimate
                + safety_margin               # execution risk buffer

        net_per_period = revenue - costs
        annualised = net_per_period × (365 × 24 / avg_funding_hours)

    Funding intervals are venue-specific (e.g. Binance ~8h, Hyperliquid ~1h).
    The annualisation uses the average of both venues' intervals, weighted
    by the time remaining until the next settlement.
    """

    def __init__(
        self,
        min_spread_pct: float = 0.0,
        safety_margin_pct: float = 0.01,
        volatility_multiplier: float = 2.0,  # × stddev to get penalty %
    ):
        self._min_spread = min_spread_pct
        self._safety_margin = safety_margin_pct
        self._volatility_multiplier = volatility_multiplier

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
        """Compute expected net annualised return after all costs."""
        spread = abs(rate_b - rate_a)

        # 1. Revenue per funding period (as percentage)
        revenue_pct = spread * 100

        # 2. Funding interval — use actual timestamps when available
        now = time.time()
        hours_a = _funding_hours(next_funding_a, now)
        hours_b = _funding_hours(next_funding_b, now)
        avg_hours = max((hours_a + hours_b) / 2, 1.0)
        periods_per_year = (365 * 24) / avg_hours

        # 3. Costs (all as percentage of notional)
        fee_cost_pct = (taker_fee_a + taker_fee_b) * 2 * 100  # open + close
        slippage_cost_pct = slippage_pct_a + slippage_pct_b
        safety_cost_pct = self._safety_margin
        volatility_penalty = _rate_volatility_penalty(
            history_a,
            history_b,
            self._volatility_multiplier,
        )
        total_cost_pct = fee_cost_pct + slippage_cost_pct + safety_cost_pct + volatility_penalty

        # 4. Net
        net_per_period_pct = revenue_pct - total_cost_pct
        net_annual_pct = net_per_period_pct * periods_per_year
        is_profitable = net_per_period_pct > 0

        return NetReturn(
            revenue_pct=revenue_pct,
            fee_cost_pct=fee_cost_pct,
            slippage_cost_pct=slippage_cost_pct,
            safety_margin_pct=safety_cost_pct,
            volatility_penalty_pct=volatility_penalty,
            total_cost_pct=total_cost_pct,
            net_per_period_pct=net_per_period_pct,
            avg_funding_hours=avg_hours,
            net_annual_pct=net_annual_pct,
            is_profitable=is_profitable,
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
        """Compute spread and cost-adjusted profitability for one pair."""
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

            # Legacy annualisation for display purposes
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
            safety_margin_pct=net_return.safety_margin_pct if net_return else 0.0,
            net_annual_return_pct=net_return.net_annual_pct if net_return else None,
            is_profitable=net_return.is_profitable if net_return else False,
        )

    def compare_all(
        self,
        pairs: list[CrossVenuePair],
        rates: dict[tuple[str, str], dict],
    ) -> list[FundingSpread]:
        """Compare all pairs and return spreads sorted by net annualised return descending."""
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
                    taker_fee_a=pair.instrument_a.taker_fee_rate,
                    taker_fee_b=pair.instrument_b.taker_fee_rate,
                    history_a=ra.get("history") if ra else None,
                    history_b=rb.get("history") if rb else None,
                )
            )
        # Sort by profitability: profitable first, then by net annual return
        results.sort(
            key=lambda s: (
                not s.is_profitable,
                -(s.net_annual_return_pct or -9999),
            ),
        )
        return results


# ── helpers ───────────────────────────────────────────────────


def _funding_hours(next_funding: float | None, now: float) -> float:
    """Estimate hours until next funding settlement.

    Uses the venue-reported next_funding_time when available.
    Falls back to 8 h (the most common interval across venues).
    """
    if next_funding is not None and next_funding > now:
        return max((next_funding - now) / 3600, 0.25)
    return 8.0


def _rate_volatility_penalty(
    history_a: list[dict] | None,
    history_b: list[dict] | None,
    multiplier: float = 2.0,
) -> float:
    """Penalty for funding rate prediction uncertainty.

    Computes stddev of recent funding rates from historical snapshots.
    Higher volatility → larger penalty → harder to be 'profitable'.
    Returns 0.0 if insufficient history (fewer than 3 data points).
    """
    penalty_a = _stdev_pct(history_a) if history_a else 0.0
    penalty_b = _stdev_pct(history_b) if history_b else 0.0
    return (penalty_a + penalty_b) * multiplier


def _stdev_pct(history: list[dict]) -> float:
    """Standard deviation of funding rates from a history list, as percentage."""
    rates = [abs(h["funding_rate"]) for h in history if h.get("funding_rate") is not None]
    if len(rates) < 3:
        return 0.0
    return statistics.stdev(rates) * 100
