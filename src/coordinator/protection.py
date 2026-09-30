"""Pure price and quantity protection shared by execution and compensation."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from src.market.instrument import Instrument
from src.market.quote import Quote

from .intent import Intent
from .plan import PlannedLeg


@dataclass(frozen=True)
class LegProtection:
    reference_price: float
    limit_price: float
    time_in_force: str
    max_quote_age_ms: float
    max_spread_pct: float | None

    def validate_quote(self, quote: Quote, qty_native: float, side: str) -> None:
        quote.validate(self.max_quote_age_ms, self.max_spread_pct)
        fill = quote.estimate_fill(qty_native, side, limit_price=self.limit_price)
        if not fill.filled_fully:
            raise ValueError(
                f"{quote.instrument.venue}: insufficient depth within fixed protection price for {qty_native}"
            )
        if (side == "buy" and fill.avg_price > self.limit_price) or (
            side == "sell" and fill.avg_price < self.limit_price
        ):
            raise ValueError(f"{quote.instrument.venue}: executable price exceeds fixed protection price")


def build_leg_protection(
    leg: PlannedLeg, intent: Intent, *, side: str | None = None, reference_price: float | None = None
) -> LegProtection:
    """Build an inward-rounded limit. Requotes never move the reference price."""
    is_compensation = side is not None
    side = side or leg.side
    reference = reference_price or leg.reference_price or leg.estimated_fill.avg_price
    slippage = intent.compensation_slippage_pct if is_compensation else intent.max_slippage_pct
    slippage = 0.5 if slippage is None else slippage
    price = reference * (1 + slippage / 100 if side == "buy" else 1 - slippage / 100)
    if not is_compensation and intent.limit_price is not None:
        price = min(price, intent.limit_price) if side == "buy" else max(price, intent.limit_price)
    step = leg.instrument.price_step
    if step > 0:
        rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
        price = float(
            (Decimal(str(price)) / Decimal(str(step))).to_integral_value(rounding=rounding) * Decimal(str(step))
        )
    if price <= 0:
        raise ValueError(f"{leg.venue}: invalid protection price")
    return LegProtection(
        reference,
        price,
        "IOC" if is_compensation else intent.time_in_force or "IOC",
        intent.max_quote_age_ms,
        intent.max_spread_pct,
    )


def compute_leg_qty(instrument: Instrument, qty_base: float) -> float:
    """Round down; never increase a user's size to satisfy a venue minimum."""
    if instrument.qty_step > 0:
        step = Decimal(str(instrument.qty_step))
        qty_base = float((Decimal(str(qty_base)) / step).to_integral_value(rounding=ROUND_FLOOR) * step)
    return qty_base
