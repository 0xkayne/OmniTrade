"""Small, deterministic helpers for matching venue-native market contracts."""

from __future__ import annotations

import re
from typing import Any

from .models import ArbPair, QuoteSnapshot


def normalize_symbol(symbol: str) -> tuple[str, str]:
    """Return ``(base, quote)`` for common spot/perpetual symbols.

    This helper is intentionally conservative; callers should prefer explicit
    ``Instrument`` metadata when a venue uses a non-standard contract symbol.
    """
    value = symbol.upper().strip().replace("-", "/").replace("_", "/")
    value = re.sub(r":(?:USDT|USDC|USD)$", "", value)
    if "/" in value:
        base, quote = value.split("/", 1)
        return base, quote.split(":", 1)[0]
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)], quote
    raise ValueError(f"cannot infer base/quote from symbol {symbol!r}")


def quote_snapshot(quote: Any) -> QuoteSnapshot:
    """Convert an existing market ``Quote`` into the arbitrage contract."""
    return QuoteSnapshot(
        quote=quote,
        fetched_at=quote.fetched_at,
        exchange_at=quote.exchange_at,
        sequence=quote.sequence_id,
        source=quote.source,
    )


def pair_from_instruments(instrument_a: Any, instrument_b: Any) -> ArbPair:
    """Build a pair and preserve the complete source instrument metadata."""
    if instrument_a.base.symbol != instrument_b.base.symbol:
        raise ValueError("instruments must have the same base asset")
    if instrument_a.market_type != instrument_b.market_type:
        raise ValueError("instruments must have the same market type")
    return ArbPair(
        base=instrument_a.base.symbol,
        market_type=instrument_a.market_type,
        venue_a=instrument_a.venue,
        symbol_a=instrument_a.venue_symbol,
        venue_b=instrument_b.venue,
        symbol_b=instrument_b.venue_symbol,
        contract_size_a=instrument_a.contract_size,
        contract_size_b=instrument_b.contract_size,
        instrument_a=instrument_a,
        instrument_b=instrument_b,
    )


def common_base_quantity(pair: ArbPair, quantity_a: float) -> float:
    """Convert venue A contracts/base units into venue B base quantity."""
    if quantity_a < 0:
        raise ValueError("quantity must be non-negative")
    return quantity_a * pair.contract_size_a / pair.contract_size_b * pair.hedge_ratio
