"""
Planner: selects Instruments, fetches Quotes, computes estimated fill/slippage/fee,
and compares against user-supplied thresholds. NO side effects (pure reads).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.market.quote import EstimatedFill

from .plan import Plan, PlannedLeg

if TYPE_CHECKING:
    from src.market.quote_fetcher import QuoteFetcher
    from src.market.registry import InstrumentRegistry

    from .intent import Intent


class Planner:
    """Given an Intent, produce a Plan by:
    1. For each venue in intent.split:
       a. Find one instrument via registry.find_one()
       b. Fetch a Quote
       c. Compute notional, qty, estimated fill, fee, and thresholds
    2. Build Plan with legs, rejected venues, aggregate stats, is_acceptable flag.

    This class has NO side effects — it only reads from the registry and fetcher.
    """

    def __init__(self, registry: InstrumentRegistry, quote_fetcher: QuoteFetcher):
        self._registry = registry
        self._quote_fetcher = quote_fetcher

    async def plan(self, intent: Intent) -> Plan:
        legs: list[PlannedLeg] = []
        rejected_venues: list[tuple[str, str]] = []
        rejection_reasons: list[str] = []

        for venue, split_ratio in intent.split.items():
            instrument = self._registry.find_one(
                base=intent.base,
                venue=venue,
                market_type=intent.product,
                quote_preference=intent.quote_preference,
            )
            if instrument is None:
                rejected_venues.append((venue, f"no instrument for base={intent.base} market={intent.product}"))
                continue

            quote = await self._quote_fetcher.fetch(instrument)

            if quote.mid_price <= 0:
                rejected_venues.append(
                    (venue, f"empty orderbook for {instrument.venue_symbol} on {venue}")
                )
                continue

            notional = self._compute_notional(intent.total_notional_usd, split_ratio)

            if instrument.min_notional_usd > 0 and notional < instrument.min_notional_usd:
                rejected_venues.append(
                    (
                        venue,
                        f"notional ${notional:.2f} below {venue} minimum "
                        f"${instrument.min_notional_usd:.2f} for {instrument.venue_symbol}",
                    )
                )
                continue

            qty_base = instrument.round_qty(notional / quote.mid_price)

            if qty_base <= 0:
                rejected_venues.append(
                    (
                        venue,
                        f"notional ${notional:.2f} too small for {instrument.venue_symbol} "
                        f"(qty_step={instrument.qty_step}, min_qty={instrument.min_qty}, "
                        f"mid_price=${quote.mid_price:.2f})",
                    )
                )
                continue

            estimated_fill = quote.estimate_fill(qty_base, intent.side)
            if not estimated_fill.filled_fully:
                rejected_venues.append(
                    (venue, f"insufficient depth: only {estimated_fill.avg_price * qty_base:.2f} filled")
                )
                continue

            estimated_fee_usd = notional * (instrument.taker_fee_rate + instrument.maker_fee_rate) / 2

            threshold_violations = self._check_thresholds(
                venue=venue,
                estimated_fill=estimated_fill,
                estimated_fee_usd=estimated_fee_usd,
                funding_rate=quote.funding_rate,
                intent=intent,
            )
            if threshold_violations:
                rejected_venues.append((venue, "; ".join(threshold_violations)))
                continue

            leg = PlannedLeg(
                venue=venue,
                instrument=instrument,
                quote_matched=instrument.quote.symbol,
                planned_notional_usd=notional,
                planned_qty_base=qty_base,
                estimated_fill=estimated_fill,
                estimated_fee_usd=estimated_fee_usd,
                funding_rate=quote.funding_rate,
                next_funding_time=quote.next_funding_time,
                selection_log=[
                    {
                        "quote_preference": intent.quote_preference,
                        "selected": instrument.quote.symbol,
                        "mid_price": quote.mid_price,
                    }
                ],
            )
            legs.append(leg)

        if not legs:
            rejection_reasons.append("no legs could be planned")

        # Aggregate stats
        if legs:
            total_notional = sum(leg.planned_notional_usd for leg in legs)
            weighted_price_sum = sum(
                leg.estimated_fill.avg_price * leg.planned_notional_usd for leg in legs
            )
            aggregate_avg_price = weighted_price_sum / total_notional if total_notional > 0 else 0.0
            aggregate_fee = sum(leg.estimated_fee_usd for leg in legs)
        else:
            aggregate_avg_price = 0.0
            aggregate_fee = 0.0

        # Collect top-level rejection reasons from rejected venues
        for venue, reason in rejected_venues:
            rejection_reasons.append(f"{venue}: {reason}")

        is_acceptable = len(legs) > 0 and len(rejected_venues) == 0

        return Plan(
            intent=intent,
            legs=legs,
            rejected_venues=rejected_venues,
            aggregate_estimated_avg_price=aggregate_avg_price,
            aggregate_estimated_fee_usd=aggregate_fee,
            is_acceptable=is_acceptable,
            rejection_reasons=rejection_reasons,
        )

    @staticmethod
    def _compute_notional(total: float, split_ratio: float) -> float:
        return total * split_ratio

    @staticmethod
    def _check_thresholds(
        venue: str,
        estimated_fill: EstimatedFill,
        estimated_fee_usd: float,
        funding_rate: float | None,
        intent: Intent,
    ) -> list[str]:
        violations: list[str] = []

        if intent.max_slippage_pct is not None and estimated_fill.slippage_pct > intent.max_slippage_pct:
            violations.append(
                f"slippage {estimated_fill.slippage_pct:.4f}% exceeds max {intent.max_slippage_pct}%"
            )

        if intent.max_fee_usd is not None and estimated_fee_usd > intent.max_fee_usd:
            violations.append(
                f"fee ${estimated_fee_usd:.2f} exceeds max ${intent.max_fee_usd:.2f}"
            )

        if intent.max_funding_rate_pct is not None and funding_rate is not None:
            funding_pct = funding_rate * 100
            if funding_pct > intent.max_funding_rate_pct:
                violations.append(
                    f"funding rate {funding_pct:.4f}% exceeds max {intent.max_funding_rate_pct}%"
                )

        return violations
