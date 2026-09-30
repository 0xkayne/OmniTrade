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
    side: str = "buy"  # actual side for this leg (may differ from Intent.side)
    leverage: int = 1  # actual leverage for this leg (may differ from Intent.leverage)
    funding_rate: float | None = None
    next_funding_time: float | None = None
    selection_log: list[dict] = field(default_factory=list)
    reference_price: float = 0.0
    quote_fetched_at: float = 0.0
    quote_source: str = ""
    estimated_spread_pct: float = 0.0
    estimated_cost_usd: float = 0.0
    planned_qty_native: float | None = None
    position_before_qty_native: float | None = None
    position_entry_price: float | None = None
    position_effect: str = "open"

    @property
    def native_qty(self) -> float:
        if self.planned_qty_native is not None:
            return self.planned_qty_native
        if self.instrument.is_inverse or self.instrument.contract_size != 1:
            raise ValueError(f"{self.venue}: missing native quantity for contract")
        return self.planned_qty_base


@dataclass
class Plan:
    intent: Intent
    legs: list[PlannedLeg]
    rejected_venues: list[tuple[str, str]]  # (venue_name, rejection_reason)
    aggregate_estimated_avg_price: float
    aggregate_estimated_fee_usd: float
    is_acceptable: bool
    rejection_reasons: list[str]
