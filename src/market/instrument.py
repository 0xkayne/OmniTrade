from dataclasses import dataclass
from typing import Literal

from src.core.base_exchange import NetworkType

from .asset import Asset


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

    @staticmethod
    def key(venue: str, network: str, market_type: str, base_symbol: str, quote_symbol: str) -> tuple:
        return (venue, network, market_type, base_symbol, quote_symbol)

    @property
    def instrument_key(self) -> tuple:
        return self.key(self.venue, self.network.value, self.market_type, self.base.symbol, self.quote.symbol)

    def round_qty(self, amount: float) -> float:
        if self.qty_step == 0:
            return amount
        steps = round(amount / self.qty_step)
        return max(self.min_qty, steps * self.qty_step)

    def round_price(self, price: float) -> float:
        if self.price_step == 0:
            return price
        return round(price / self.price_step) * self.price_step

    def required_margin(self, notional_usd: float, leverage: int = 1) -> float:
        """Margin required for a position of *notional_usd* at *leverage*.

        Spot: full notional (leverage is always 1).
        Perp: notional / leverage.
        """
        if self.market_type == "perp" and leverage > 0:
            return notional_usd / leverage
        return notional_usd
