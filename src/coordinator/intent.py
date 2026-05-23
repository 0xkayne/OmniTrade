from dataclasses import dataclass
from typing import Literal


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

    def __post_init__(self):
        total = sum(self.split.values())
        if not (0.999 <= total <= 1.001):
            raise ValueError(f"Split ratios must sum to 1.0, got {total}")
        if self.product not in ("spot", "perp"):
            raise ValueError(f"product must be 'spot' or 'perp', got {self.product}")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        if self.product == "spot" and self.leverage != 1:
            raise ValueError("leverage must be 1 for spot orders")
