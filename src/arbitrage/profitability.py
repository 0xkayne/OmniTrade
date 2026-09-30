"""Depth-aware cross-venue spread and net profitability calculations."""

from __future__ import annotations

import time
from typing import Any, Literal

from .models import ArbDirection, ArbPair, SpreadOpportunity
from .normalization import common_base_quantity


def estimate_vwap(quote: Any, quantity: float, side: Literal["buy", "sell"]) -> tuple[float, float, bool]:
    """Return ``(VWAP, notional, fully_filled)`` for a depth walk."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    fill = quote.estimate_fill(quantity, side)
    if fill.avg_price <= 0 or fill.filled_fully is False:
        return fill.avg_price, fill.avg_price * quantity, False
    return fill.avg_price, fill.avg_price * quantity, True


def _one_way(
    pair: ArbPair,
    quote_a: Any,
    quote_b: Any,
    quantity_a: float,
    direction: ArbDirection,
    *,
    fee_multiplier: float,
    latency_reserve_bps: float,
    funding_cost_bps: float,
    now: float,
    ttl_ms: float,
) -> SpreadOpportunity:
    if direction == "buy_a_sell_b":
        buy_quote, sell_quote = quote_a, quote_b
        buy_qty, sell_qty = quantity_a, common_base_quantity(pair, quantity_a)
        buy_venue, sell_venue = pair.venue_a, pair.venue_b
    else:
        buy_quote, sell_quote = quote_b, quote_a
        buy_qty, sell_qty = common_base_quantity(pair, quantity_a), quantity_a
        buy_venue, sell_venue = pair.venue_b, pair.venue_a

    buy_vwap, buy_notional, buy_full = estimate_vwap(buy_quote, buy_qty, "buy")
    sell_vwap, sell_notional, sell_full = estimate_vwap(sell_quote, sell_qty, "sell")
    contract_notional = min(buy_notional, sell_notional)
    gross = (sell_vwap - buy_vwap) * min(buy_qty, sell_qty)
    fees = (buy_notional * buy_quote.taker_fee_rate + sell_notional * sell_quote.taker_fee_rate) * max(
        fee_multiplier, 0.0
    )
    mid = (buy_quote.mid_price + sell_quote.mid_price) / 2
    # The entry spread is already reflected in ``gross``.  Charge only depth
    # impact beyond the best executable level, avoiding double-counting half
    # the bid/ask spread as slippage.
    buy_impact = max(0.0, buy_vwap - buy_quote.ask_price) * buy_qty
    sell_impact = max(0.0, sell_quote.bid_price - sell_vwap) * sell_qty
    slippage = buy_impact + sell_impact
    latency = contract_notional * max(latency_reserve_bps, 0.0) / 10_000
    funding = contract_notional * max(funding_cost_bps, 0.0) / 10_000
    net = gross - fees - slippage - latency - funding
    edge_bps = net / contract_notional * 10_000 if contract_notional > 0 else 0.0
    return SpreadOpportunity(
        pair=pair,
        direction=direction,
        target_qty=quantity_a,
        buy_vwap=buy_vwap,
        sell_vwap=sell_vwap,
        gross_edge_usd=gross,
        fee_cost_usd=fees,
        slippage_cost_usd=slippage,
        latency_reserve_usd=latency,
        funding_cost_usd=funding,
        net_edge_usd=net,
        net_edge_bps=edge_bps,
        created_at=now,
        expires_at=now + max(ttl_ms, 0.0) / 1000,
        buy_venue=buy_venue,
        sell_venue=sell_venue,
        filled_fully=buy_full and sell_full and mid > 0,
        quote_age_a_ms=quote_a.age_ms,
        quote_age_b_ms=quote_b.age_ms,
        opportunity_id=(f"{pair.venue_a}:{pair.symbol_a}:{pair.venue_b}:{pair.symbol_b}:{direction}:{int(now * 1000)}"),
    )


def evaluate_spread(
    pair: ArbPair,
    quote_a: Any,
    quote_b: Any,
    quantity_a: float,
    *,
    fee_multiplier: float = 2.0,
    latency_reserve_bps: float = 0.0,
    funding_cost_bps: float = 0.0,
    now: float | None = None,
    ttl_ms: float = 500.0,
) -> SpreadOpportunity:
    """Evaluate both directions and return the better executable direction."""
    if quote_a.instrument.venue != pair.venue_a or quote_b.instrument.venue != pair.venue_b:
        raise ValueError("quotes do not match pair venues")
    timestamp = time.time() if now is None else now
    candidates = [
        _one_way(
            pair,
            quote_a,
            quote_b,
            quantity_a,
            direction,
            fee_multiplier=fee_multiplier,
            latency_reserve_bps=latency_reserve_bps,
            funding_cost_bps=funding_cost_bps,
            now=timestamp,
            ttl_ms=ttl_ms,
        )
        for direction in ("buy_a_sell_b", "buy_b_sell_a")
    ]
    return max(candidates, key=lambda opportunity: opportunity.net_edge_usd)
