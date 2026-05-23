from dataclasses import dataclass, field

from src.market.instrument import Instrument
from src.market.quote import EstimatedFill

from .intent import Intent


@dataclass
class PlannedLeg:
    venue: str
    instrument: Instrument
    quote_matched: str  # which quote preference was selected
    planned_notional_usd: float
    planned_qty_base: float  # notional / mid_price, rounded to qty_step
    estimated_fill: EstimatedFill
    estimated_fee_usd: float
    funding_rate: float | None = None
    next_funding_time: float | None = None
    selection_log: list[dict] = field(default_factory=list)


@dataclass
class Plan:
    intent: Intent
    legs: list[PlannedLeg]
    rejected_venues: list[tuple[str, str]]  # (venue_name, rejection_reason)
    aggregate_estimated_avg_price: float
    aggregate_estimated_fee_usd: float
    is_acceptable: bool
    rejection_reasons: list[str]
