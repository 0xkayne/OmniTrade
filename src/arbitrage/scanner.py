"""Cross-venue opportunity scanner.

The scanner only reads market data.  It deliberately has no order side effects;
execution belongs to a separate coordinator after a risk decision.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .normalization import pair_from_instruments
from .profitability import evaluate_spread


@dataclass(frozen=True)
class ScannerConfig:
    quantity_base: float = 0.001
    depth: int = 20
    quote_ttl_ms: float = 500.0
    latency_reserve_bps: float = 2.0
    funding_cost_bps: float = 0.0
    fee_multiplier: float = 2.0

    @classmethod
    def from_mapping(cls, values: dict | None) -> ScannerConfig:
        values = values or {}
        fields = cls.__dataclass_fields__
        return cls(**{name: values[name] for name in fields if name in values})


class CrossVenueArbitrageScanner:
    """Fetch quotes concurrently and return cost-adjusted opportunities."""

    def __init__(self, quote_fetcher, config: ScannerConfig | None = None):
        self._quote_fetcher = quote_fetcher
        self._config = config or ScannerConfig()

    async def scan_pair(self, pair):
        arb_pair = pair_from_instruments(pair.instrument_a, pair.instrument_b)
        quote_a, quote_b = await asyncio.gather(
            self._quote_fetcher.fetch(pair.instrument_a, depth=self._config.depth),
            self._quote_fetcher.fetch(pair.instrument_b, depth=self._config.depth),
        )
        return evaluate_spread(
            arb_pair,
            quote_a,
            quote_b,
            self._config.quantity_base,
            fee_multiplier=self._config.fee_multiplier,
            latency_reserve_bps=self._config.latency_reserve_bps,
            funding_cost_bps=self._config.funding_cost_bps,
            ttl_ms=self._config.quote_ttl_ms,
        )

    async def scan(self, pairs: list):
        """Scan all configured pairs and sort by net edge descending."""
        opportunities = await asyncio.gather(*(self.scan_pair(pair) for pair in pairs))
        return sorted(opportunities, key=lambda item: item.net_edge_usd, reverse=True)
