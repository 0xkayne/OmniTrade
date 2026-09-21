"""Pure pre-flight checks for cross-venue arbitrage opportunities."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .models import SpreadOpportunity


@dataclass(frozen=True)
class ArbitrageRiskConfig:
    """Safety limits; defaults are conservative and paper-trading friendly."""

    enabled: bool = False
    dry_run: bool = True
    min_net_edge_bps: float = 15.0
    max_quote_age_ms: float = 500.0
    max_unhedged_ms: float = 1_500.0
    max_unhedged_usd: float = 100.0
    max_cycle_notional_usd: float = 1_000.0
    max_open_cycles: int = 1
    max_slippage_bps: float = 8.0
    daily_loss_limit_usd: float = 100.0

    @classmethod
    def from_mapping(cls, values: dict | None) -> ArbitrageRiskConfig:
        values = values or {}
        return cls(
            **{field_name: values[field_name] for field_name in cls.__dataclass_fields__ if field_name in values}
        )


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    failures: list[str] = field(default_factory=list)


class ArbitrageRiskValidator:
    """Evaluate an opportunity without network or persistence side effects."""

    def __init__(self, config: ArbitrageRiskConfig | None = None):
        self.config = config or ArbitrageRiskConfig()

    def check(
        self,
        opportunity: SpreadOpportunity,
        *,
        open_cycles: int = 0,
        unhedged_usd: float = 0.0,
        unhedged_ms: float = 0.0,
        daily_pnl_usd: float = 0.0,
        now: float | None = None,
    ) -> RiskDecision:
        cfg = self.config
        failures: list[str] = []
        timestamp = time.time() if now is None else now
        notional = abs(opportunity.target_qty * opportunity.buy_vwap)
        if not cfg.enabled:
            failures.append("arbitrage is disabled")
        if opportunity.expires_at < timestamp:
            failures.append("opportunity quote has expired")
        if max(opportunity.quote_age_a_ms, opportunity.quote_age_b_ms) > cfg.max_quote_age_ms:
            failures.append("quote age exceeds limit")
        if not opportunity.filled_fully:
            failures.append("orderbook depth is insufficient")
        if opportunity.net_edge_bps < cfg.min_net_edge_bps:
            failures.append(f"net edge {opportunity.net_edge_bps:.3f} bps below minimum {cfg.min_net_edge_bps:.3f} bps")
        if opportunity.slippage_cost_usd / notional * 10_000 > cfg.max_slippage_bps if notional else True:
            failures.append("estimated slippage exceeds limit")
        if notional > cfg.max_cycle_notional_usd:
            failures.append("cycle notional exceeds limit")
        if open_cycles >= cfg.max_open_cycles:
            failures.append("maximum open arbitrage cycles reached")
        if unhedged_usd > cfg.max_unhedged_usd or unhedged_ms > cfg.max_unhedged_ms:
            failures.append("unhedged exposure exceeds limit")
        if daily_pnl_usd < -abs(cfg.daily_loss_limit_usd):
            failures.append("daily loss limit reached")
        return RiskDecision(allowed=not failures, failures=failures)
