"""
RiskValidator: pre-execution guardrails. Read-only except for rate-limit counter.

Checks (each independently configurable):
- max_notional_per_intent: rejects single intents above a USD cap
- daily_loss_limit: rejects if cumulative PnL today is below a negative threshold
- max_venue_exposure: rejects if any venue has too much filled-but-uncompensated notional
- rate_limit: rejects if too many intents are submitted within a sliding window
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .intent import Intent
    from .plan import Plan


@dataclass
class RiskResult:
    is_allowed: bool
    failures: list[str] = field(default_factory=list)


class RiskValidator:
    """Pre-execution risk checks. One instance per Orchestrator lifetime."""

    def __init__(self, store: Any, risk_config: dict[str, Any] | None = None):
        self._store = store
        cfg = risk_config or {}

        self._max_notional: float | None = cfg.get("max_notional_per_intent")
        self._daily_loss_limit: float | None = cfg.get("daily_loss_limit_usd")
        self._max_venue_exposure: float | None = cfg.get("max_venue_exposure_usd")

        rl = cfg.get("rate_limit", {}) or {}
        self._rate_max_orders: int | None = rl.get("max_orders")
        self._rate_window: float = float(rl.get("window_seconds", 60))
        self._order_timestamps: deque[float] = deque()

    async def check(self, intent: Intent, plan: Plan) -> RiskResult:
        failures: list[str] = []

        # 1. Max notional per intent
        if self._max_notional is not None and intent.total_notional_usd > self._max_notional:
            failures.append(
                f"notional ${intent.total_notional_usd:,.2f} exceeds max ${self._max_notional:,.2f} per intent"
            )

        # 2. Daily loss limit
        if self._daily_loss_limit is not None:
            daily_pnl = await self._store.get_daily_pnl()
            if daily_pnl is not None and daily_pnl < -self._daily_loss_limit:
                failures.append(
                    f"daily PnL ${daily_pnl:,.2f} exceeds loss limit -${self._daily_loss_limit:,.2f}"
                )

        # 3. Max venue exposure
        if self._max_venue_exposure is not None:
            for leg in plan.legs:
                venue_exposure = await self._store.get_venue_exposure(leg.venue)
                if venue_exposure is not None and venue_exposure > self._max_venue_exposure:
                    failures.append(
                        f"venue {leg.venue} exposure ${venue_exposure:,.2f} exceeds max ${self._max_venue_exposure:,.2f}"
                    )

        # 4. Rate limiting (in-memory sliding window)
        if self._rate_max_orders is not None:
            now = time.time()
            cutoff = now - self._rate_window
            while self._order_timestamps and self._order_timestamps[0] < cutoff:
                self._order_timestamps.popleft()
            if len(self._order_timestamps) >= self._rate_max_orders:
                failures.append(
                    f"rate limit: {self._rate_max_orders} orders per {self._rate_window:.0f}s"
                )
            else:
                self._order_timestamps.append(now)

        return RiskResult(is_allowed=(len(failures) == 0), failures=failures)
