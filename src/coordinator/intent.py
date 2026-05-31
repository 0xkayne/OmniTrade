from dataclasses import dataclass, field
from typing import Literal

PRODUCTS = ("spot", "perp")
SIDES = ("buy", "sell")


@dataclass
class LegConfig:
    """Per-leg overrides for product, side, and leverage.

    All fields are optional — when None, the Intent-level default is used.
    """

    product: str | None = None
    side: str | None = None
    leverage: int | None = None

    def __post_init__(self):
        if self.product is not None and self.product not in PRODUCTS:
            raise ValueError(f"product must be 'spot' or 'perp', got {self.product}")
        if self.side is not None and self.side not in SIDES:
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side}")
        if self.leverage is not None and self.leverage < 1:
            raise ValueError(f"leverage must be >= 1, got {self.leverage}")

    def resolve_product(self, default: str) -> str:
        return self.product if self.product is not None else default

    def resolve_side(self, default: str) -> str:
        return self.side if self.side is not None else default

    def resolve_leverage(self, default: int) -> int:
        return self.leverage if self.leverage is not None else default


_EMPTY_LEG_CONFIG = LegConfig()


@dataclass
class Intent:
    intent_id: str  # uuid7 or ulid
    base: str  # "BTC"
    quote_preference: list[str]  # ["USDT", "USDC"]
    product: Literal["spot", "perp"]
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    total_notional_usd: float  # e.g. 1000.00
    split: dict[str, float]  # {"binance": 0.5, "hyperliquid": 0.5}
    leverage: int = 1
    limit_price: float | None = None
    max_slippage_pct: float | None = None
    max_fee_usd: float | None = None
    max_funding_rate_pct: float | None = None
    execute_timeout_seconds: int = 30
    created_at: str = ""  # ISO 8601, set by Orchestrator on submission
    leg_configs: dict[str, LegConfig] = field(default_factory=dict)

    def __post_init__(self):
        total = sum(self.split.values())
        if not (0.999 <= total <= 1.001):
            raise ValueError(f"Split ratios must sum to 1.0, got {total}")
        if self.product not in PRODUCTS:
            raise ValueError(f"product must be 'spot' or 'perp', got {self.product}")
        if self.side not in SIDES:
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side}")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        # Validate leverage: Intent-level default (when no leg override exists)
        if self.product == "spot" and self.leverage != 1:
            raise ValueError("leverage must be 1 for spot orders")
        # Per-leg validation: spot legs must have leverage 1
        for venue, lc in self.leg_configs.items():
            product = lc.resolve_product(self.product)
            leverage = lc.resolve_leverage(self.leverage)
            if product == "spot" and leverage != 1:
                raise ValueError(f"leverage must be 1 for spot leg on {venue} (product={product}, leverage={leverage})")

    def get_leg_config(self, venue: str) -> LegConfig:
        return self.leg_configs.get(venue, _EMPTY_LEG_CONFIG)
