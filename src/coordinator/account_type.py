"""Helpers for selecting the exchange account used by a planned leg."""

from __future__ import annotations


def ccxt_account_type(market_type: str) -> str:
    """Map OmniTrade market_type to the ccxt account type parameter."""
    return "swap" if market_type == "perp" else market_type


def account_type_params(market_type: str) -> dict[str, str]:
    return {"type": ccxt_account_type(market_type)}
