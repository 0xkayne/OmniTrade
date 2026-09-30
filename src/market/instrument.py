from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum
from typing import Literal

from .asset import Asset


class NetworkType(Enum):
    """Which network a market belongs to.

    Defined here rather than in ``exchange`` because it is a field of
    ``Instrument``: the exchange layer imports it from ``market``, keeping the
    dependency one-way. See docs/developer-guide/standards/directory-structure.md §3.
    """

    MAINNET = "mainnet"
    TESTNET = "testnet"


@dataclass(frozen=True)
class Instrument:
    venue: str
    network: NetworkType
    market_type: Literal["spot", "perp"]
    base: Asset
    quote: Asset
    venue_symbol: str  # native symbol on this venue, e.g. "BTCUSDT"
    min_qty: float = 0.0
    qty_step: float = 0.0
    price_step: float = 0.0
    min_notional: float = 0.0
    taker_fee_rate: float = 0.0
    maker_fee_rate: float = 0.0
    contract_size: float = 1.0
    is_inverse: bool = False
    listing_status: str = "trading"
    max_leverage: float | None = None  # venue max leverage for this instrument
    settlement_asset: Asset | None = None
    quantity_unit: Literal["base", "contracts"] = "base"

    def __post_init__(self) -> None:
        if self.quantity_unit not in {"base", "contracts"}:
            raise ValueError(f"{self.venue}:{self.venue_symbol}: invalid quantity unit")
        size = Decimal(str(self.contract_size))
        if not size.is_finite() or size <= 0:
            raise ValueError(f"{self.venue}:{self.venue_symbol}: invalid contract size")
        if self.is_inverse and self.quantity_unit != "contracts":
            raise ValueError(f"{self.venue}:{self.venue_symbol}: inverse quantity must use contracts")

    @staticmethod
    def key(venue: str, network: str, market_type: str, venue_symbol: str) -> tuple:
        return (venue, network, market_type, venue_symbol)

    @property
    def instrument_key(self) -> tuple:
        return self.key(self.venue, self.network.value, self.market_type, self.venue_symbol)

    def _quantity_inputs(self, quantity: float, price: float) -> tuple[Decimal, Decimal, Decimal]:
        qty, px, size = Decimal(str(quantity)), Decimal(str(price)), Decimal(str(self.contract_size))
        if not qty.is_finite() or qty < 0 or not px.is_finite() or px <= 0:
            raise ValueError(f"{self.venue}:{self.venue_symbol}: invalid quantity or conversion price")
        return qty, px, size

    def native_qty_from_notional(self, notional: float, price: float) -> float:
        """Convert quote notional to native quantity, rounded down to its step."""
        value, px, size = self._quantity_inputs(notional, price)
        quantity = value / size if self.is_inverse else value / px
        if self.quantity_unit == "contracts" and not self.is_inverse:
            quantity /= size
        if self.qty_step > 0:
            step = Decimal(str(self.qty_step))
            quantity = (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step
        return float(quantity)

    def base_equivalent(self, qty_native: float, price: float) -> float:
        """Return execution base equivalent; inverse positions remain in contracts."""
        qty, px, size = self._quantity_inputs(qty_native, price)
        if self.quantity_unit == "contracts":
            qty *= size
        return float(qty / px if self.is_inverse else qty)

    def quote_notional(self, qty_native: float, price: float) -> float:
        """Value native quantity in the quote asset at the supplied price."""
        qty, px, size = self._quantity_inputs(qty_native, price)
        if self.quantity_unit == "contracts":
            qty *= size
        return float(qty if self.is_inverse else qty * px)

    def round_qty(self, amount: float) -> float:
        if self.qty_step == 0:
            return amount
        steps = round(amount / self.qty_step)
        return max(self.min_qty, steps * self.qty_step)

    def round_price(self, price: float) -> float:
        if self.price_step == 0:
            return price
        return round(price / self.price_step) * self.price_step

    def required_margin(self, notional_usd: float, leverage: int = 1, price: float | None = None) -> float:
        """Margin required for a position of *notional_usd* at *leverage*.

        Spot: full notional (leverage is always 1).
        Perp: notional / leverage.
        """
        if self.market_type == "perp" and leverage > 0:
            if self.is_inverse:
                if price is None:
                    raise ValueError(f"{self.venue}:{self.venue_symbol}: inverse margin requires a price")
                value, px, _ = self._quantity_inputs(notional_usd, price)
                return float(value / px / Decimal(str(leverage)))
            return notional_usd / leverage
        return notional_usd
