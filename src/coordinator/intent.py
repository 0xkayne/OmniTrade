import uuid
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

PRODUCTS = ("spot", "perp")
SIDES = ("buy", "sell")
TIME_IN_FORCE_VALUES = ("GTC", "IOC", "FOK")


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
    time_in_force: Literal["GTC", "IOC", "FOK"] | None = None
    created_at: str = ""  # ISO 8601, set by Orchestrator on submission
    leg_configs: dict[str, LegConfig] = field(default_factory=dict)
    max_spread_pct: float | None = None
    max_quote_age_ms: float = 1000.0
    max_total_cost_usd: float | None = None
    max_order_notional_usd: float | None = None
    min_fill_ratio: float = 1.0
    compensation_slippage_pct: float = 0.5
    reconcile_timeout_seconds: float = 10.0

    def __post_init__(self):
        if not self.intent_id:
            self.intent_id = str(uuid.uuid4())
        if not self.split or any(not isfinite(v) or v <= 0 for v in self.split.values()):
            raise ValueError("positive finite split ratios are required")
        for name in ("total_notional_usd", "max_quote_age_ms", "execute_timeout_seconds", "reconcile_timeout_seconds"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name in (
            "limit_price",
            "max_slippage_pct",
            "max_spread_pct",
            "max_fee_usd",
            "max_total_cost_usd",
            "max_order_notional_usd",
            "compensation_slippage_pct",
            "max_funding_rate_pct",
        ):
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError(f"{name} must be nonnegative and finite")
        if self.limit_price == 0 or self.max_order_notional_usd == 0:
            raise ValueError("limit_price and max_order_notional_usd must be positive")
        if not 0 < self.min_fill_ratio <= 1:
            raise ValueError("min_fill_ratio must be in (0, 1]")
        if self.order_type not in ("market", "limit"):
            raise ValueError("order_type must be market or limit")
        if self.leverage < 1:
            raise ValueError("leverage must be >= 1")
        total = sum(self.split.values())
        if not (0.999 <= total <= 1.001):
            raise ValueError(f"Split ratios must sum to 1.0, got {total}")
        if self.product not in PRODUCTS:
            raise ValueError(f"product must be 'spot' or 'perp', got {self.product}")
        if self.side not in SIDES:
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side}")
        if self.time_in_force is not None:
            normalized_tif = self.time_in_force.upper()
            if normalized_tif not in TIME_IN_FORCE_VALUES:
                raise ValueError(
                    f"time_in_force must be one of {', '.join(TIME_IN_FORCE_VALUES)}, got {self.time_in_force}"
                )
            self.time_in_force = normalized_tif
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        # Validate leverage: Intent-level default (when no leg override exists)
        if self.product == "spot" and self.leverage != 1:
            raise ValueError("leverage must be 1 for spot orders")
        # Per-leg validation: spot legs must have leverage 1
        for venue, lc in self.leg_configs.items():
            if isinstance(lc, dict):
                lc = LegConfig(**lc)
                self.leg_configs[venue] = lc
            product = lc.resolve_product(self.product)
            leverage = lc.resolve_leverage(self.leverage)
            if product == "spot" and leverage != 1:
                raise ValueError(f"leverage must be 1 for spot leg on {venue} (product={product}, leverage={leverage})")

    def get_leg_config(self, venue: str) -> LegConfig:
        return self.leg_configs.get(venue, _EMPTY_LEG_CONFIG)
