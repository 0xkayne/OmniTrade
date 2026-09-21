"""Data contracts used by cross-venue arbitrage calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ArbDirection = Literal["buy_a_sell_b", "buy_b_sell_a"]
ArbStatus = Literal[
    "detected",
    "prechecked",
    "opening",
    "partial_open",
    "hedging",
    "open",
    "closing",
    "closed",
    "rejected",
    "unhedged",
    "recovery",
    "manual_review",
]


@dataclass(frozen=True)
class ArbPair:
    """Two instruments representing the same delta exposure."""

    base: str
    market_type: Literal["spot", "perp"]
    venue_a: str
    symbol_a: str
    venue_b: str
    symbol_b: str
    contract_size_a: float = 1.0
    contract_size_b: float = 1.0
    hedge_ratio: float = 1.0
    instrument_a: Any | None = None
    instrument_b: Any | None = None

    def __post_init__(self) -> None:
        if not self.base:
            raise ValueError("base is required")
        if self.venue_a == self.venue_b:
            raise ValueError("arbitrage pair must use different venues")
        if self.contract_size_a <= 0 or self.contract_size_b <= 0:
            raise ValueError("contract sizes must be positive")
        if self.hedge_ratio <= 0:
            raise ValueError("hedge_ratio must be positive")


@dataclass(frozen=True)
class QuoteSnapshot:
    """Normalized quote metadata used by risk checks and reporting."""

    quote: Any
    fetched_at: float
    exchange_at: float | None
    sequence: str | None
    source: str

    @property
    def age_ms(self) -> float:
        return self.quote.age_ms


@dataclass(frozen=True)
class SpreadOpportunity:
    """A depth-aware, cost-adjusted entry opportunity."""

    pair: ArbPair
    direction: ArbDirection
    target_qty: float
    buy_vwap: float
    sell_vwap: float
    gross_edge_usd: float
    fee_cost_usd: float
    slippage_cost_usd: float
    latency_reserve_usd: float
    funding_cost_usd: float
    net_edge_usd: float
    net_edge_bps: float
    created_at: float
    expires_at: float
    buy_venue: str
    sell_venue: str
    filled_fully: bool = True
    quote_age_a_ms: float = 0.0
    quote_age_b_ms: float = 0.0
    opportunity_id: str = ""

    @property
    def is_profitable(self) -> bool:
        return self.filled_fully and self.net_edge_usd > 0

    @property
    def target_qty_base(self) -> float:
        """Compatibility name used by execution coordinators."""
        return self.target_qty

    @property
    def expected_net_pnl_usd(self) -> float:
        return self.net_edge_usd

    @property
    def buy_instrument(self) -> Any | None:
        return self.pair.instrument_a if self.direction == "buy_a_sell_b" else self.pair.instrument_b

    @property
    def sell_instrument(self) -> Any | None:
        return self.pair.instrument_b if self.direction == "buy_a_sell_b" else self.pair.instrument_a


@dataclass(frozen=True)
class ArbFill:
    """A normalized fill belonging to one arbitrage cycle."""

    cycle_id: str
    venue: str
    symbol: str
    order_id: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    fee: float = 0.0
    fee_currency: str | None = None
    exchange_timestamp: float | None = None
    received_timestamp: float | None = None


@dataclass
class ArbCycle:
    """Mutable lifecycle record maintained by an execution coordinator."""

    cycle_id: str
    pair: ArbPair
    direction: ArbDirection
    target_qty: float
    status: ArbStatus = "detected"
    filled_qty_a: float = 0.0
    filled_qty_b: float = 0.0
    expected_edge_bps: float = 0.0
    realized_gross_pnl_usd: float = 0.0
    realized_fee_usd: float = 0.0
    realized_funding_usd: float = 0.0
    realized_slippage_usd: float = 0.0
    realized_net_pnl_usd: float = 0.0
    residual_exposure_usd: float = 0.0
    max_unhedged_ms: float = 0.0
    fills: list[ArbFill] = field(default_factory=list)


# Long names are kept as public aliases for callers that prefer the domain
# terminology used in the design documentation.
ArbitrageOpportunity = SpreadOpportunity
ArbitrageCycle = ArbCycle
