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
    contract_type: Literal["linear", "inverse"] | None = None
    settlement_asset: str | None = None

    def __post_init__(self):
        if self.product is not None and self.product not in PRODUCTS:
            raise ValueError(f"product must be 'spot' or 'perp', got {self.product}")
        if self.side is not None and self.side not in SIDES:
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side}")
        if self.leverage is not None and self.leverage < 1:
            raise ValueError(f"leverage must be >= 1, got {self.leverage}")
        if self.contract_type not in (None, "linear", "inverse"):
            raise ValueError("contract_type must be linear or inverse")
        if self.settlement_asset is not None and not self.settlement_asset.strip():
            raise ValueError("settlement_asset must not be empty")

    def resolve_product(self, default: str) -> str:
        return self.product if self.product is not None else default

    def resolve_side(self, default: str) -> str:
        return self.side if self.side is not None else default

    def resolve_leverage(self, default: int) -> int:
        return self.leverage if self.leverage is not None else default

    def resolve_contract_type(self, default: str | None) -> str:
        return self.contract_type or default or "linear"

    def resolve_settlement_asset(self, default: str | None) -> str | None:
        return self.settlement_asset or default


_EMPTY_LEG_CONFIG = LegConfig()


@dataclass
class Intent:
    intent_id: str  # uuid7 or ulid
    base: str  # "BTC"
    quote_preference: list[str]  # ["USDT", "USDC"]
    product: Literal["spot", "perp"]
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    total_notional_usd: float | None  # Required except when closing the entire position.
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
    contract_type: Literal["linear", "inverse"] | None = None
    settlement_asset: str | None = None
    position_effect: Literal["open", "close"] = "open"
    close_all: bool = False
    quantity_native: float | None = None

    def __post_init__(self):
        if not self.intent_id:
            self.intent_id = str(uuid.uuid4())
        if not self.split or any(not isfinite(v) or v <= 0 for v in self.split.values()):
            raise ValueError("positive finite split ratios are required")
        if self.contract_type not in (None, "linear", "inverse"):
            raise ValueError("contract_type must be linear or inverse")
        if self.settlement_asset is not None and not self.settlement_asset.strip():
            raise ValueError("settlement_asset must not be empty")
        if self.position_effect not in ("open", "close"):
            raise ValueError("position_effect must be open or close")
        if self.close_all and (self.position_effect != "close" or len(self.split) != 1):
            raise ValueError("close_all requires a single perpetual close leg")
        if self.close_all and self.quantity_native is not None:
            raise ValueError("close_all and quantity_native are mutually exclusive")
        if self.quantity_native is not None and (
            len(self.split) != 1 or not isfinite(self.quantity_native) or self.quantity_native <= 0
        ):
            raise ValueError("quantity_native requires one leg and a positive finite quantity")
        if self.total_notional_usd is None and not self.close_all:
            raise ValueError("total_notional_usd is required except with close_all")
        if self.total_notional_usd is not None and (
            not isfinite(self.total_notional_usd) or self.total_notional_usd <= 0
        ):
            raise ValueError("total_notional_usd must be positive and finite")
        if self.position_effect == "close" and self.min_fill_ratio != 1:
            raise ValueError("close requests require min_fill_ratio=1")
        for name in ("max_quote_age_ms", "execute_timeout_seconds", "reconcile_timeout_seconds"):
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
            if venue not in self.split:
                raise ValueError(f"leg override references unknown venue {venue}")
            if isinstance(lc, dict):
                lc = LegConfig(**lc)
                self.leg_configs[venue] = lc
            product = lc.resolve_product(self.product)
            leverage = lc.resolve_leverage(self.leverage)
            if product == "spot" and leverage != 1:
                raise ValueError(f"leverage must be 1 for spot leg on {venue} (product={product}, leverage={leverage})")
        for venue in self.split:
            lc = self.get_leg_config(venue)
            if lc.resolve_product(self.product) == "spot":
                if self.position_effect == "close":
                    raise ValueError("close intents may only contain perpetual legs")
                if lc.contract_type is not None or lc.settlement_asset is not None:
                    raise ValueError(f"spot leg on {venue} cannot select contract settlement")
                if self.product == "spot" and (self.contract_type is not None or self.settlement_asset is not None):
                    raise ValueError("spot intents cannot select contract settlement")

    def get_leg_config(self, venue: str) -> LegConfig:
        return self.leg_configs.get(venue, _EMPTY_LEG_CONFIG)
